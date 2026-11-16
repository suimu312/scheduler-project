# -*- coding: utf-8 -*-
"""P3 冲突裁决与 P5 对抗式评审的全部判定规则。

设计约束（见 docs/review-conflict.md）：
1. 全部为纯函数：不调模型、不读时钟、不写文件、不依赖本包之外的模块；
2. 只返回判定结果，落盘与事件由编排器 A 负责；
3. 加权总分必须归一化到百分制（四维权重和为 90，成本只估算不计分）；
4. 字段一律先过 models 的归一，禁止在规则里直接比较 "FOR" / "for" 这种原始值。

对外函数（与设计 §2.8 一一对应）：
    detect_conflicts(statements)       -> list[dict]     P3 冲突扫描
    adjudicate(conflict)               -> dict           P3 三级裁决
    validate_challenge(challenge)      -> list[str]      质疑四要素校验
    find_veto_items(tasks)             -> list[dict]     否决项判定
    review(scores, ...)                -> ScoreSheet     P5 打分 + 门禁 + 回炉分流
"""

from __future__ import annotations

import re
from statistics import pstdev
from typing import Any, Iterable

from .models import (
    SEVERITIES,
    Challenge,
    Conflict,
    ScoreSheet,
    Stance,
    canon_dim,
)

# 四个计分维度，权重和为 90；成本合理性只给估算，不计分
DIM_WEIGHTS: dict[str, int] = {
    "completeness": 25,
    "feasibility": 25,
    "risk": 25,
    "testability": 15,
}
# 二级裁决权重：用户代表与质量评审员的诉求被牺牲后返工代价最大
VOTE_WEIGHT: dict[str, float] = {"A1": 1.0, "A2": 1.5, "A3": 1.0, "A4": 1.5, "A5": 0.5, "A6": 0.5}

PASS_TOTAL = 80.0
STD_LIMIT = 12.0
VOTE_MARGIN = 1.0
MAX_TASK_DAYS = 3.0
MAX_P5_REVISIONS = 3
CHALLENGE_REJECT_LIMIT = 0.6

_ID_RE = re.compile(r"[A-Z]{2,5}-\d+")


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 打分（P5）

def canon_scores(scores: dict[str, Any] | None) -> dict[str, float]:
    """任意写法 → 四维标准名；成本与未知维度丢弃，缺维度不补。"""
    out: dict[str, float] = {}
    for key, value in (scores or {}).items():
        dim = canon_dim(key)
        num = _num(value)
        if dim and num is not None and dim not in out:
            out[dim] = num
    return out


def weighted_total(scores: dict[str, Any]) -> float | None:
    """四个维度原始分 -> 百分制总分。

    raw = Σ(score × weight)，权重和为 90，因此除以 90 即得百分制。
    注意不要再乘 100：那样会得到 8550 这种量级，门禁阈值 80 就永远成立。
    """
    dims = canon_scores(scores)
    if not dims:
        return None
    denom = sum(DIM_WEIGHTS[d] for d in dims)
    raw = sum(dims[d] * DIM_WEIGHTS[d] for d in dims)
    return round(raw / denom, 2)


def stddev(scores: dict[str, Any]) -> float | None:
    """四维原始分的总体标准差（不含成本、不用加权分），衡量「有维度严重分歧」。"""
    dims = canon_scores(scores)
    if len(dims) < 2:
        return None
    return round(pstdev(list(dims.values())), 2)


