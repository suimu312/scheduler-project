"""判据层（O3）：signals.yaml 的可执行副本。

分工边界：
- `src/scheduler/obs.py` 负责「写」——开/关 span、落 spans.jsonl（A 的埋点）；
- 本模块负责「判」——读事件流与产物，判出信号级别（D 的判据）。

三条铁律（与 docs/observability-signals.md §二 一致）：
1. 只读。不写文件、不改运行状态、不调模型、不计预算；同一份输入永远同一个结论。
2. 能反算就不拍阈值。阈值一律从 signals.yaml 抄，不在这里另起一套。
3. 算不出来就说算不出来。缺字段返回 None，不补 0、不假装判过。

阈值与 signals.yaml 同源——改一处必须同步另一处，冲突时以 signals.yaml 为准。
处置动作（提示文案、钻取到哪个视图）留给呈现层，本模块只回答「命中没命中、什么级别」。
"""

from __future__ import annotations

from typing import Any, Iterable

# ---- 阈值（逐条对应 signals.yaml，勿在此另立口径） ----
BUDGET_WARN_HEADROOM = 0.20  # OBS-01 thresholds[0]：headroom < 0.20 且 stage != P7
RETRY_LIMIT = 3  # OBS-03：retry > 3 才 block
SIMILARITY_THRESHOLD = 0.85  # OBS-06：similarity > 0.85 判该次发言无效

# ---- 级别口径（signals.yaml meta.level_convention） ----
LEVEL_INFO = "info"  # 仅提示
LEVEL_WARN = "warn"  # 需人注意
LEVEL_BLOCK = "block"  # 必须干预（进等待人态）


def _dicts(rows: Iterable[Any]) -> list[dict[str, Any]]:
    """只留字典，脏数据直接跳过——判据层不该因为一条脏事件整轮崩掉。"""
    return [row for row in rows if isinstance(row, dict)]


# ---------------------------------------------------------------- OBS-01 预算余量

def budget_headroom(events: list[dict[str, Any]], ledger: dict[str, Any]) -> dict[str, Any] | None:
    """OBS-01：headroom = 1 - budget_used / global_limit。

    budget_used 取事件流里最后一条 budget.charged（事件流是主证据），
    取不到才退回 state.ledger.global_used。
    返回 None 表示未命中；命中时 level 为 warn 或 block。
    """
    charged = [e for e in _dicts(events) if e.get("event_type") == "budget.charged"]
    if not charged:
        return None  # 没有预算事件，这条信号判不了——不补 0

    ledger = ledger or {}
    limit = int(ledger.get("global_limit") or 0)
    last = charged[-1]
    used = last.get("budget_used")
    used = int(used if used is not None else (ledger.get("global_used") or 0))
    stage = str(last.get("stage") or "")

    if limit <= 0:
        headroom = 0.0
    else:
        headroom = 1 - used / limit

    if headroom <= 0:
        level = LEVEL_BLOCK
    elif headroom < BUDGET_WARN_HEADROOM and stage != "P7":
        # P7 是收尾阶段，余量低是常态，不报 warn（signals.yaml 里写死的例外）
        level = LEVEL_WARN
    else:
        return None

    return {
        "signal": "OBS-01",
        "code": "budget_headroom",
        "level": level,
        "stage": stage,
        "used": used,
        "limit": limit,
        "headroom": round(headroom, 4),
        "detail": f"{stage} 末轮预算 {used}/{limit}，余量 {headroom:.1%}",
    }


# ---------------------------------------------------------------- OBS-03 单卡重试

