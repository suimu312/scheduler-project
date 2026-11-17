# -*- coding: utf-8 -*-
"""D 侧数据结构：立场、冲突、质疑、评分单。

这里解决的是「字段对不上」这一联调头号故障：

- B 的 discussion_minutes 里写的是 `claim` / `source_refs` / `position="FOR"`；
- 设计文档与旧代码写的是 `proposal` / `evidence_refs` / `position="for"`；
- 两边都必须能读，所以统一在 from_any() 里做归一，内部只用标准名。

归一后的标准名（对外契约，写死在这里，改字段先改本文件）：
    Stance   : agent_id / topic_id / position(for|against|neutral) / claim / source_refs / quote_ref
    Conflict : conflict_id / issue_id / topic_id / stances / level / status / resolution
    Challenge: challenge_id / description / impact_ref / consequence / severity / category / verdict
    ScoreSheet: score_id / scores(四维原始分) / weighted_total / stddev / veto / veto_items / challenges
            + evidence / unavailable / reviewed_scope（评审依据，别省）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------- 归一工具

POS_FOR = {"for", "支持", "同意", "赞成", "yes", "pro", "adopt"}
POS_AGAINST = {"against", "反对", "不同意", "no", "con", "reject"}
POS_NEUTRAL = {"neutral", "中立", "abstain", "弃权", "unknown"}

# 四维计分口径：A 的 gate_check 和 mocks 都用这套名字，别名只做兼容，不做扩展
DIM_CANONICAL: tuple[str, ...] = ("completeness", "feasibility", "risk", "testability")
DIM_ALIASES: dict[str, str] = {
    # 设计文档口径
    "completeness": "completeness",
    "coverage": "completeness",
    "覆盖度": "completeness",
    "完整性": "completeness",
    "functional": "completeness",  # 旧代码
    # 可行性
    "feasibility": "feasibility",
    "technical": "feasibility",  # 旧代码
    "可执行性": "feasibility",
    "技术可行性": "feasibility",
    # 依赖闭环
    "risk": "risk",
    "dependency": "risk",
    "dependency_closure": "risk",
    "依赖闭环": "risk",
    "风险": "risk",
    # 验收可测性
    "testability": "testability",
    "可测试性": "testability",
    "验收可测性": "testability",
}

CHALLENGE_CATEGORY = {"solution": "solution", "sol": "solution", "方案": "solution",
                      "solution_level": "solution", "selection": "solution",
                      "requirement": "requirement", "req": "requirement", "需求": "requirement",
                      "requirement_level": "requirement", "scope": "requirement"}
SEVERITIES = ("minor", "major", "blocking")

# 四个计分维度，权重和为 90；成本合理性只给估算，不计分。
# 放在 models 而不是 rules：这是对外契约的一部分（评审单上要印权重列）。
DIM_WEIGHTS: dict[str, int] = {
    "completeness": 25,
    "feasibility": 25,
    "risk": 25,
    "testability": 15,
}

# 按角色分派：谁负责哪一维。P5 在席的是 A2/A3/A4/A5，A1 是方案作者必须回避。
DIM_SCORER: dict[str, str] = {
    "completeness": "A2",   # 用户代表：功能是不是都有人接
    "feasibility": "A3",    # 架构师：技术上做不做得到
    "risk": "A4",           # 质量评审员：依赖、集成、返工风险
    "testability": "A5",    # PM：验收标准能不能写出来
}


def _s(value: Any) -> str:
    return str(value or "").strip()


def canon_position(raw: Any) -> str:
    """任意写法 → for / against / neutral（大小写、中文都吃）。"""
    text = _s(raw).lower()
    if text in POS_FOR:
        return "for"
    if text in POS_AGAINST:
        return "against"
    if text in POS_NEUTRAL:
        return "neutral"
    if text in ("支持", "同意"):
        return "for"
    return "neutral"


def canon_dim(raw: Any) -> str | None:
    """维度名 → 四维标准名；成本/未知维度返回 None（不计分）。"""
    return DIM_ALIASES.get(_s(raw).lower())


def canon_category(raw: Any) -> str:
    return CHALLENGE_CATEGORY.get(_s(raw).lower(), "solution")


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        return [str(v) for v in value.values() if v]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    return [str(value)]


# ---------------------------------------------------------------- 立场

@dataclass
class Stance:
    """一个智能体在某个议题上的立场陈述。"""

    agent_id: str
    position: str = "neutral"
    claim: str = ""
    source_refs: list[str] = field(default_factory=list)
    quote_ref: str = ""
    topic_id: str = ""

    @classmethod
    def from_any(cls, raw: dict[str, Any], topic_id: str = "") -> "Stance":
        if isinstance(raw, Stance):
            return raw
        raw = raw or {}
        claim = raw.get("claim") or raw.get("proposal") or raw.get("statement") \
            or raw.get("text") or raw.get("opinion") or ""
        refs = raw.get("source_refs")
        if not refs:
            refs = raw.get("evidence_refs") or raw.get("sources") or raw.get("evidence")
        return cls(
            agent_id=_s(raw.get("agent_id") or raw.get("agent") or raw.get("speaker")
                        or raw.get("scorer") or "unknown"),
            position=canon_position(raw.get("position") or raw.get("stance")
                                    or raw.get("attitude")),
            claim=_s(claim),
            source_refs=_as_list(refs),
            quote_ref=_s(raw.get("quote_ref") or raw.get("stance_id") or raw.get("id")),
            topic_id=_s(raw.get("topic_id") or topic_id),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "topic_id": self.topic_id,
            "position": self.position,
            "claim": self.claim,
            "source_refs": list(self.source_refs),
            "quote_ref": self.quote_ref,
        }

    @property
    def has_evidence(self) -> bool:
        return bool(self.source_refs)


# ---------------------------------------------------------------- 冲突

@dataclass
class Conflict:
    """一条冲突登记；status: open | resolved | suspended。"""

    conflict_id: str
    topic_id: str
    stances: list[Stance] = field(default_factory=list)
    issue_id: str = ""
    level: str | None = None
    status: str = "open"
    resolution: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "issue_id": self.issue_id,
            "topic_id": self.topic_id,
            "stances": [s.to_dict() for s in self.stances],
            "level": self.level,
            "status": self.status,
            "resolution": dict(self.resolution),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Conflict":
        raw = raw or {}
        topic = _s(raw.get("topic_id") or raw.get("issue_id"))
        return cls(
            conflict_id=_s(raw.get("conflict_id")),
            topic_id=topic,
            stances=[Stance.from_any(s, topic) for s in (raw.get("stances") or [])],
            issue_id=_s(raw.get("issue_id")),
            level=raw.get("level"),
            status=_s(raw.get("status") or "open"),
            resolution=raw.get("resolution") or {},
        )


# ---------------------------------------------------------------- 质疑（四要素）

@dataclass
class Challenge:
    """对抗式评审提的一条质疑。

    四要素（缺一即退回重写，不许进评审表）：
        1. description  具体问题是什么（≥8 字，不许「整体不太好」）
        2. impact_ref   质疑对象，必须带 ID（TASK-xxx / FEA-xxx）
        3. consequence  不改会有什么后果
        4. category+severity  归到方案层还是需求层、严重度
    """

    challenge_id: str = ""
    description: str = ""
    impact_ref: str = ""
    consequence: str = ""
    severity: str = "major"
    category: str = "solution"
    verdict: str = "accepted"

    @classmethod
    def from_any(cls, raw: dict[str, Any] | "Challenge", seq: int = 1) -> "Challenge":
        if isinstance(raw, Challenge):
            return raw
        raw = raw or {}
        severity = _s(raw.get("severity") or "major").lower()
        return cls(
            challenge_id=_s(raw.get("challenge_id") or raw.get("id") or "CHG-%03d" % seq),
            description=_s(raw.get("description") or raw.get("claim") or raw.get("issue")),
            impact_ref=_s(raw.get("impact_ref") or raw.get("target") or raw.get("impact")),
            consequence=_s(raw.get("consequence") or raw.get("impact_text")),
            severity=severity if severity in SEVERITIES else "major",
            category=canon_category(raw.get("category") or raw.get("target_kind")),
            verdict=_s(raw.get("verdict") or "accepted").lower(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "challenge_id": self.challenge_id,
            "description": self.description,
            "impact_ref": self.impact_ref,
            "consequence": self.consequence,
            "severity": self.severity,
            "category": self.category,
            "verdict": self.verdict,
        }


# ---------------------------------------------------------------- 评分单

@dataclass
class ScoreSheet:
    """四维打分结果（四维计分 + 成本仅估算）。

    evidence / unavailable / reviewed_scope 是「评审依据」，不是装饰：
    - evidence[d]      该维分数由什么算出来（评审单上必须写，不能只有分数）
    - unavailable      哪一维这次算不出来、为什么（不补 0，不假装评过）
    - reviewed_scope   这次审了哪些功能点 / 采纳了哪几套方案 / 有几张任务卡
    """

    score_id: str
    scores: dict[str, float]
    weighted_total: float
    stddev: float
    veto: bool = False
    veto_items: list[dict[str, Any]] = field(default_factory=list)
    challenges: list[Challenge] = field(default_factory=list)
    passed: bool = False
    reasons: list[str] = field(default_factory=list)
    rework_target: str | None = None
    score_origin: str = "input"
    evidence: dict[str, str] = field(default_factory=dict)
    unavailable: list[dict[str, str]] = field(default_factory=list)
    reviewed_scope: dict[str, Any] = field(default_factory=dict)
    triage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score_id": self.score_id,
            "scores": {k: v for k, v in self.scores.items()},
            "weighted_total": self.weighted_total,
            "stddev": self.stddev,
            "veto": self.veto,
            "veto_items": [dict(v) for v in self.veto_items],
            "challenges": [c.to_dict() for c in self.challenges],
            "pass": self.passed,
            "reasons": list(self.reasons),
            "rework_target": self.rework_target,
            "score_origin": self.score_origin,
            "evidence": dict(self.evidence),
            "unavailable": [dict(u) for u in self.unavailable],
            "reviewed_scope": dict(self.reviewed_scope),
            "triage": dict(self.triage),
        }

    def to_review_sheet_data(self, scorer_map: dict[str, str] | None = None) -> dict[str, Any]:
        """转成 A 的 gate_check / mocks 认得的形状。

        gate_check.score_metrics 读：weighted_total / stddev / veto / scores[].dimension+score。
        scores[].scorer / reason 只给报告看：谁打的分、凭什么。

        打分人是按角色分派的，不是随便填一个人：
        A2 用户代表管完整性（功能是否都有人接）、A3 架构师管可行性、
        A4 质量评审员管风险、A5 PM 管验收可测性；A1 是被审方案作者，回避。
        """
        scorer_map = {**DIM_SCORER, **(scorer_map or {})}
        dim_score = [
            {
                "dimension": dim,
                "scorer": scorer_map.get(dim, "A2"),
                "score": value,
                "weight": DIM_WEIGHTS.get(dim),
                "reason": self.evidence.get(dim, ""),
            }
            for dim, value in self.scores.items()
        ]
        blocking = sum(1 for v in self.veto_items if v.get("severity") == "blocking")
        return {
            "score_id": self.score_id,
            "scores": dim_score,
            "weighted_total": self.weighted_total,
            "stddev": self.stddev,
            "veto": self.veto,
            # 门禁对任何否决项都是一票否决（gate() 里 `if veto or veto_items`），
            # 所以没有 blocking 级时 blocker_count 按否决项总条数计，别让它读成 0。
            "blocker_count": blocking or len(self.veto_items),
            "veto_count": len(self.veto_items),
            "veto_items": [dict(v) for v in self.veto_items],
            "challenges": [c.to_dict() for c in self.challenges],
            "rework_target": self.rework_target,
            "pass": self.passed,
            "reasons": list(self.reasons),
            "score_origin": self.score_origin,
            "evidence": dict(self.evidence),
            "unavailable": [dict(u) for u in self.unavailable],
            "reviewed_scope": dict(self.reviewed_scope),
            "triage": dict(self.triage),
        }