def gate(
    scores: dict[str, Any],
    veto: bool = False,
    veto_items: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """G_P5_P6：总分 >= 80、无否决项、标准差 < 12，三条同时满足才放行。"""
    veto_items = list(veto_items or [])
    total = weighted_total(scores)
    sd = stddev(scores)
    reasons: list[str] = []

    if total is None:
        reasons.append("缺少可用的维度分数，无法计算总分")
    elif total < PASS_TOTAL:
        reasons.append("加权总分 %.2f < %.0f" % (total, PASS_TOTAL))
    if veto or veto_items:
        codes = sorted({str(v.get("code") or "veto") for v in veto_items}) or ["veto"]
        reasons.append("存在阻断级否决项：" + "、".join(codes))
    if sd is not None and sd >= STD_LIMIT:
        reasons.append("标准差 %.2f >= %.0f（有维度分歧过大）" % (sd, STD_LIMIT))

    return {
        "pass": not reasons,
        "weighted_total": total,
        "stddev": sd,
        "veto": bool(veto or veto_items),
        "veto_items": veto_items,
        "reasons": reasons,
    }


# ---------------------------------------------------------------- 质疑四要素（P5）

def validate_challenge(challenge: Challenge | dict[str, Any], seq: int = 1) -> list[str]:
    """质疑四要素校验。返回错误列表，空列表即合格。

    设计要求：质疑不能是「整体感觉不太好」，必须说清 对象、问题、后果、归属。
    """
    ch = Challenge.from_any(challenge, seq)
    errors: list[str] = []
    if len(ch.description) < 8:
        errors.append("description 太短（<8 字），不构成可核查的问题描述")
    if not _ID_RE.search(ch.impact_ref):
        errors.append("impact_ref 未带对象 ID（应形如 FEA-001 / TASK-0001）")
    if len(ch.consequence) < 4:
        errors.append("consequence 缺失，未说明不改的后果")
    if ch.category not in ("solution", "requirement"):
        errors.append("category 必须是 solution 或 requirement")
    if ch.severity not in SEVERITIES:
        errors.append("severity 必须是 minor / major / blocking")
    return errors


def triage_challenges(challenges: Iterable[Any]) -> dict[str, Any]:
    """把质疑分成受理 / 退回两堆，并算驳回率（灌水抑制）。

    驳回率 = 退回条数 / 提交条数，全部由规则算，不允许模型自评。
    """
    accepted: list[Challenge] = []
    rejected: list[dict[str, Any]] = []
    for seq, raw in enumerate(challenges or [], start=1):
        ch = Challenge.from_any(raw, seq)
        errors = validate_challenge(ch, seq)
        if errors:
            rejected.append({"challenge": ch.to_dict(), "errors": errors})
        else:
            accepted.append(ch)
    total = len(accepted) + len(rejected)
    rate = round(len(rejected) / total, 3) if total else 0.0
    guard = None
    if total and rate > CHALLENGE_REJECT_LIMIT:
        guard = {
            "signal": "challenge_reject_rate_high",
            "value": rate,
            "reason": "驳回率 %.0f%% 超过 %.0f%%，评审员可能在建无效质疑"
            % (rate * 100, CHALLENGE_REJECT_LIMIT * 100),
        }
    return {
        "accepted": accepted,
        "rejected": rejected,
        "submit_count": total,
        "reject_count": len(rejected),
        "reject_rate": rate,
        "guard": guard,
    }


# ---------------------------------------------------------------- 否决项（P5 结构硬伤）

def _dep_cycle_ids(tasks: list[dict[str, Any]]) -> list[str]:
    """Kahn 拓扑排序，剩下没出队的就是成环节点。"""
    graph: dict[str, set[str]] = {}
    for task in tasks:
        tid = str(task.get("task_id") or task.get("id") or "").strip()
        if not tid:
            continue
        deps = task.get("depends_on") or task.get("dependencies") or []
        if isinstance(deps, str):
            deps = [deps]
        graph[tid] = {str(d).strip() for d in deps if str(d).strip()}

    indeg = {tid: 0 for tid in graph}
    for tid, deps in graph.items():
        for dep in deps:
            if dep in indeg:
                indeg[tid] += 1

    ready = [t for t, d in indeg.items() if d == 0]
    seen = 0
    while ready:
        node = ready.pop()
        seen += 1
        for tid, deps in graph.items():
            if node in deps:
                indeg[tid] -= 1
                if indeg[tid] == 0:
                    ready.append(tid)
    if seen >= len(graph):
        return []
    return sorted(tid for tid, d in indeg.items() if d > 0)


def find_veto_items(
    tasks: Iterable[dict[str, Any]],
    max_days: float = MAX_TASK_DAYS,
) -> list[dict[str, Any]]:
    """否决项判定（设计 7.1 D 的第一条职责）：三条结构硬伤，命中即整单不放行。

    ① 依赖成环 —— 排期算法永远算不出波次；
    ② 有任务无验收 —— 无法判定「做完了」，基线不可冻结；
    ③ 单卡超 3 人天 —— 粒度太粗，估时不可信。
    """
    tasks = [t for t in (tasks or []) if isinstance(t, dict)]
    items: list[dict[str, Any]] = []

    cycle = _dep_cycle_ids(tasks)
    if cycle:
        items.append({
            "code": "dep_cycle",
            "severity": "blocking",
            "task_ids": cycle,
            "detail": "依赖成环：" + "、".join(cycle),
        })

    for task in tasks:
        tid = str(task.get("task_id") or task.get("id") or "?").strip()
        acceptance = task.get("acceptance") or task.get("acceptance_criteria") or []
        if isinstance(acceptance, str):
            acceptance = [acceptance]
        acceptance = [a for a in acceptance if str(a).strip()]
        if not acceptance:
            items.append({
                "code": "no_acceptance",
                "severity": "blocking",
                "task_id": tid,
                "detail": "任务 %s 没有验收标准" % tid,
            })

        days = _num(task.get("estimate_days") or task.get("estimate") or task.get("days"))
        if days is not None and days > max_days:
            items.append({
                "code": "oversize",
                "severity": "major",
                "task_id": tid,
                "detail": "任务 %s 预估 %.1f 人天 > %.0f 人天，粒度太粗" % (tid, days, max_days),
            })
    return items


# ---------------------------------------------------------------- 冲突扫描（P3）

def detect_conflicts(stances: Iterable[Any], existing: int = 0) -> list[dict[str, Any]]:
    """同一议题出现两种以上立场即登记一条冲突。

    中性/弃权不计入立场数：四个人里两个支持一个中立，仍然只有一种有效立场。
    """
    grouped: dict[str, list[Stance]] = {}
    for raw in stances or []:
        st = Stance.from_any(raw)
        key = st.topic_id or "unnamed"
        grouped.setdefault(key, []).append(st)

    conflicts: list[dict[str, Any]] = []
    seq = existing
    for topic_id, group in grouped.items():
        positions = {s.position for s in group if s.position != "neutral"}
        if len(positions) < 2:
            continue
        seq += 1
        issue_id = next(
            (s.quote_ref.split("-")[1] for s in group if s.quote_ref.count("-") >= 2), topic_id
        )
        conflicts.append(
            Conflict(
                conflict_id="CFL-%03d" % seq,
                topic_id=topic_id,
                issue_id=issue_id,
                stances=group,
            ).to_dict()
        )
    return conflicts


# ---------------------------------------------------------------- 三级裁决（P3）

def _verdict(conf: Conflict, level: str, status: str, resolution: dict[str, Any]) -> dict[str, Any]:
    return {
        "conflict_id": conf.conflict_id,
        "issue_id": conf.issue_id,
        "topic_id": conf.topic_id,
        "level": level,
        "status": status,
        "resolution": resolution,
    }


def adjudicate(conflict: Conflict | dict[str, Any]) -> dict[str, Any]:
    """三级裁决：证据优先 → 加权投票 → 挂起交人。

    一级只看「有没有可查依据」，不看谁嗓门大；
    二级按角色权重算票，边际 1.0 以下判不动；
    三级不猜，直接挂起进 HUMAN_GATE。
    """
    conf = conflict if isinstance(conflict, Conflict) else Conflict.from_dict(conflict)
    stances = [s for s in conf.stances if s.position != "neutral"]

    with_ev = [s for s in stances if s.has_evidence]
    without_ev = [s for s in stances if not s.has_evidence]

    # 一级：一方有依据、另一方只有主张
    if with_ev and without_ev:
        winner = with_ev[0]
        return _verdict(conf, "L1_evidence", "resolved", {
            "type": "evidence",
            "winner": winner.agent_id,
            "proposal": winner.claim,
            "decided_by": "rule",
            "reason": "对方无可查依据，直接采信有依据一方",
        })

    # 二级：按角色权重算票
    score = 0.0
    for s in stances:
        weight = VOTE_WEIGHT.get(s.agent_id, 1.0)
        score += weight if s.position == "for" else -weight
    score = round(score, 2)

    if abs(score) >= VOTE_MARGIN:
        side = "for" if score > 0 else "against"
        winner = next((s for s in stances if s.position == side), None)
        if winner:
            return _verdict(conf, "L2_weighted_vote", "resolved", {
                "type": "weighted_vote",
                "winner": winner.agent_id,
                "proposal": winner.claim,
                "score": score,
                "decided_by": "rule",
                "reason": "加权票差 %+.1f，达到判定线 %.1f" % (score, VOTE_MARGIN),
            })

    # 三级：判不动，挂起
    return _verdict(conf, "L3_escalate", "suspended", {
        "type": "human",
        "winner": None,
        "score": score,
        "decided_by": "human",
        "reason": "加权票差 %+.1f，未达判定线 %.1f，需人工裁决" % (score, VOTE_MARGIN),
    })


# ---------------------------------------------------------------- 回炉分流（P5）

def route_rework(
    challenges: Iterable[Any],
    revision_count: int = 0,
    limit: int = MAX_P5_REVISIONS,
) -> dict[str, Any]:
    """打分不通过时往哪儿退。

    优先级与 A 的 gate_rule.yaml 保持一致：方案层（G_P5_REWORK_SOL）先于需求层判定。
    累计修改次数到上限则不再回炉，直接交人。
    """
    challenges = [Challenge.from_any(c, i) for i, c in enumerate(challenges or [], start=1)]
    if revision_count >= limit:
        return {
            "target": "HUMAN",
            "reason": "累计修改 %d 次已达上限 %d，交人定夺" % (revision_count, limit),
        }

    solution = [c for c in challenges if c.category == "solution"]
    requirement = [c for c in challenges if c.category == "requirement"]
    if solution:
        return {"target": "P4", "reason": "方案层问题：改切法/改取舍，退 P4 重出三套"}
    if requirement:
        return {"target": "P3", "reason": "需求层问题：口径没谈拢，退 P3 补质询"}
    return {"target": "P4", "reason": "无明确归因，按方案层默认退 P4"}


# ---------------------------------------------------------------- 一步到位

def review(
    scores: dict[str, Any],
    veto: bool = False,
    veto_items: Iterable[dict[str, Any]] | None = None,
    challenges: Iterable[Any] | None = None,
    revision_count: int = 0,
    score_id: str = "SCR-001",
) -> ScoreSheet:
    """P5 一步到位：四要素校验 → 算分 → 门禁 → 回炉分流。"""
    dims = canon_scores(scores)
    triage = triage_challenges(challenges or [])
    result = gate(scores, veto=veto, veto_items=veto_items)

    rework = None
    if not result["pass"]:
        rework = route_rework(triage["accepted"], revision_count)

    return ScoreSheet(
        score_id=score_id,
        scores=dims,
        weighted_total=result["weighted_total"] if result["weighted_total"] is not None else 0.0,
        stddev=result["stddev"] if result["stddev"] is not None else 0.0,
        veto=result["veto"],
        veto_items=result["veto_items"],
        challenges=triage["accepted"],
        passed=result["pass"],
        reasons=result["reasons"],
        rework_target=(rework or {}).get("target"),
    )
