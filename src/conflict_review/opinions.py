"""P5 意见处理表（D｜本周补考项）

问题出在哪：P7 产出的 `opinion_handling.json` 一直是 `{"opinions": []}`——不是忘了填，
而是 `src/scheduler/mocks.py` 里写死了空表；`baseline_mgr.apply_human_decision` 的处置
又只有 `adopt` / `pending` 两态，既没有"不采纳"，也没有理由。

本模块补上 P5 这一环：把评审单上的每条疑质按**产物复算**成三态处置，每条都带一条
能当场核对的理由。P7 只负责汇总落盘，不再自己编。

字段与枚举严格照 `docs/review-conflict.md §2.1 ReviewOpinion` 的设计定稿，不另起一套：
    id / author / target / problem / impact / if_unfixed / severity / verdict
`verdict` 取值就是设计里的 `ACCEPTED` / `REJECTED` / `PENDING`，对应中文「采纳 / 不采纳 / 待议」。
设计文档没有 `reason` 这一列，但计划里要求"采纳/不采纳/待议 **+ 理由**"，所以本模块在
设计字段之后追加 `reason` / `evidence` / `action` / `handled_by` 四个扩展字段，
评审单上排在标准的八列之后，不改动原有八列的语义。

三态判定表（全部可复算，不调模型）：
    impact = FEA-xxx        → 看 solution_comparison.rows[FEA].adopted 是否为空
    impact = CFL-xxx        → 看 conflict_register.conflicts[CFL].status
    impact = DEV-/TASK-xxx  → P5 阶段还没有任务卡，一律 PENDING
    其他 / 为空             → PENDING，并写明"指错对象"

受理门（照 §3.1 执行）：缺 `if_unfixed`（不改会怎样）的意见直接丢弃、不进制表——
这是设计里防"正确的废话"那道闸，不能在本模块绕过去。丢弃条数记在 summary 里。
"""

from __future__ import annotations

from typing import Any, Iterable

# ---- 三态：照设计文档 §2.1 的 verdict 枚举，不要改成小写或别的词 ----
ACCEPTED = "ACCEPTED"
REJECTED = "REJECTED"
PENDING = "PENDING"
VERDICTS = (ACCEPTED, REJECTED, PENDING)

VERDICT_LABEL = {ACCEPTED: "采纳", REJECTED: "不采纳", PENDING: "待议"}

# ---- 严重度：内部小写，对外按 §2.1 的大写枚举出 ----
SEVERITY_TO_DESIGN = {"blocking": "BLOCKER", "major": "MAJOR", "minor": "MINOR"}

# 提出意见的人：规则版评审员，由质量评审员 A4 署名（风险维度归 A4）
AUTHOR = "A4"

# 复核人：方案层 → 架构师 A3，需求层 → 用户代表 A2
HANDLED_BY = {"solution": "A3", "requirement": "A2"}

# P5 阶段还拿不到的产物所对应的 id 前缀
_FUTURE_PREFIXES = ("DEV-", "TASK-")


def _field(obj: Any, name: str, default: Any = "") -> Any:
    """同时吃 dataclass 与 dict —— 调用方不用先转格式。"""
    if isinstance(obj, dict):
        value = obj.get(name)
    else:
        value = getattr(obj, name, None)
    return default if value is None else value


