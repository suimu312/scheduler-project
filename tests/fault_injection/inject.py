"""故障注入 · FI-01 ~ FI-05（O3 检出表的数据来源）

用途：验证判据层能不能把人为造出来的坏情况抓出来，并证明它不会乱抓。

两条底线：
- 只在内存里改数据，不碰 runs/ 下的真实文件，不碰业务代码；
- 注入的故障必须有唯一正确的检出信号，否则测不出漏报。

注入分两类：
  A. 产物注入（FI-01 依赖成环 / FI-04 覆盖空缺）——改任务卡集合，跑覆盖率与查环
  B. 事件与配置注入（FI-02 预算打满 / FI-03 单卡重试 / FI-05 发言趋同）——
     改账本限额、拼一段事件流、改两对角色的话，再由 src/observability/signals.py 判

用法：
    python tests/fault_injection/inject.py                          # 默认 examples/results/DEMO-WX
    python tests/fault_injection/inject.py --run runs/MOCKD         # 换本地自己跑的 run
    python tests/fault_injection/inject.py --case FI-02
    python tests/fault_injection/inject.py --case FI-02,FI-05

输出：每例一行结果（是否检出 / 命中信号 / 依据 / 耗时 / 有无误报），末行写 JSON 存档，
      同目录 last_run.json 可直接当《检出记录表》的原始数据。
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from observability import signals as sig  # noqa: E402
from req_builder.coverage import compute_coverage  # noqa: E402
from req_builder.models import SourceRef  # noqa: E402
from req_builder.task_list import load_task_list  # noqa: E402
from req_builder.validators import find_cycle  # noqa: E402

ALL_CASES = ["FI-01", "FI-02", "FI-03", "FI-04", "FI-05"]


# ---------------------------------------------------------------- 读运行目录

def _patch_refs(tasks, raw_tasks):
    """兼容层（临时）：dev_task_list.json 落盘时写的是 feature_refs / requirement_refs，
    而 DevTask.from_dict 只认 source_refs，导致二次读取后来源全丢、覆盖矩阵恒为 0%。
    这里从原始 JSON 把来源补回去，等 E 修好 models.py 的 from_dict 后可直接删掉本函数。
    """
    by_id = {str(r.get("task_id")): r for r in raw_tasks}
    for task in tasks:
        if task.source_refs:
            continue
        raw = by_id.get(task.task_id, {})
        refs = [SourceRef(kind="feature", ref_id=str(x)) for x in raw.get("feature_refs") or []]
        refs += [SourceRef(kind="requirement", ref_id=str(x)) for x in raw.get("requirement_refs") or []]
        task.source_refs = refs
    return tasks


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _artifact(run_dir: Path, name: str) -> dict:
    path = run_dir / "artifacts" / f"{name}.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("data") or payload


def _load(run_dir: Path) -> dict:
    cfg = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    spec = cfg.get("design_spec") or {}
    artifact = json.loads(
        (run_dir / "artifacts" / "dev_task_list.json").read_text(encoding="utf-8")
    )
    raw_tasks = (artifact.get("data") or artifact).get("tasks", [])
    tasks = _patch_refs(load_task_list(artifact), raw_tasks)

    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}

    return {
        "spec": spec,
        "tasks": tasks,
        "raw_tasks": raw_tasks,
        "events": _read_jsonl(run_dir / "events.jsonl"),
        "ledger": state.get("ledger") or {},
        "statements": _artifact(run_dir, "discussion_minutes").get("statements") or [],
    }


def _baseline(ctx: dict) -> dict:
    report = compute_coverage(ctx["tasks"], ctx["spec"])
    return {
        "gap_count": len(report.gaps),
        "uncovered_count": sum(1 for g in report.gaps if g.kind == "uncovered_source"),
        "coverage_rate": report.coverage_rate,
        "cycle": find_cycle(ctx["tasks"]),
        "signals": sorted(sig.snapshot(ctx["events"], ctx["ledger"], ctx["statements"])),
    }


def _precise_cycle(tasks) -> list[str]:
    """把真正的环取出来。

    validators.find_cycle 用的是 Kahn 残余法，返回的是"入度仍大于 0 的全部节点"，
    也就是环 + 环下游的整条链。判定"有没有环"够用，但报"环上任务"会夸大范围。
    这里用 DFS 回溯边取一个最小环，仅用于报告，不改 E 的实现。
    """
    graph = {t.task_id: list(t.depends_on) for t in tasks}
    state: dict[str, int] = {}  # 1=在栈上 2=已完成
    stack: list[str] = []

    def dfs(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for nxt in graph.get(node, []):
            if nxt not in graph:
                continue
            if state.get(nxt) == 1:
                return stack[stack.index(nxt):] + [nxt]
            if state.get(nxt) is None:
                found = dfs(nxt)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for tid in graph:
        if state.get(tid) is None:
            found = dfs(tid)
            if found:
                return found
    return []


# ---------------------------------------------------------------- A 类：产物注入

def inject_cycle(tasks):
    """FI-01：把一条已有依赖反向补一条，制造二元环。"""
    mutated = copy.deepcopy(tasks)
    by_id = {t.task_id: t for t in mutated}

    for task in mutated:
        if task.depends_on:
            upstream = by_id.get(task.depends_on[0])
            if upstream is not None and task.task_id not in upstream.depends_on:
                upstream.depends_on.append(task.task_id)
                return mutated, f"{upstream.task_id} → {task.task_id}（原方向 {task.task_id} → {upstream.task_id}）"

    if len(mutated) >= 2:  # 兜底：没有现成依赖就自己造一对
        a, b = mutated[0], mutated[1]
        a.depends_on.append(b.task_id)
        b.depends_on.append(a.task_id)
        return mutated, f"{a.task_id} ↔ {b.task_id}（新造对称依赖）"

    raise RuntimeError("任务卡少于 2 张，无法注入依赖成环")


def inject_gap(tasks, spec):
    """FI-04：抽掉承接某个来源的全部任务卡，制造覆盖空缺。"""
    base = compute_coverage(tasks, spec)
    victim_row = None

    for row in base.rows:
        if row.status == "covered" and row.covered_by:
            victim_row = row
            break

    if victim_row is None:
        raise RuntimeError("没有任何被覆盖的来源，无法注入空缺（先看基线是否已经全空）")

    removed = list(victim_row.covered_by)
    mutated = [t for t in copy.deepcopy(tasks) if t.task_id not in removed]
    return mutated, f"删除 {', '.join(removed)}，使 {victim_row.source_ref} 无人承接"


# ---------------------------------------------------------------- B 类：事件与配置注入

def inject_budget(events, ledger):
    """FI-02：把账本限额调到「已用次数 + 1」，再模拟跑一轮，验 warn → block 的两段升级。

    截到 P5 末轮是有意的：signals.yaml 里 warn 那条带 `stage != P7` 的例外，
    在 P7 上注入预算只会 block，看不出「先 warn 后 block」这条升级路径。
    """
    charged = [i for i, e in enumerate(events) if e.get("event_type") == "budget.charged"]
    if not charged:
        raise RuntimeError("事件流里没有 budget.charged，无法注入预算")

    p5 = [i for i in charged if events[i].get("stage") == "P5"]
    trimmed = events[: (p5[-1] if p5 else charged[-1]) + 1]
    used = int(trimmed[-1].get("budget_used") or 0)

    squeezed = dict(ledger or {})
    squeezed["global_limit"] = used + 1  # 只剩一次余量

    nxt = copy.deepcopy(trimmed[-1])  # 「再跑一轮」：已用次数 +1，限额不变
    nxt["event_id"] = f"{trimmed[-1].get('event_id')}-FI02"
    nxt["budget_used"] = used + 1
    after_round = trimmed + [nxt]

    how = f"把 global_limit 从 {ledger.get('global_limit')} 压到 {used + 1}（已用 {used}），再模拟跑一轮"
    return trimmed, squeezed, after_round, how


def inject_retry(events, tasks):
    """FI-03：拼一段「某卡连发多次调用、校验器恒失败」的事件流。

    故意做两张卡：一张超限、一张不超限，用来验「指出具体 task_id」而不是笼统说
    "有重试"。task_id 字段是本模块与埋点的约定（现网还没有 task.dispatched /
    model.call 埋点，所以只能用 fixture 注入）。
    """
    if len(tasks) < 2:
        raise RuntimeError("任务卡少于 2 张，无法构造单卡重试 fixture")
    over, fine = tasks[0].task_id, tasks[1].task_id

    base = events[-1] if events else {}
    out = list(events)
    seq = 0

    def emit(kind: str, task_id: str) -> None:
        nonlocal seq
        seq += 1
        out.append(
            {
                "event_id": f"EVT-FI03-{seq:03d}",
                "run_id": base.get("run_id") or "FIXTURE",
                "ts": base.get("ts") or "",
                "stage": "P6",
                "event_type": kind,
                "actor": "scheduler",
                "payload_ref": task_id,
                "task_id": task_id,
                "budget_used": base.get("budget_used") or 0,
            }
        )

    emit("task.dispatched", over)
    for _ in range(5):
        emit("model.call", over)  # 校验器恒失败，连发 5 次仍未完成 → retry=4
    emit("task.dispatched", fine)
    for _ in range(3):
        emit("model.call", fine)
    emit("task.completed", fine)  # retry=2，不该报

    how = f"{over} 连发 5 次调用且始终未完成（retry=4）；{fine} 3 次后完成（retry=2，应当不报）"
    return out, how


def inject_similarity(statements):
    """FI-05：给两组角色同一份上下文切片，制造高度相似的陈述。"""
    mutated = copy.deepcopy(list(statements))
    by_agent = {str(s.get("agent_id")): s for s in mutated if s.get("agent_id")}
    need = ["A1", "A2", "A3", "A4"]
    missing = [a for a in need if a not in by_agent]
    if missing:
        raise RuntimeError(f"发言里缺角色 {'/'.join(missing)}，凑不出两对趋同陈述")

    by_agent["A4"]["quote"] = by_agent["A1"]["quote"]
    by_agent["A3"]["quote"] = by_agent["A2"]["quote"]
    return mutated, "把 A4 的发言改成与 A1 完全相同、A3 改成与 A2 完全相同"


# ---------------------------------------------------------------- 判与记

def _others(case: str, snap: dict) -> list[str]:
    """除本用例自己的信号外，还多出来哪些信号——这些就是误报。"""
    own = {"FI-01": "OBS-04", "FI-02": "OBS-01", "FI-03": "OBS-03", "FI-04": "OBS-08", "FI-05": "OBS-06"}[case]
    return [k for k in snap if k != own]


def _result(case, detected, signal, detail, cost_ms, false_positive, extra=None):
    row = {
        "case": case,
        "detected": bool(detected),
        "signal": signal,
        "detail": detail,
        "cost_ms": round(cost_ms, 1),
        "false_positive": bool(false_positive),
    }
    if extra:
        row.update(extra)
    return row


def evaluate(case: str, ctx: dict, base: dict) -> dict:
    """逐例注入 + 判定。判定式全部来自现有实现，本函数不引入新规则。"""
    t0 = time.perf_counter()
    extra: dict = {}
    # 误报检查的基线：注入前的信号快照。注入后只许多出「本用例那一条」，多出别的就是误报。
    snap = sig.snapshot(ctx["events"], ctx["ledger"], ctx["statements"])

    if case == "FI-01":
        mutated, how = inject_cycle(ctx["tasks"])
        blocked = find_cycle(mutated)
        ring = _precise_cycle(mutated)
        signal = "OBS-04 依赖成环"
        if blocked:
            detail = f"环 {len(ring) - 1} 张：{' → '.join(ring)}"
            if len(blocked) > len(ring) - 1:
                detail += f"（find_cycle 另把下游 {len(blocked) - (len(ring) - 1)} 张也算进环里）"
        else:
            detail = "未检出"
        uncovered = sum(
            1 for g in compute_coverage(mutated, ctx["spec"]).gaps if g.kind == "uncovered_source"
        )
        false_positive = uncovered > base["uncovered_count"]
        detected = bool(blocked)

    elif case == "FI-04":
        mutated, how = inject_gap(ctx["tasks"], ctx["spec"])
        report = compute_coverage(mutated, ctx["spec"])
        gap_ids = [g.target for g in report.gaps if g.kind == "uncovered_source"]
        detected = len(gap_ids) > base["uncovered_count"]
        signal = "OBS-08 覆盖空缺"
        detail = f"缺失来源 {', '.join(gap_ids)}" if detected else "未检出"
        false_positive = bool(find_cycle(mutated))
        extra["gap_delta"] = len(gap_ids) - base["uncovered_count"]

    elif case == "FI-02":
        trimmed, squeezed, after_round, how = inject_budget(ctx["events"], ctx["ledger"])
        first = sig.budget_headroom(trimmed, squeezed)
        second = sig.budget_headroom(after_round, squeezed)
        signal = "OBS-01 预算余量"
        detected = bool(first and first["level"] == "warn" and second and second["level"] == "block")
        if first and second:
            detail = (
                f"该轮末尾 {first['used']}/{first['limit']}，余量 {first['headroom']:.1%} → warn；"
                f"再跑一轮 {second['used']}/{second['limit']}，余量 {second['headroom']:.1%} → block"
            )
        else:
            detail = f"未按预期升级（第一段 {first and first['level']}，第二段 {second and second['level']}）"
        snap = sig.snapshot(after_round, squeezed, ctx["statements"])
        false_positive = False
        extra["stages"] = [
            {"at": "该轮末尾", "level": first and first["level"], "headroom": first and first["headroom"]},
            {"at": "再跑一轮", "level": second and second["level"], "headroom": second and second["headroom"]},
        ]

    elif case == "FI-03":
        mutated, how = inject_retry(ctx["events"], ctx["tasks"])
        hits = sig.task_retry(mutated)
        signal = "OBS-03 单卡重试超限"
        detected = any(h["retry"] > sig.RETRY_LIMIT for h in hits)
        named = "、".join(f"{h['task_id']}（{h['model_calls']} 次调用，retry={h['retry']}）" for h in hits)
        detail = f"超限任务 {named}" if detected else "未检出"
        snap = sig.snapshot(mutated, ctx["ledger"], ctx["statements"])
        # 另一张卡 retry=2 没被报出来，才算「指得准」而不是「一报一大片」
        over_reported = any(h["retry"] <= sig.RETRY_LIMIT for h in hits)
        false_positive = over_reported
        extra["retry_hits"] = hits

    elif case == "FI-05":
        mutated, how = inject_similarity(ctx["statements"])
        verdict = sig.statement_similarity(mutated)
        signal = "OBS-06 发言趋同"
        detected = bool(verdict and verdict["level"] == "warn")
        detail = verdict["detail"] if verdict else "未检出"
        snap = sig.snapshot(ctx["events"], ctx["ledger"], mutated)
        false_positive = False
        if verdict:
            extra["pairs"] = verdict["pairs"]
            extra["similarity_mode"] = verdict["similarity_mode"]

    else:  # pragma: no cover - argparse 已限制取值范围
        raise ValueError(f"未知用例 {case}")

    spurious = [k for k in _others(case, snap) if k not in base["signals"]]
    if spurious:
        false_positive = True
        extra["spurious_signals"] = spurious

    return _result(case, detected, signal, detail, (time.perf_counter() - t0) * 1000,
                   false_positive, {**extra, "injection": how})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--run",
        default="examples/results/DEMO-WX",
        help="运行目录（含 config.json / state.json / artifacts/）；默认用入库的那份，clone 后即可复现",
    )
    ap.add_argument("--case", default="all", help="FI-01 ~ FI-05，多个用逗号分隔，或 all")
    args = ap.parse_args()

    run_dir = (ROOT / args.run).resolve()
    ctx = _load(run_dir)
    base = _baseline(ctx)

    print(
        f"基线：任务 {len(ctx['tasks'])} 张｜缺口 {base['gap_count']}｜覆盖率 {base['coverage_rate']:.2%}"
        f"｜成环 {base['cycle'] or '无'}｜已触发信号 {', '.join(base['signals']) or '无'}"
    )
    print()
    print("| 用例 | 是否检出 | 命中信号 | 依据 | 耗时 | 误报 |")
    print("|---|---|---|---|---|---|")

    cases = ALL_CASES if args.case == "all" else [c.strip().upper() for c in args.case.split(",") if c.strip()]
    results = []

    for case in cases:
        try:
            result = evaluate(case, ctx, base)
        except RuntimeError as exc:
            print(f"| {case} | 无法注入 | — | {exc} | — | — |")
            continue
        results.append(result)
        print(
            f"| {case} | {'是' if result['detected'] else '**否**'} | {result['signal']} "
            f"| {result['detail']} | {result['cost_ms']} ms | {'有' if result['false_positive'] else '无'} |"
        )
        print(f"    注入方式：{result['injection']}")

    detected = sum(1 for r in results if r["detected"])
    false_positives = sum(1 for r in results if r["false_positive"])
    hit_rate = detected / len(results) if results else 0.0

    print()
    print(
        f"汇总：注入 {len(results)} 例，检出 {detected} 例（{hit_rate:.0%}），"
        f"误报 {false_positives} 条，单例最慢 {max((r['cost_ms'] for r in results), default=0)} ms"
    )

    out = ROOT / "tests" / "fault_injection" / "last_run.json"
    out.write_text(
        json.dumps(
            {
                "run": str(run_dir),
                "baseline": base,
                "summary": {
                    "injected": len(results),
                    "detected": detected,
                    "hit_rate": round(hit_rate, 4),
                    "false_positives": false_positives,
                    "max_cost_ms": max((r["cost_ms"] for r in results), default=0),
                },
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"存档：{out.relative_to(ROOT)}")
    return 0 if all(r["detected"] and not r["false_positive"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