def task_retry(events: list[dict[str, Any]], limit: int = RETRY_LIMIT) -> list[dict[str, Any]]:
    """OBS-03：retry(task) = 该卡在 dispatched 与 completed 之间的 model.call 次数 - 1。

    只认带 task_id 的事件；没 task_id 的 model.call 归不到卡上，跳过而不是平摊。
    到事件流结束还没 completed 的卡，按「未完成」结算——校验器恒失败时正是这个样子。
    返回空列表 = 全都没超限。
    """
    opened: dict[str, int] = {}
    hits: list[dict[str, Any]] = []

    def close(task_id: str, state: str) -> None:
        calls = opened.pop(task_id, None)
        if calls is None:
            return
        retry = calls - 1
        if retry > limit:
            hits.append(
                {"task_id": task_id, "model_calls": calls, "retry": retry, "state": state}
            )

    for event in _dicts(events):
        kind = event.get("event_type")
        task_id = str(event.get("task_id") or "")
        if not task_id:
            continue
        if kind == "task.dispatched":
            opened.setdefault(task_id, 0)
        elif kind == "model.call" and task_id in opened:
            opened[task_id] += 1
        elif kind in ("task.completed", "task.failed"):
            close(task_id, kind)

    for task_id in list(opened):
        close(task_id, "unfinished")  # 收尾时仍未完成，按未完成结算

    return sorted(hits, key=lambda h: (-h["retry"], h["task_id"]))


# ---------------------------------------------------------------- OBS-06 发言趋同

def statement_similarity(
    statements: list[dict[str, Any]], threshold: float = SIMILARITY_THRESHOLD
) -> dict[str, Any] | None:
    """OBS-06：同一轮内两两比发言，相似度超阈值的判该次发言无效。

    相似度复用 `src/agents/core.py:estimate_similarity`——那是设计定稿里已有的实现，
    口径不能新拍一套。它返回 semantic_score（有 embedding 时）与 lexical_score
    （字符 bigram Jaccard，无 embedding 时的降级口径），取两者较大值与本阈值比。
    返回 None = 没有无效发言。
    """
    from agents.core import estimate_similarity  # 延迟导入：判据层不该在 import 期拉重依赖

    items = [s for s in _dicts(statements) if str(s.get("quote") or "").strip()]
    if len(items) < 2:
        return None  # 一条发言谈不上「趋同」

    pairs: list[dict[str, Any]] = []
    invalid: set[str] = set()

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            left, right = items[i], items[j]
            report = estimate_similarity(str(left["quote"]), str(right["quote"]))
            scores = [
                s for s in (report.get("semantic_score"), report.get("lexical_score")) if s is not None
            ]
            best = max(scores) if scores else 0.0
            if best >= threshold:
                pair = [str(left.get("agent_id") or ""), str(right.get("agent_id") or "")]
                pairs.append(
                    {
                        "pair": pair,
                        "score": round(float(best), 3),
                        "degraded": bool(report.get("degraded")),
                    }
                )
                invalid.update(pair)

    if not invalid:
        return None

    # yaml 的口径是「单轮内无效发言 >= 2」，而无效是按发言条数算的：
    # 只要有一对雷同，两条发言就都记无效，所以 info 分支实际上不可达。
    # 保留它是为了以后改成「按对数计数」时不用重写这段判定。
    level = LEVEL_WARN if len(invalid) >= 2 else LEVEL_INFO
    shown = "、".join(f"{p['pair'][0]}↔{p['pair'][1]} {p['score']:.2f}" for p in pairs)
    degraded = any(p["degraded"] for p in pairs)
    return {
        "signal": "OBS-06",
        "code": "statement_similarity",
        "level": level,
        "invalid_count": len(invalid),
        "invalid_agents": sorted(invalid),
        "pairs": pairs,
        "threshold": threshold,
        "similarity_mode": "lexical_degraded" if degraded else "embedding_and_lexical",
        "detail": f"{len(invalid)} 条发言判无效：{shown}（阈值 {threshold}）",
    }


# ---------------------------------------------------------------- 统一入口

def snapshot(
    events: list[dict[str, Any]],
    ledger: dict[str, Any] | None,
    statements: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """把三条信号一次判完，返回 {信号 id: 结论}，未命中的信号不出现。

    故障注入靠它做误报检查：注入 A 故障时，除 A 自己的信号外不该多出别的键。
    """
    out: dict[str, Any] = {}
    budget = budget_headroom(events, ledger or {})
    if budget:
        out["OBS-01"] = budget
    retries = task_retry(events)
    if retries:
        out["OBS-03"] = {
            "signal": "OBS-03",
            "code": "task_retry",
            "level": LEVEL_BLOCK,
            "tasks": retries,
            "detail": "超限任务 " + "、".join(f"{h['task_id']}(retry={h['retry']})" for h in retries),
        }
    similarity = statement_similarity(statements or [])
    if similarity:
        out["OBS-06"] = similarity
    return out
