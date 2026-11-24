"""O2 观测数据层：把 spans.jsonl / events.jsonl / state.json 聚合成三视图要的纯数据。

原则与整页一致——界面是数据的投影，这里只做聚合，不猜缺失字段：
- 有 spans 就按 spans 算耗时，没有就退到事件时间戳，并在数据里标 `source`；
- 有 task.dispatched/completed 事件就画实测段，没有就画排期段并标 `basis=plan`；
- 闸门结论优先用 detail.action，缺失时按「后续阶段是否真的进入过」推断，并标 `inferred`。

三视图（作业3 O2）对应三个函数：
  health_strip()   健康条：每阶段一盏灯 + 耗时 + 闸门 + 告警数
  card_waterfall() 单卡瀑布：单张任务卡的等待 / 执行 / 顺延 / 实测
  alerts()         告警列表：每条都带证据事件号，双击可钻到原始事件
plus 命令流水与父/子运行对比，支撑「暂停 / 批注 / 单卡重试」三个动作的验收。
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import median
from typing import Any

from .events import load_events

# 闸门"通过类"结论：只有这些算绿灯
GATE_PASS_ACTIONS = ("advance", "freeze", "pass", "pass_next")
GATE_FAIL_ACTIONS = ("rework", "wait_human", "stay", "escalate", "block", "fail")
STAGE_ORDER = ("P1", "P2", "P3", "P4", "P5", "P6", "P7")

LEVEL_ORDER = {"high": 0, "mid": 1, "low": 2}
LEVEL_LABEL = {"high": "高", "mid": "中", "low": "提示"}


# ---------------------------------------------------------------- 读原始数据

def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def load_run(run_dir: str | Path) -> dict[str, Any]:
    """读一次运行的观测数据：事件（带 seq）、spans、state、产物。"""
    directory = Path(run_dir)
    if not directory.exists():
        raise FileNotFoundError(f"运行目录不存在：{directory}")

    events_path = directory / "events.jsonl"
    events = load_events(events_path) if events_path.exists() else []

    spans = _read_jsonl(directory / "spans.jsonl")
    state_path = directory / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}

    artifacts: dict[str, Any] = {}
    art_dir = directory / "artifacts"
    if art_dir.exists():
        for path in sorted(art_dir.glob("*.json")):
            try:
                artifacts[path.stem] = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue

    return {
        "run_id": str(state.get("run_id") or directory.name),
        "run_dir": directory,
        "events": events,
        "spans": spans,
        "state": state,
        "artifacts": artifacts,
        "fork": None,
    }


def attach_fork(data: dict[str, Any], fork_dir: str | Path | None = None) -> dict[str, Any]:
    """把子运行（<run_id>.fork.<n>）挂进主数据，供并排对比；找不到就置 None。"""
    base = Path(data["run_dir"])
    candidates = [Path(fork_dir)] if fork_dir else sorted(base.parent.glob(f"{data['run_id']}.fork.*"))
    for cand in candidates:
        if cand.exists():
            data["fork"] = load_run(cand)
            break
    return data


def artifact_data(data: dict[str, Any], *names: str) -> dict[str, Any]:
    """按名字取产物 data 段；A 与 E 的产物名并存时依次尝试。"""
    for name in names:
        envelope = data["artifacts"].get(name)
        if isinstance(envelope, dict):
            inner = envelope.get("data")
            return inner if isinstance(inner, dict) else envelope
    return {}


# ---------------------------------------------------------------- 公共小工具

def _event_evidence(event) -> str:
    return str(event.event_id or f"EVT-{event.seq:05d}")


def _detail(event) -> dict[str, Any]:
    """A 的事件把附加字段放 detail；E 的 demo 事件是平铺 payload。两种都收。"""
    payload = getattr(event, "payload", None) or {}
    inner = payload.get("detail")
    if isinstance(inner, dict):
        merged = dict(inner)
        for key, value in payload.items():
            if key != "detail":
                merged.setdefault(key, value)
        return merged
    return dict(payload)


def _gate_of(event) -> tuple[str, str]:
    """闸门号与结论：优先 detail，其次 payload_ref（mock 路径只有 ref）。"""
    detail = _detail(event)
    gate_id = str(detail.get("gate_id") or event.payload_ref or "")
    action = str(detail.get("action") or detail.get("verdict") or "").lower()
    return gate_id, action


def _stage_of_gate(gate_id: str) -> str:
    """G_P1_P2 → P1；G_P7_BASELINE → P7。"""
    parts = [p for p in gate_id.split("_") if p]
    for part in parts:
        if part in STAGE_ORDER:
            return part
    return ""


def _gate_verdicts(events: list[Any]) -> dict[str, dict[str, Any]]:
    """闸门结论表；没有 action 的按「后续阶段进入过没有」推断，并记 inferred=True。"""
    entered: dict[str, int] = {}
    for event in events:
        if event.event_type == "stage.enter":
            stage = str(event.stage or "")
            entered[stage] = entered.get(stage, 0) + 1

    verdicts: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.event_type != "gate.evaluated":
            continue
        gate_id, action = _gate_of(event)
        if not gate_id:
            continue
        stage = _stage_of_gate(gate_id)
        inferred = False
        if not action:
            nxt = STAGE_ORDER[STAGE_ORDER.index(stage) + 1] if stage in STAGE_ORDER[:-1] else ""
            if nxt and entered.get(nxt):
                action = "advance"
            elif stage == "P7":
                action = "freeze"
            else:
                # 后续阶段没进去过：闸门没放行（等人工或重做），这是要被看见的状态
                action = "pending"
            inferred = True
        verdicts[gate_id] = {
            "gate_id": gate_id,
            "stage": stage,
            "action": action,
            "inferred": inferred,
            "pass": action in GATE_PASS_ACTIONS,
            "event_id": _event_evidence(event),
            "seq": event.seq,
            "reason": str(_detail(event).get("reason") or ""),
        }
    return verdicts


def _stage_durations(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """阶段耗时：优先 stage span 的 duration_ms，退到事件时间戳差。"""
    out: dict[str, dict[str, Any]] = {}
    for span in data["spans"]:
        if span.get("kind") != "stage":
            continue
        stage = str(span.get("stage") or "")
        duration = span.get("duration_ms")
        if stage and isinstance(duration, (int, float)):
            prev = out.get(stage)
            if prev is None or float(duration) > float(prev["duration_ms"]):
                out[stage] = {
                    "stage": stage,
                    "duration_ms": float(duration),
                    "status": str(span.get("status") or "ok"),
                    "span_id": str(span.get("span_id") or ""),
                    "source": "span",
                    "start_ts": str(span.get("start_ts") or ""),
                    "end_ts": str(span.get("end_ts") or ""),
                }
    return out


def _budget_peak(data: dict[str, Any]) -> tuple[int, int, str]:
    """预算峰值与上限；取 budget.charged 的最大 global_used。"""
    used, limit, evidence = 0, 0, ""
    for event in data["events"]:
        if event.event_type != "budget.charged":
            continue
        payload = getattr(event, "payload", None) or {}
        try:
            used = max(used, int(payload.get("global_used") or event.budget_used or 0))
        except (TypeError, ValueError):
            pass
        try:
            limit = max(limit, int(payload.get("limit") or payload.get("global_limit") or 0))
        except (TypeError, ValueError):
            pass
        evidence = _event_evidence(event)
    if not limit:
        budget = (data["state"].get("ledger") or {}).get("budget") or {}
        limit = int((data["state"].get("ledger") or {}).get("global_limit") or 30)
        if int(budget.get("global_llm_calls") or 0):
            limit = int(budget["global_llm_calls"])
    return used, limit, evidence


def _count_by_stage(data: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in data["events"]:
        stage = str(event.stage or "")
        counts[stage] = counts.get(stage, 0) + 1
    return counts


def _stage_events(data: dict[str, Any], stage: str) -> list[str]:
    return [_event_evidence(e) for e in data["events"] if str(e.stage or "") == stage]


# ---------------------------------------------------------------- 视图一：健康条

def health_strip(data: dict[str, Any]) -> dict[str, Any]:
    """每阶段一盏灯：状态 / 耗时 / 闸门 / 告警数 / 证据事件。"""
    durations = _stage_durations(data)
    verdicts = _gate_verdicts(data["events"])
    counts = _count_by_stage(data)
    all_alerts = alerts(data)
    alerts_by_stage: dict[str, int] = {}
    for alert in all_alerts:
        stage = str(alert.get("stage") or "")
        if stage:
            alerts_by_stage[stage] = alerts_by_stage.get(stage, 0) + 1

    stages = [s for s in STAGE_ORDER if counts.get(s)]
    if not stages:
        stages = [s for s in STAGE_ORDER if durations.get(s)]

    rows: list[dict[str, Any]] = []
    for stage in stages:
        dur = durations.get(stage, {})
        gate = next((v for v in verdicts.values() if v["stage"] == stage), {})
        n_alerts = alerts_by_stage.get(stage, 0)
        status = str(dur.get("status") or "ok")
        if n_alerts and status == "ok":
            status = "warn"
        rows.append(
            {
                "stage": stage,
                "status": status,
                "duration_ms": dur.get("duration_ms"),
                "source": dur.get("source", "span" if dur else "event"),
                "gate_id": gate.get("gate_id", ""),
                "gate_action": gate.get("action", ""),
                "gate_pass": bool(gate.get("pass")),
                "gate_inferred": bool(gate.get("inferred")),
                "events": counts.get(stage, 0),
                "alerts": n_alerts,
                "evidence": [gate.get("event_id")] if gate.get("event_id") else _stage_events(data, stage)[:4],
            }
        )

    total_ms = sum(r["duration_ms"] or 0 for r in rows)
    used, limit, budget_ev = _budget_peak(data)
    bad = sum(1 for r in rows if r["status"] not in ("ok",))
    score = max(0, 100 - bad * 12 - len(all_alerts) * 3)
    return {
        "rows": rows,
        "total_ms": round(total_ms, 2),
        "budget_used": used,
        "budget_limit": limit,
        "budget_evidence": budget_ev,
        "alert_total": len(all_alerts),
        "score": score,
        "status": str(data["state"].get("status") or "running"),
        "phase": str(data["state"].get("phase") or ""),
    }


# ---------------------------------------------------------------- 视图二：单卡瀑布

def _tasks_of(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """任务卡来源：优先 P7 的 schedule（带波次/起止日），没跑到 P7 就退到 P6 的 dev_task_list。"""
    schedule = artifact_data(data, "schedule")
    tasks = [t for t in (schedule.get("tasks") or []) if isinstance(t, dict)]
    waves = [w for w in (schedule.get("waves") or []) if isinstance(w, dict)]
    if not tasks:
        cards = artifact_data(data, "dev_task_list")
        tasks = [t for t in (cards.get("tasks") or []) if isinstance(t, dict)]
        waves = []
    return tasks, waves


def card_ids(data: dict[str, Any]) -> list[str]:
    tasks, _ = _tasks_of(data)
    return [str(t.get("task_id")) for t in tasks if t.get("task_id")]


def _task_events(data: dict[str, Any], task_id: str) -> dict[str, int]:
    """实测段：task.dispatched / task.completed 里点名该卡的事件序号。"""
    found: dict[str, int] = {}
    for event in data["events"]:
        if event.event_type not in ("task.dispatched", "task.completed"):
            continue
        detail = _detail(event)
        target = str(detail.get("task_id") or event.payload_ref or "")
        if target == task_id:
            found[event.event_type] = event.seq
    return found


def card_waterfall(data: dict[str, Any], task_id: str) -> dict[str, Any]:
    """单卡的耗时分解：等待（依赖/并行度） + 执行 + 尾巴，外加实测段（若有）。"""
    tasks, waves = _tasks_of(data)
    by_id = {str(t.get("task_id")): t for t in tasks}
    task = by_id.get(task_id) or (tasks[0] if tasks else {})
    if not task:
        return {"task_id": task_id, "empty": True, "segments": [], "cards": []}

    task_id = str(task.get("task_id") or task_id)
    wave_map: dict[str, dict[str, Any]] = {}
    for wave in waves:
        for tid in wave.get("task_ids") or []:
            wave_map[str(tid)] = wave

    estimate = float(task.get("estimate_days") or 0)

    dep_ends = []
    for dep in task.get("depends_on") or []:
        dep_wave = wave_map.get(str(dep), {})
        if dep_wave.get("end_day") is not None:
            dep_ends.append(float(dep_wave["end_day"]))

    has_schedule = bool(waves)
    if has_schedule:
        start = float(wave_map.get(task_id, {}).get("start_day") or 0)
        end = float(wave_map.get(task_id, {}).get("end_day") or 0)
        if end <= start:
            end = start + estimate
        ready = max(dep_ends) if dep_ends else 1.0
    else:
        # 还没跑到 P7：只有估工日，没有波次与起止日，画不了等待段
        start, end, ready = 0.0, estimate, 0.0

    wait = max(0.0, start - ready)
    exec_days = estimate
    tail = max(0.0, end - start - estimate)

    deferred = next(
        (d for d in (artifact_data(data, "schedule").get("deferred") or []) if str(d.get("task_id")) == task_id),
        None,
    )

    segments = [
        {"key": "wait", "label": "等待（依赖/并行度）", "days": round(wait, 2), "color": "#adb5bd"},
        {"key": "exec", "label": "执行（排期估算）", "days": round(exec_days, 2), "color": "#4c6ef5"},
    ]
    if tail:
        segments.append({"key": "tail", "label": "收尾（验收/缓冲）", "days": round(tail, 2), "color": "#74c0fc"})
    if deferred:
        segments.append(
            {
                "key": "deferred",
                "label": "顺延（" + str(deferred.get("reason") or "资源冲突") + "）",
                "days": round(float(deferred.get("days") or 0), 2) or 0,
                "color": "#f08c00",
            }
        )

    observed = _task_events(data, task_id)
    cards = []
    for other in tasks:
        cid = str(other.get("task_id"))
        ev = _task_events(data, cid)
        cards.append(
            {
                "task_id": cid,
                "title": str(other.get("title") or ""),
                "owner": str(other.get("owner_role") or ""),
                "estimate": float(other.get("estimate_days") or 0),
                "wave": int(wave_map.get(cid, {}).get("wave") or 0),
                "start": float(wave_map.get(cid, {}).get("start_day") or 0),
                "end": float(wave_map.get(cid, {}).get("end_day") or 0),
                "critical": cid in (artifact_data(data, "schedule").get("critical_tasks") or []),
                "retried": cid == str(
                    (data["state"].get("pending") or {}).get("retry_task")
                    or next(
                        (c.get("target") for c in (data["state"].get("commands") or []) if c.get("action") == "retry_task"),
                        "",
                    )
                ),
                "evidence": list(ev.values()),
            }
        )

    return {
        "task_id": task_id,
        "title": str(task.get("title") or ""),
        "owner": str(task.get("owner_role") or ""),
        "module": str(task.get("module") or ""),
        "kind": str(task.get("kind") or ""),
        "start_day": start,
        "end_day": end,
        "ready_day": ready,
        "estimate": estimate,
        "segments": segments,
        "total_days": round(end - ready, 2),
        "observed": observed,
        "basis": "measured" if observed else "plan",
        "has_schedule": has_schedule,
        "note": "" if has_schedule else "该运行还没到 P7，没有 schedule.json：瀑布只按 dev_task_list 的估工日画，等待与顺延段缺失",
        "acceptance": [str(a) for a in (task.get("acceptance") or [])],
        "depends_on": [str(d) for d in (task.get("depends_on") or [])],
        "cards": cards,
    }


# ---------------------------------------------------------------- 视图三：告警列表

def _alert(
    level: str,
    code: str,
    title: str,
    detail: str,
    stage: str = "",
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "level": level,
        "level_label": LEVEL_LABEL.get(level, level),
        "code": code,
        "title": title,
        "detail": detail,
        "stage": stage,
        "evidence": [e for e in (evidence or []) if e],
    }


def alerts(data: dict[str, Any]) -> list[dict[str, Any]]:
    """告警=判据命中。每条都带证据事件号，界面上双击能钻到原始事件。"""
    out: list[dict[str, Any]] = []
    verdicts = _gate_verdicts(data["events"])

    # 1) 闸门没放行
    for gate in verdicts.values():
        action = str(gate.get("action") or "")
        if action and action not in GATE_PASS_ACTIONS:
            if action == "pending":
                title = f"闸门 {gate['gate_id']} 未放行"
                detail = f"阶段 {gate.get('stage') or '—'} 的后续阶段没有进入过（结论由事件顺序推断）"
            else:
                title = f"闸门 {gate['gate_id']} 结论 {action}"
                detail = f"阶段 {gate.get('stage') or '—'} 未放行" + ("（结论由后续阶段是否进入推断）" if gate.get("inferred") else "")
            out.append(
                _alert(
                    "high" if action in ("block", "fail", "escalate") else "mid",
                    "GATE_NOT_PASS",
                    title,
                    detail,
                    stage=str(gate.get("stage") or ""),
                    evidence=[str(gate.get("event_id") or "")],
                )
            )

    # 2) span 报错 / 中断
    for span in data["spans"]:
        status = str(span.get("status") or "ok")
        if status in ("ok", "running"):
            continue
        out.append(
            _alert(
                "high",
                "SPAN_FAILED",
                f"span {span.get('span_id')} {span.get('kind')}:{span.get('stage')} 状态 {status}",
                str((span.get("detail") or {}).get("error") or "span 未正常收尾"),
                stage=str(span.get("stage") or ""),
                evidence=[str(span.get("span_id") or "")],
            )
        )

    # 3) 阶段耗时离群（> 2×中位数）
    durations = [v["duration_ms"] for v in _stage_durations(data).values() if v.get("duration_ms")]
    if len(durations) >= 3:
        mid = median(durations)
        if mid > 0:
            for stage, info in _stage_durations(data).items():
                if float(info.get("duration_ms") or 0) > 2 * mid:
                    out.append(
                        _alert(
                            "mid",
                            "STAGE_SLOW",
                            f"{stage} 耗时 {info['duration_ms']:.1f} ms，超过中位数 {mid:.1f} ms 的 2 倍",
                            "可能是模型重试或闸门反复判定",
                            stage=stage,
                            evidence=list(_stage_events(data, stage)[:3]),
                        )
                    )

    # 4) 预算吃紧
    used, limit, ev = _budget_peak(data)
    if limit:
        ratio = used / limit
        if ratio >= 0.8:
            out.append(
                _alert(
                    "high" if used >= limit else "mid",
                    "BUDGET_PRESSURE",
                    f"模型调用预算 {used}/{limit}（{ratio * 100:.0f}%）",
                    "预算耗尽后闸门会转人工，fork 会继承已花预算",
                    evidence=[ev],
                )
            )

    # 5) 覆盖空缺
    coverage = artifact_data(data, "task_coverage", "coverage_matrix")
    gap = int(coverage.get("gap_count") or 0)
    if gap:
        out.append(
            _alert(
                "mid",
                "COVERAGE_GAP",
                f"覆盖矩阵有空缺 {gap} 处",
                f"覆盖率 {coverage.get('coverage_rate') or 0:.0%}，空缺要回填到任务卡",
                stage="P6",
            )
        )

    # 6) 任务顺延 / 资源冲突
    schedule = artifact_data(data, "schedule")
    for item in schedule.get("deferred") or []:
        out.append(
            _alert(
                "low",
                "TASK_DEFERRED",
                f"任务 {item.get('task_id')} 顺延到第 {item.get('wave_no')} 波",
                str(item.get("reason") or "并行度上限"),
                stage="P7",
            )
        )
    for conflict in schedule.get("resource_conflicts") or []:
        out.append(
            _alert(
                "low",
                "RESOURCE_CONFLICT",
                f"资源冲突：{conflict.get('task_id') or conflict.get('resource') or ''}",
                str(conflict.get("reason") or conflict.get("detail") or ""),
                stage="P7",
            )
        )

    # 7) 发言趋同（护栏信号）
    for event in data["events"]:
        if event.event_type != "guard.signal":
            continue
        detail = _detail(event)
        out.append(
            _alert(
                "mid",
                "GUARD_SIMILARITY",
                "发言趋同护栏命中",
                f"agent {detail.get('agent_id') or ''} 相似度 {detail.get('similarity') or detail.get('value') or '—'}",
                stage=str(event.stage or ""),
                evidence=[_event_evidence(event)],
            )
        )

    # 8) 命令 issued 但没 applied
    pairs = _command_pairs(data)
    for cmd in pairs:
        if not cmd.get("applied_event"):
            out.append(
                _alert(
                    "high",
                    "COMMAND_UNPAIRED",
                    f"命令 {cmd['command_id']} 只有 issued，没有 applied",
                    f"动作 {cmd.get('action')} 未落地，状态机不会看到效果",
                    evidence=[cmd.get("issued_event") or ""],
                )
            )

    # 9) 子运行提前结束 / 继承预算
    fork = data.get("fork")
    if fork:
        status = str(fork["state"].get("status") or "")
        if status in ("escalated", "failed", "terminated"):
            fork_ev = next((e for e in fork["events"] if e.event_type == "fork.created"), None)
            out.append(
                _alert(
                    "mid",
                    "FORK_BLOCKED",
                    f"fork {fork['run_id']} 停在 {fork['state'].get('phase')}（{status}）",
                    str((fork["state"].get("pending") or {}).get("reason") or "fork 未跑完，需人工处理"),
                    evidence=[_event_evidence(fork_ev)] if fork_ev else [],
                )
            )

    out.sort(key=lambda a: LEVEL_ORDER.get(str(a.get("level")), 9))
    return out


# ---------------------------------------------------------------- 命令流水

def _command_pairs(data: dict[str, Any]) -> list[dict[str, Any]]:
    """把 state.commands 与 command.issued / command.applied 事件按 command_id 对齐。"""
    issued: dict[str, str] = {}
    applied: dict[str, str] = {}
    fork_of: dict[str, str] = {}
    for event in data["events"]:
        detail = _detail(event)
        cid = str(detail.get("command_id") or "")
        if not cid:
            continue
        if event.event_type == "command.issued":
            issued[cid] = _event_evidence(event)
        elif event.event_type == "command.applied":
            applied[cid] = _event_evidence(event)
            effect = detail.get("effect") or {}
            if isinstance(effect, dict) and effect.get("fork_run_id"):
                fork_of[cid] = str(effect["fork_run_id"])

    rows: list[dict[str, Any]] = []
    for cmd in data["state"].get("commands") or []:
        cid = str(cmd.get("command_id") or "")
        rows.append(
            {
                **cmd,
                "issued_event": issued.get(cid, ""),
                "applied_event": applied.get(cid, ""),
                "effective_fork": fork_of.get(cid) or cmd.get("fork_run_id") or "",
            }
        )
    return rows


def command_log(data: dict[str, Any]) -> dict[str, Any]:
    pairs = _command_pairs(data)
    by_action: dict[str, int] = {}
    for row in pairs:
        by_action[str(row.get("action"))] = by_action.get(str(row.get("action")), 0) + 1
    return {
        "commands": pairs,
        "counts": by_action,
        "paired": all(r.get("issued_event") and r.get("applied_event") for r in pairs) if pairs else True,
    }


# ---------------------------------------------------------------- 父子并排对比

def _metrics(data: dict[str, Any]) -> dict[str, Any]:
    spans = data["spans"]
    verdicts = _gate_verdicts(data["events"])
    entered = {str(e.stage) for e in data["events"] if e.event_type == "stage.enter"}
    used, limit, _ = _budget_peak(data)
    return {
        "run_id": data["run_id"],
        "phase": str(data["state"].get("phase") or ""),
        "status": str(data["state"].get("status") or ""),
        "events": len(data["events"]),
        "spans": len(spans),
        "artifacts": len(data["artifacts"]),
        "stages": len(entered),
        "model_calls": sum(1 for e in data["events"] if e.event_type == "model.call"),
        "budget_used": used,
        "budget_limit": limit,
        "command_events": sum(
            1 for e in data["events"] if e.event_type in ("command.issued", "command.applied", "fork.created")
        ),
        "gates_passed": sum(1 for v in verdicts.values() if v.get("pass")),
        "gates_total": len(verdicts),
    }


def compare_runs(data: dict[str, Any]) -> dict[str, Any] | None:
    fork = data.get("fork")
    if not fork:
        return None
    parent = _metrics(data)
    child = _metrics(fork)
    rows = []
    for key, label in (
        ("phase", "当前阶段"),
        ("status", "状态"),
        ("stages", "已进入阶段"),
        ("events", "事件条数"),
        ("spans", "span 条数"),
        ("artifacts", "产物份数"),
        ("gates_passed", "闸门通过"),
        ("model_calls", "model.call 条数"),
        ("budget_used", "预算已用"),
    ):
        rows.append({"label": label, "parent": parent[key], "fork": child[key]})

    # fork 的动机记在 fork.created 事件里（pending 在后续跑动时会被覆盖）
    retry_task = ""
    reason = str((fork["state"].get("pending") or {}).get("reason") or "")
    for event in fork["events"]:
        if event.event_type != "fork.created":
            continue
        detail = _detail(event)
        retry_task = str(detail.get("retry_task") or retry_task)
        reason = "fork 创建：" + (str(detail.get("command_id") or "") or reason or "单卡重试")
        break
    if not retry_task:
        retry_task = str(
            next(
                (
                    c.get("target")
                    for c in (data["state"].get("commands") or [])
                    if str(c.get("action")) == "retry_task"
                ),
                "",
            )
        )
    return {
        "parent": parent,
        "fork": child,
        "rows": rows,
        "retry_task": retry_task,
        "forked_from": str(fork["state"].get("parent_run_id") or ""),
        "reason": reason,
    }


# ---------------------------------------------------------------- 汇总入口

def overview(run_dir: str | Path, task_id: str | None = None) -> dict[str, Any]:
    """一次算齐三视图 + 命令面板要的全部数据。"""
    data = attach_fork(load_run(run_dir))
    strip = health_strip(data)
    ids = card_ids(data)
    retried = ""
    for cmd in data["state"].get("commands") or []:
        if str(cmd.get("action")) == "retry_task" and cmd.get("target"):
            retried = str(cmd["target"])
    picked = task_id if task_id in ids else (retried if retried in ids else (ids[0] if ids else ""))
    waterfalls = {tid: card_waterfall(data, tid) for tid in ids}
    return {
        "run_id": data["run_id"],
        "run_dir": str(Path(run_dir)),
        "health": strip,
        "alerts": alerts(data),
        "waterfall": waterfalls.get(picked, {"empty": True, "segments": [], "cards": []}),
        "waterfalls": waterfalls,
        "picked": picked,
        "command_log": command_log(data),
        "compare": compare_runs(data),
        "state": {
            "phase": str(data["state"].get("phase") or ""),
            "status": str(data["state"].get("status") or ""),
            "constraints": list(data["state"].get("constraints") or []),
            "human_decisions": len(data["state"].get("human_decisions") or []),
            "fork": (data["fork"] or {}).get("run_id") if data.get("fork") else "",
            "spans": len(data["spans"]),
        },
        "raw": {
            "spans": data["spans"],
            "events": [
                {
                    "event_id": str(e.event_id or f"EVT-{e.seq:05d}"),
                    "seq": e.seq,
                    "ts": e.ts,
                    "stage": e.stage,
                    "event_type": e.event_type,
                    "actor": e.actor,
                    "payload_ref": e.payload_ref,
                    "budget_used": e.budget_used,
                    "payload": getattr(e, "payload", {}) or {},
                }
                for e in data["events"]
            ],
        },
    }