def _rows_by_feature(comparison: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = (comparison or {}).get("rows") or []
    return {
        str(row.get("feature_id")): row
        for row in rows
        if isinstance(row, dict) and row.get("feature_id")
    }


def _conflicts_by_id(register: dict[str, Any]) -> dict[str, dict[str, Any]]:
    conflicts = (register or {}).get("conflicts") or []
    return {
        str(conf.get("conflict_id")): conf
        for conf in conflicts
        if isinstance(conf, dict) and conf.get("conflict_id")
    }


def _adopted_of(row: dict[str, Any]) -> str:
    """取出这一行被采纳的方案 id；空串表示没人被采纳。"""
    adopted = row.get("adopted")
    if isinstance(adopted, dict):
        return str(adopted.get("solution_id") or "")
    return str(adopted or "")


def _judge_feature(target: str, row: dict[str, Any] | None, row_count: int) -> tuple[str, str, dict]:
    """FEA-xxx：采纳与否看这一行有没有被采纳的方案。"""
    if row is None:
        return (
            PENDING,
            f"{target} 不在方案对比的 {row_count} 行里，意见指错了对象",
            {"checked": "solution_comparison.rows", "actual": "未找到该功能项", "expected": target},
        )

    options = row.get("options") or []
    sid = _adopted_of(row)
    if sid:
        return (
            REJECTED,
            f"{target} 在方案对比里已采纳 {sid}（候选 {len(options)} 个），意见与产物不符",
            {"checked": f"rows[{target}].adopted", "actual": sid, "expected": "空"},
        )
    return (
        ACCEPTED,
        f"{target} 有 {len(options)} 个候选方案但没有任何一个被采纳，意见成立",
        {"checked": f"rows[{target}].adopted", "actual": "空", "expected": "非空"},
    )


def _judge_conflict(
    target: str, conf: dict[str, Any] | None, open_count: int
) -> tuple[str, str, dict]:
    """CFL-xxx：采纳与否看这条冲突是不是已经裁决掉了。"""
    if conf is None:
        return (
            PENDING,
            f"冲突登记表里没有 {target}，意见指错了对象",
            {"checked": "conflict_register.conflicts", "actual": "未找到该冲突", "expected": target},
        )

    status = str(conf.get("status") or "open")
    level = str(conf.get("level") or "—")

    if status == "resolved":
        return (
            REJECTED,
            f"冲突 {target} 已 {status}（裁决路径 {level}），意见已处理，不重复回炉",
            {"checked": f"conflicts[{target}].status", "actual": status, "expected": "open"},
        )
    if status == "suspended":
        return (
            PENDING,
            f"冲突 {target} 处于 {status}（已升级 L3 待裁决），定不了性，等裁决后复核",
            {"checked": f"conflicts[{target}].status", "actual": status, "expected": "resolved"},
        )
    return (
        ACCEPTED,
        f"冲突 {target} 仍为 {status}（登记表 open_count={open_count}），意见成立，不许带进 P6",
        {"checked": f"conflicts[{target}].status", "actual": status, "expected": "resolved"},
    )


def _action_for(verdict: str, target: str, category: str) -> str:
    """处置动作：采纳要说退到哪一层，待议要说等什么。"""
    if verdict == ACCEPTED:
        if target.startswith("CFL-"):
            return "退 P3 补质询，结论未定前不许进 P6"
        return "退 P4 补该功能项的采纳方案"
    if verdict == REJECTED:
        return "无需回炉；留档备查，答辩时可作为「意见被证伪」的证据"
    if target.startswith(_FUTURE_PREFIXES):
        return "待 P6 产出任务卡后复核"
    return f"由 {HANDLED_BY.get(category, 'A3')} 带证据重提"


def _reason_is_checkable(reason: str, target: str) -> bool:
    """理由里必须有具体 id 或数字，否则就是"整体不太好"这类空话。"""
    if len(reason.strip()) < 8:
        return False
    if target and target in reason:
        return True
    return any(ch.isdigit() for ch in reason)


def _is_dropped(challenge: Any) -> bool:
    """§3.1 受理门的两种丢弃：说不清后果、或指不到具体条目。"""
    if not str(_field(challenge, "consequence")).strip():
        return True  # 缺 if_unfixed：不改会怎样都说不清，就不是质疑
    target = str(_field(challenge, "impact_ref")).strip()
    return not target.startswith(("FEA-", "CFL-") + _FUTURE_PREFIXES)


def build_opinions(
    challenges: Iterable[Any],
    comparison: dict[str, Any] | None,
    register: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """把每条意见判成三态，返回可直接落盘的 opinions 列表。

    缺 `if_unfixed` 的意见按 §3.1 丢弃，不进返回结果；丢弃条数由 `summarize` 统计。
    """
    rows = _rows_by_feature(comparison or {})
    conflicts = _conflicts_by_id(register or {})
    open_count = int((register or {}).get("open_count") or 0)

    opinions: list[dict[str, Any]] = []
    for challenge in challenges or []:
        # 受理门（§3.1）：说不清"不改会怎样"的、指不到具体条目的，都不是意见，是表态
        if _is_dropped(challenge):
            continue

        target = str(_field(challenge, "impact_ref")).strip()
        category = str(_field(challenge, "category", "solution")) or "solution"
        seq = len(opinions) + 1

        if target.startswith("FEA-"):
            verdict, reason, evidence = _judge_feature(target, rows.get(target), len(rows))
        elif target.startswith("CFL-"):
            verdict, reason, evidence = _judge_conflict(target, conflicts.get(target), open_count)
        else:  # DEV- / TASK-：P5 阶段还没有任务卡
            verdict, reason, evidence = (
                PENDING,
                f"P5 阶段还没有任务卡（dev_task_list 由 P6 产出），{target} 无法核对",
                {"checked": "P5 可读产物", "actual": "无任务卡", "expected": target},
            )

        opinions.append(
            {
                # ---- 设计文档 §2.1 的八个字段，语义不动 ----
                "id": f"R-{seq:03d}",
                "author": AUTHOR,
                "target": target,
                "problem": str(_field(challenge, "description")),
                "impact": target,
                "if_unfixed": str(_field(challenge, "consequence")),
                "severity": SEVERITY_TO_DESIGN.get(
                    str(_field(challenge, "severity", "major")).lower(), "MAJOR"
                ),
                "verdict": verdict,
                # ---- 本模块扩展：处置理由与去向 ----
                "verdict_label": VERDICT_LABEL[verdict],
                "reason": reason,
                "evidence": evidence,
                "challenge_id": str(_field(challenge, "challenge_id")),
                "category": category,
                "action": _action_for(verdict, target, category),
                "handled_by": HANDLED_BY.get(category, "A3"),
                "stage": "P5",
            }
        )
    return opinions


def validate_opinions(opinions: list[dict[str, Any]]) -> list[str]:
    """校验意见处理表：三态齐全、理由可核对、不重复。返回错误列表，空列表 = 通过。"""
    errors: list[str] = []
    seen: set[str] = set()

    for opinion in opinions or []:
        oid = str(opinion.get("id") or "?")
        target = str(opinion.get("target") or "")
        verdict = str(opinion.get("verdict") or "")
        reason = str(opinion.get("reason") or "")

        if verdict not in VERDICTS:
            errors.append(f"{oid}: 处置必须是 {'/'.join(VERDICTS)}，实际是「{verdict}」")
        if not _reason_is_checkable(reason, target):
            errors.append(f"{oid}: 理由里既没有 {target or '对象 id'} 也没有数字，等于没写理由")
        if not str(opinion.get("if_unfixed") or "").strip():
            errors.append(f"{oid}: 缺 if_unfixed（不改会怎样），按 §3.1 不该进制表")
        if not target:
            errors.append(f"{oid}: 缺 target，追不到针对哪个条目")
        elif target in seen:
            errors.append(f"{oid}: 条目 {target} 被处理了两次")
        else:
            seen.add(target)

    return errors


def summarize(opinions: list[dict[str, Any]], dropped: int = 0) -> dict[str, Any]:
    """给确认单/汇报用的一行统计：三态各几条、有没有漏处理的、丢了几条。"""
    counts = {v: 0 for v in VERDICTS}
    for opinion in opinions or []:
        key = str(opinion.get("verdict") or "")
        if key in counts:
            counts[key] += 1
    return {
        "total": len(opinions or []),
        ACCEPTED: counts[ACCEPTED],
        REJECTED: counts[REJECTED],
        PENDING: counts[PENDING],
        "dropped_unfixable": dropped,
        "unhandled": sum(counts.values()) < len(opinions or []),
    }


def count_dropped(challenges: Iterable[Any]) -> int:
    """按 §3.1 受理门被丢弃的意见条数（缺后果 / 指不到具体条目）。"""
    return sum(1 for c in challenges or [] if _is_dropped(c))
