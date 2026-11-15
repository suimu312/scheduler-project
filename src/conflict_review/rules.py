# -*- coding: utf-8 -*-
"""P3 冲突裁决与 P5 评审判定。

设计约束（见 docs/review-conflict.md）：
1. 全部为纯函数：不调模型、不读时钟、不写文件；
2. 只返回判定结果，落盘与事件由编排器 A 负责；
3. 加权总分必须先归一化到百分制（四维权重和为 90，成本不计分）。
"""

from statistics import pstdev

# 四个计分维度，权重和为 90；成本合理性只给估算，不计分
DIM_WEIGHTS = {"functional": 25, "technical": 25, "risk": 25, "testability": 15}
# 二级裁决权重：用户代表与质量评审员的诉求被牺牲后返工代价最大
VOTE_WEIGHT = {"A1": 1.0, "A2": 1.5, "A3": 1.0, "A4": 1.5}

PASS_TOTAL = 80.0
STD_LIMIT = 12.0
VOTE_MARGIN = 1.0


def weighted_total(scores):
    """四个维度原始分 -> 百分制总分。

    raw = sum(score * weight)，权重和为 90，因此除以 90 即得百分制。
    注意不要再乘 100：那样会得到 8550 这种量级，门禁阈值 80 就永远成立。
    """
    raw = sum(scores[k] * w for k, w in DIM_WEIGHTS.items())
    return round(raw / sum(DIM_WEIGHTS.values()), 2)


def stddev(scores):
    """四个计分维度原始分的标准差（不含成本、不用加权分）。"""
    return round(pstdev([scores[k] for k in DIM_WEIGHTS]), 2)


def gate(scores, veto=False):
    """G_P5_P6：总分 >= 80、无否决项、标准差 < 12，三条同时满足才放行。"""
    total, sd = weighted_total(scores), stddev(scores)
    reasons = []
    if total < PASS_TOTAL:
        reasons.append("加权总分 %.2f < %.0f" % (total, PASS_TOTAL))
    if veto:
        reasons.append("存在阻断级否决项")
    if sd >= STD_LIMIT:
        reasons.append("标准差 %.2f >= %.0f（有维度分歧过大）" % (sd, STD_LIMIT))
    return {
        "pass": not reasons,
        "weighted_total": total,
        "stddev": sd,
        "veto": veto,
        "reasons": reasons,
    }


def detect_conflicts(stances, existing=0):
    """同议题出现两种以上立场即登记一条冲突。

    stances: [{"agent_id","topic_id","position","proposal","evidence_refs","quote_ref"}]
    """
    grouped = {}
    for st in stances:
        grouped.setdefault(st["topic_id"], []).append(st)

    conflicts, seq = [], existing
    for topic_id, group in grouped.items():
        if len({g["position"] for g in group}) >= 2:
            seq += 1
            conflicts.append({
                "conflict_id": "CFL-%03d" % seq,
                "topic_id": topic_id,
                "stances": group,
                "level": None,
                "status": "open",
            })
    return conflicts


def adjudicate(conflict):
    """三级裁决：证据优先 -> 加权投票 -> 挂起。"""
    stances = conflict["stances"]
    with_ev = [s for s in stances if s.get("evidence_refs")]
    without_ev = [s for s in stances if not s.get("evidence_refs")]

    # 一级：一方有依据、另一方只有主张，直接采有依据的一方，不投票
    if with_ev and without_ev:
        winner = with_ev[0]
        return {
            "conflict_id": conflict["conflict_id"],
            "level": "L1_evidence",
            "status": "resolved",
            "resolution": {
                "type": "evidence",
                "winner": winner["agent_id"],
                "proposal": winner["proposal"],
                "decided_by": "rule",
                "reason": "对方无可查依据",
            },
        }

    # 二级：按角色权重算票
    score = 0.0
    for s in stances:
        w = VOTE_WEIGHT.get(s["agent_id"], 1.0)
        score += w if s["position"] == "for" else -w
    score = round(score, 2)

    if abs(score) >= VOTE_MARGIN:
        # 票差为正取第一个 for，为负取第一个 against
        side = "for" if score > 0 else "against"
        winner = next(s for s in stances if s["position"] == side)
        return {
            "conflict_id": conflict["conflict_id"],
            "level": "L2_weighted_vote",
            "status": "resolved",
            "resolution": {
                "type": "weighted_vote",
                "winner": winner["agent_id"],
                "proposal": winner["proposal"],
                "score": score,
                "decided_by": "rule",
                "reason": "加权票差 %.1f" % score,
            },
        }

    # 三级：分不出，挂起交人
    return {
        "conflict_id": conflict["conflict_id"],
        "level": "L3_escalate",
        "status": "suspended",
        "resolution": {
            "type": "human",
            "winner": None,
            "score": score,
            "decided_by": "human",
            "reason": "加权票差 %.1f，未达判定线，需人裁决" % score,
        },
    }


def review(scores, veto=False):
    """P5 一步到位：算分 + 门禁判定。"""
    result = gate(scores, veto)
    result["scores"] = dict(scores)
    return result
