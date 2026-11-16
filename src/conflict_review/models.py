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
    """五维打分结果（四维计分 + 成本仅估算）。"""

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
        }

    def to_review_sheet_data(self, scorer_map: dict[str, str] | None = None) -> dict[str, Any]:
        """转成 A 的 gate_check / mocks 认得的形状。

        gate_check.score_metrics 读：weighted_total / stddev / veto / scores[].dimension+score。
        """
        scorer_map = scorer_map or {}
        dim_score = [
            {
                "dimension": dim,
                "scorer": scorer_map.get(dim, "A2"),
                "score": value,
                "reason": "",
            }
            for dim, value in self.scores.items()
        ]
        return {
            "score_id": self.score_id,
            "scores": dim_score,
            "weighted_total": self.weighted_total,
            "stddev": self.stddev,
            "veto": self.veto,
            "blocker_count": sum(1 for v in self.veto_items if v.get("severity") == "blocking")
            or len(self.veto_items),
            "veto_items": [dict(v) for v in self.veto_items],
            "challenges": [c.to_dict() for c in self.challenges],
            "rework_target": self.rework_target,
            "pass": self.passed,
            "reasons": list(self.reasons),
        }
