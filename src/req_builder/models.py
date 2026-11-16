"""P6 任务卡与覆盖矩阵的内部数据模型。

内部模型用富结构（对象），对外产物按 docx 表 14 的八字段拍平成 JSON，
序列化集中在 DevTask.to_dict() 一处，避免「内部一套、产物一套」各写一遍。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PRIORITIES = ("must", "should", "could", "wont")
PRIORITY_LABEL = {"must": "M", "should": "S", "could": "C", "wont": "W"}
SOURCE_KINDS = ("feature", "constraint", "conflict", "challenge")
COVERAGE_STATUS = ("covered", "gap")


def priority_rank(priority: str) -> int:
    """数值越大越优先；用于排序与资源冲突保留。"""
    return {"must": 4, "should": 3, "could": 2, "wont": 1}.get(priority, 0)


@dataclass
class SourceRef:
    """来源引用：kind + ref_id 归一成 "产物名:ID" 字符串。"""

    kind: str
    ref_id: str
    quote: str = ""
    relation: str = "direct"  # direct / derived / constraint

    ARTIFACT_OF = {
        "feature": "solution_comparison",
        "constraint": "design_spec",
        "conflict": "conflict_register",
        "challenge": "review_sheet",
    }

    def to_string(self) -> str:
        return f"{self.ARTIFACT_OF.get(self.kind, 'unknown')}:{self.ref_id}"

    @classmethod
    def from_string(cls, raw: str) -> "SourceRef":
        artifact, _, ref_id = str(raw).partition(":")
        kind = next(
            (k for k, v in cls.ARTIFACT_OF.items() if v == artifact), "feature"
        )
        return cls(kind=kind, ref_id=ref_id or artifact)

    @classmethod
    def from_any(cls, raw: Any) -> "SourceRef":
        if isinstance(raw, SourceRef):
            return raw
        if isinstance(raw, str):
            return cls.from_string(raw)
        if isinstance(raw, dict):
            return cls(
                kind=str(raw.get("kind") or raw.get("source_type") or "feature"),
                ref_id=str(raw.get("ref_id") or raw.get("source_id") or ""),
                quote=str(raw.get("quote") or ""),
                relation=str(raw.get("relation") or "direct"),
            )
        raise TypeError(f"无法识别的 SourceRef: {raw!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ref_id": self.ref_id,
            "quote": self.quote,
            "relation": self.relation,
            "ref": self.to_string(),
        }


@dataclass
class AcceptanceCriterion:
    ac_id: str
    given: str
    when: str
    then: str
    kind: str = "functional"
    metric: dict[str, Any] | None = None

    @classmethod
    def from_any(cls, raw: Any, task_id: str, seq: int) -> "AcceptanceCriterion":
        if isinstance(raw, AcceptanceCriterion):
            return raw
        if isinstance(raw, str):
            # 产物里允许直接写一句话验收口径："操作之后看到什么结果"
            return cls(
                ac_id=f"AC-{task_id}-{seq}",
                given="",
                when="",
                then=raw.strip(),
                kind="functional",
            )
        if isinstance(raw, dict):
            return cls(
                ac_id=str(raw.get("ac_id") or f"AC-{task_id}-{seq}"),
                given=str(raw.get("given") or ""),
                when=str(raw.get("when") or ""),
                then=str(raw.get("then") or ""),
                kind=str(raw.get("type") or raw.get("kind") or "functional"),
                metric=raw.get("metric") if isinstance(raw.get("metric"), dict) else None,
            )
        raise TypeError(f"无法识别的验收口径: {raw!r}")

    def text(self) -> str:
        if self.when or self.given:
            parts = []
            if self.given:
                parts.append(f"Given {self.given}")
            if self.when:
                parts.append(f"When {self.when}")
            if self.then:
                parts.append(f"Then {self.then}")
            return "；".join(parts)
        return self.then

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ac_id": self.ac_id,
            "given": self.given,
            "when": self.when,
            "then": self.then,
            "type": self.kind,
        }
        if self.metric:
            out["metric"] = dict(self.metric)
        return out


@dataclass
class DevTask:
    """开发任务卡：docx 表 14 的八字段 + 优先级理由、状态。"""

    task_id: str
    module_id: str
    title: str
    depends_on: list[str] = field(default_factory=list)
    estimate_days: float = 1.0
    owner_role: str = ""
    acceptance: list[AcceptanceCriterion] = field(default_factory=list)
    priority: str = "should"
    priority_reason: str = ""
    priority_source: str = "rule"
    source_refs: list[SourceRef] = field(default_factory=list)
    status: str = "pending"

    @classmethod
    def from_dict(cls, raw: dict[str, Any], index: int = 1) -> "DevTask":
        task_id = str(raw.get("task_id") or raw.get("id") or f"DEV-{index:03d}")
        acceptance_raw = raw.get("acceptance") or raw.get("acceptance_criteria") or []
        if isinstance(acceptance_raw, str):
            acceptance_raw = [acceptance_raw]
        acceptance = [
            AcceptanceCriterion.from_any(item, task_id, seq)
            for seq, item in enumerate(acceptance_raw, start=1)
        ]
        deps = raw.get("depends_on") or raw.get("dependencies") or []
        if isinstance(deps, str):
            deps = [deps]
        refs_raw = raw.get("source_refs") or raw.get("sources") or []
        if isinstance(refs_raw, (str, dict)):
            refs_raw = [refs_raw]
        return cls(
            task_id=task_id,
            module_id=str(raw.get("module_id") or raw.get("module") or "MOD-000"),
            title=str(raw.get("title") or ""),
            depends_on=[str(d).strip() for d in deps if str(d).strip()],
            estimate_days=float(raw.get("estimate_days", raw.get("estimate", 1)) or 0),
            owner_role=str(raw.get("owner_role") or raw.get("owner") or ""),
            acceptance=acceptance,
            priority=str(raw.get("priority") or "should").lower(),
            priority_reason=str(raw.get("priority_reason") or ""),
            priority_source=str(raw.get("priority_source") or "rule"),
            source_refs=[SourceRef.from_any(r) for r in refs_raw],
            status=str(raw.get("status") or "pending"),
        )

    def to_dict(self) -> dict[str, Any]:
        """对外八字段 + 追溯与优先级证据（F 与可视化都要读）。"""
        return {
            "task_id": self.task_id,
            "module_id": self.module_id,
            "title": self.title,
            "depends_on": list(self.depends_on),
            "estimate_days": round(float(self.estimate_days), 2),
            "owner_role": self.owner_role,
            "acceptance": [ac.to_dict() for ac in self.acceptance],
            "priority": self.priority,
            "priority_reason": self.priority_reason,
            "priority_source": self.priority_source,
            "source_refs": [ref.to_string() for ref in self.source_refs],
            "status": self.status,
        }

    def acceptance_texts(self) -> list[str]:
        return [ac.text() for ac in self.acceptance if ac.text()]


@dataclass
class CoverageRow:
    source_type: str
    source_id: str
    source_ref: str
    covered_by: list[str] = field(default_factory=list)
    status: str = "gap"
    weight: int = 2
    title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_ref": self.source_ref,
            "title": self.title,
            "weight": self.weight,
            "covered_by": list(self.covered_by),
            "status": self.status,
        }


@dataclass
class Gap:
    kind: str
    target: str
    severity: str  # ERROR / WARN
    detail: str
    weight: int = 1
    rule: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target": self.target,
            "severity": self.severity,
            "detail": self.detail,
            "weight": self.weight,
            "rule": self.rule,
        }


@dataclass
class CoverageReport:
    rows: list[CoverageRow] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    r_source: float = 0.0
    r_task: float = 0.0
    coverage_rate: float = 0.0

    @property
    def gap_count(self) -> int:
        return sum(1 for row in self.rows if row.status == "gap")

    @property
    def error_count(self) -> int:
        return sum(1 for gap in self.gaps if gap.severity == "ERROR")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": [row.to_dict() for row in self.rows],
            "columns": list(self.columns),
            "gap_count": self.gap_count,
            "coverage_rate": round(self.coverage_rate, 3),
            "r_source": round(self.r_source, 3),
            "r_task": round(self.r_task, 3),
            "gaps": [gap.to_dict() for gap in self.gaps],
        }
