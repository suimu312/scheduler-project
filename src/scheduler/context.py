"""统一数据契约与运行时对象。

对应 docs/architecture.md：
  §0.4 公共信封（ArtifactMeta / BudgetLedger / StageContext / StageResult / RunState / RunEvent / GuardSignal）
  §2.1 RunConfig / §2.2 RunResult
本文件是编排引擎与 B/C/D/E/F 五个模块之间唯一的数据形状来源，任何字段变更先改文档 §0 再改这里。
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0"
TZ = datetime.timezone(datetime.timedelta(hours=8))

# ---------------------------------------------------------------- 枚举（§0.2）

STAGES: tuple[str, ...] = ("P1", "P2", "P3", "P4", "P5", "P6", "P7")
CONTROL_PHASES: tuple[str, ...] = ("HUMAN_GATE", "BASELINE_FROZEN", "ABORTED")
ALL_PHASES: tuple[str, ...] = STAGES + CONTROL_PHASES

AGENT_IDS: tuple[str, ...] = ("A1", "A2", "A3", "A4", "A5", "A6")
AGENT_FAMILY: dict[str, str] = {
    "A1": "deliberative",
    "A2": "deliberative",
    "A3": "deliberative",
    "A4": "deliberative",
    "A5": "processing",
    "A6": "processing",
}

RUN_STATUS = ("running", "waiting_human", "frozen", "escalated", "aborted")
GATE_ACTIONS = ("advance", "stay", "rework", "escalate", "wait_human", "abort", "freeze")
TERMINATION_REASONS = (
    "baseline_signed",
    "conflict_no_open",
    "max_rounds",
    "budget_exhausted",
    "human_abort",
    "rework_limit",
    "error",
)
ERROR_CODES = (
    "E_ARTIFACT_MISSING",
    "E_SCHEMA_INVALID",
    "E_GATE_UNSATISFIED",
    "E_BUDGET_EXHAUSTED",
    "E_AGENT_UNAVAILABLE",
    "E_HUMAN_TIMEOUT",
)

# 模块归属（§1.3）
MODULE_OWNERS: dict[str, str] = {
    "P1": "scheduler",
    "P2": "agents",
    "P3": "agents",
    "P4": "solution_gen",
    "P5": "conflict_review",
    "P6": "req_builder",
    "P7": "baseline_mgr",
}

# 每个阶段产出的默认 artifact 名（§3.3）
STAGE_OUTPUTS: dict[str, list[str]] = {
    "P1": ["idea_card"],
    "P2": ["problem_list", "convocation_table"],
    "P3": ["discussion_minutes", "conflict_register"],
    "P4": ["solution_comparison", "solutions"],
    "P5": ["review_sheet", "conflict_register"],
    "P6": ["requirement_spec", "coverage_matrix"],
    "P7": ["opinion_handling", "confirmation_sheet", "baseline"],
}


# ---------------------------------------------------------------- 基础工具

def now_iso() -> str:
    """ISO 8601（+08:00），与产物 meta.created_at 口径一致。"""
    return datetime.datetime.now(TZ).replace(microsecond=0).isoformat()


def new_run_id(seq: int = 1) -> str:
    return f"R{seq:04d}"


_EVT_RE = re.compile(r"EVT-(\d{5})")


def next_event_id(last_event_id: str | None) -> str:
    if not last_event_id:
        return "EVT-00001"
    m = _EVT_RE.search(last_event_id)
    n = int(m.group(1)) + 1 if m else 1
    return f"EVT-{n:05d}"


# ---------------------------------------------------------------- 产物信封（§0.4）

def make_artifact(
    data: dict[str, Any],
    run_id: str,
    stage: str,
    produced_by: str,
    extensions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """所有产物的唯一造壳入口：{meta, data, extensions}。"""
    return {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "stage": stage,
            "produced_by": produced_by,
            "created_at": now_iso(),
        },
        "data": data,
        "extensions": extensions or {},
    }


def validate_artifact(artifact: Any, expected_stage: str | None = None) -> list[str]:
    """校验 §0.4 信封 + C5（meta 必须齐全、schema_version == "1.0"）。返回错误列表，空列表即通过。"""
    errors: list[str] = []
    if not isinstance(artifact, dict):
        return [f"E_SCHEMA_INVALID: 产物必须是 object，实际 {type(artifact).__name__}"]
    for key in ("meta", "data"):
        if key not in artifact:
            errors.append(f"E_SCHEMA_INVALID: 缺 {key}")
    meta = artifact.get("meta") or {}
    if not isinstance(meta, dict):
        return errors + ["E_SCHEMA_INVALID: meta 必须是 object"]
    for key in ("schema_version", "run_id", "stage", "produced_by", "created_at"):
        if key not in meta or meta.get(key) in (None, ""):
            errors.append(f"E_SCHEMA_INVALID: meta.{key} 缺失")
    if meta.get("schema_version") not in (None, SCHEMA_VERSION):
        errors.append(f"E_SCHEMA_INVALID: schema_version 必须为 {SCHEMA_VERSION}")
    if expected_stage and meta.get("stage") not in (None, expected_stage):
        errors.append(f"E_SCHEMA_INVALID: meta.stage={meta.get('stage')} 与阶段 {expected_stage} 不符")
    if not isinstance(artifact.get("data"), (dict, list)):
        errors.append("E_SCHEMA_INVALID: data 必须是 object/array")
    if "extensions" in artifact and not isinstance(artifact["extensions"], dict):
        errors.append("E_SCHEMA_INVALID: extensions 必须是 object")
    return errors


# ---------------------------------------------------------------- 预算台账（§7）

@dataclass
class BudgetLedger:
    global_limit: int = 30
    global_used: int = 0
    per_stage_limit: dict[str, int] = field(default_factory=dict)
    per_stage_used: dict[str, int] = field(default_factory=dict)
    rounds: dict[str, int] = field(default_factory=dict)
    rework_count: int = 0
    p5_revision_count: int = 0

    # -- 计费 --
    def charge(self, stage: str, used: int) -> int:
        used = int(used or 0)
        self.global_used += used
        self.per_stage_used[stage] = self.per_stage_used.get(stage, 0) + used
        return self.global_used

    def limit_of(self, stage: str) -> int | None:
        return self.per_stage_limit.get(stage)

    def used_of(self, stage: str) -> int:
        return self.per_stage_used.get(stage, 0)

    def exhausted(self) -> bool:
        """全局预算耗尽：这是硬终止条件（T_BUDGET）。"""
        return self.global_used >= self.global_limit

    def stage_exhausted(self, stage: str) -> bool:
        """单阶段预算用超了：只让「这个阶段」停下来，不等于整轮终止（§7.2）。"""
        limit = self.per_stage_limit.get(stage)
        # P1 的单阶段上限是 0（纯人工阶段不调模型），0 表示「不许调」，不是「已耗尽」
        return bool(limit and limit > 0 and self.used_of(stage) >= limit)

    def round_of(self, stage: str) -> int:
        return self.rounds.get(stage, 0)

    def bump_round(self, stage: str) -> int:
        self.rounds[stage] = self.round_of(stage) + 1
        return self.rounds[stage]

    def to_dict(self) -> dict[str, Any]:
        return {
            "global_limit": self.global_limit,
            "global_used": self.global_used,
            "per_stage_limit": dict(self.per_stage_limit),
            "per_stage_used": dict(self.per_stage_used),
            "rounds": dict(self.rounds),
            "rework_count": self.rework_count,
            "p5_revision_count": self.p5_revision_count,
        }

    @classmethod
    def from_dict(cls, obj: dict[str, Any] | None) -> "BudgetLedger":
        obj = obj or {}
        return cls(
            global_limit=int(obj.get("global_limit", 30)),
            global_used=int(obj.get("global_used", 0)),
            per_stage_limit=dict(obj.get("per_stage_limit") or {}),
            per_stage_used=dict(obj.get("per_stage_used") or {}),
            rounds=dict(obj.get("rounds") or {}),
            rework_count=int(obj.get("rework_count", 0)),
            p5_revision_count=int(obj.get("p5_revision_count", 0)),
        )


# ---------------------------------------------------------------- 状态机输入输出

@dataclass
class GuardSignal:
    """B/D 产生的防退化信号；A 只消费，不解释语义（§6.2）。"""

    signal: str
    agent_id: str | None = None
    value: float | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"signal": self.signal, "agent_id": self.agent_id, "value": self.value, "reason": self.reason}


@dataclass
class RunEvent:
    event_id: str
    run_id: str
    ts: str
    stage: str
    event_type: str
    actor: str
    payload_ref: str = ""
    budget_used: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "ts": self.ts,
            "stage": self.stage,
            "event_type": self.event_type,
            "actor": self.actor,
            "payload_ref": self.payload_ref,
            "budget_used": self.budget_used,
        }


@dataclass
class StageContext:
    """A → 模块的唯一输入（§8.1）。artifacts / ledger 对模块只读。"""

    run_id: str
    stage: str
    round_index: int
    dispatch_plan: dict[str, Any] | None
    artifacts: dict[str, Any]
    config: dict[str, Any]
    ledger: BudgetLedger
    constraints: list[str] = field(default_factory=list)
    human_decisions: list[dict[str, Any]] = field(default_factory=list)

    def artifact_data(self, name: str) -> dict[str, Any]:
        art = self.artifacts.get(name) or {}
        return art.get("data") or {}


@dataclass
class StageResult:
    """模块 → A 的唯一输出（§8.1）。"""

    stage: str
    status: str = "ok"  # ok | blocked | need_human | failed
    artifacts: dict[str, Any] = field(default_factory=dict)
    events: list[RunEvent] = field(default_factory=list)
    guard_signals: list[GuardSignal] = field(default_factory=list)
    budget_used: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def error_codes(self) -> list[str]:
        return [e.get("code", "") for e in self.errors]


# ---------------------------------------------------------------- 配置（§2.1）

DEFAULT_BUDGET: dict[str, Any] = {
    "global_llm_calls": 30,
    "per_stage": {"P1": 0, "P2": 4, "P3": 12, "P4": 6, "P5": 8, "P6": 4, "P7": 2},
}

DEFAULT_LIMITS: dict[str, Any] = {
    "max_discussion_rounds": 3,
    "max_p5_revisions": 3,
    "max_reworks": 2,
    "max_supplements_per_round": 3,
    "max_supplement_speakers": 2,
    "supplement_max_chars": 80,
    "similarity_threshold": 0.85,
    "score_pass": 80,
    "score_stddev_max": 12,
    "idea_min_chars": 10,
    "challenge_reject_rate": 0.6,
    "max_steps": 60,
}


@dataclass
class RunConfig:
    run_id: str
    idea_text: str
    author: str = "user"
    flow_ref: str = "flow.yaml"
    gate_ref: str = "gate_rule.yaml"
    agent_ref: str = "../agents/agent_config.yaml"
    budget: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_BUDGET))
    limits: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    seed: int = 42
    run_root: str = "runs"
    provider: str = "mock"  # mock | real：决定模块来源（mock 用于离线跑通状态机）

    @property
    def global_llm_calls(self) -> int:
        return int(self.budget.get("global_llm_calls", 30))

    @property
    def per_stage_budget(self) -> dict[str, int]:
        return {k: int(v) for k, v in (self.budget.get("per_stage") or {}).items()}

    def limit(self, key: str, default: Any = None) -> Any:
        return self.limits.get(key, DEFAULT_LIMITS.get(key, default))

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> "RunConfig":
        budget = {**DEFAULT_BUDGET, **(obj.get("budget") or {})}
        budget["per_stage"] = {**DEFAULT_BUDGET["per_stage"], **(obj.get("budget", {}).get("per_stage") or {})}
        limits = {**DEFAULT_LIMITS, **(obj.get("limits") or {})}
        return cls(
            run_id=str(obj["run_id"]),
            idea_text=str(obj.get("idea_text", "")),
            author=str(obj.get("author", "user")),
            flow_ref=str(obj.get("flow_ref", "flow.yaml")),
            gate_ref=str(obj.get("gate_ref", "gate_rule.yaml")),
            agent_ref=str(obj.get("agent_ref", "../agents/agent_config.yaml")),
            budget=budget,
            limits=limits,
            seed=int(obj.get("seed", 42)),
            run_root=str(obj.get("run_root", "runs")),
            provider=str(obj.get("provider", "mock")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "idea_text": self.idea_text,
            "author": self.author,
            "flow_ref": self.flow_ref,
            "gate_ref": self.gate_ref,
            "agent_ref": self.agent_ref,
            "budget": self.budget,
            "limits": self.limits,
            "seed": self.seed,
            "run_root": self.run_root,
            "provider": self.provider,
        }


# ---------------------------------------------------------------- 运行状态（§3.4）

@dataclass
class RunState:
    run_id: str
    status: str = "running"
    phase: str = "P1"
    round_index: int = 1
    artifacts_index: dict[str, str] = field(default_factory=dict)
    ledger: BudgetLedger = field(default_factory=BudgetLedger)
    constraints: list[str] = field(default_factory=list)
    last_gate_verdict: dict[str, Any] | None = None
    human_decisions: list[dict[str, Any]] = field(default_factory=list)
    guard_signals: list[dict[str, Any]] = field(default_factory=list)
    pending: dict[str, Any] = field(default_factory=dict)
    termination_reason: str | None = None
    updated_at: str = field(default_factory=now_iso)

    def touch(self) -> None:
        self.updated_at = now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "phase": self.phase,
            "round_index": self.round_index,
            "artifacts_index": dict(self.artifacts_index),
            "ledger": self.ledger.to_dict(),
            "constraints": list(self.constraints),
            "last_gate_verdict": self.last_gate_verdict,
            "human_decisions": list(self.human_decisions),
            "guard_signals": list(self.guard_signals),
            "pending": dict(self.pending),
            "termination_reason": self.termination_reason,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> "RunState":
        return cls(
            run_id=str(obj["run_id"]),
            status=str(obj.get("status", "running")),
            phase=str(obj.get("phase", "P1")),
            round_index=int(obj.get("round_index", 1)),
            artifacts_index=dict(obj.get("artifacts_index") or {}),
            ledger=BudgetLedger.from_dict(obj.get("ledger")),
            constraints=list(obj.get("constraints") or []),
            last_gate_verdict=obj.get("last_gate_verdict"),
            human_decisions=list(obj.get("human_decisions") or []),
            guard_signals=list(obj.get("guard_signals") or []),
            pending=dict(obj.get("pending") or {}),
            termination_reason=obj.get("termination_reason"),
            updated_at=str(obj.get("updated_at") or now_iso()),
        )


# ---------------------------------------------------------------- 阶段结果校验

def validate_stage_result(
    result: StageResult,
    expected_outputs: list[str],
    run_id: str,
    stage: str,
) -> list[str]:
    """校验模块交上来的 StageResult（§8.4：缺产物 / 缺 meta / 阶段不符 → E_SCHEMA_INVALID）。"""
    errors: list[str] = []
    if result.status not in ("ok", "blocked", "need_human", "failed"):
        errors.append(f"E_SCHEMA_INVALID: 非法 status={result.status}")
    if result.status != "ok":
        return errors
    for name in expected_outputs:
        if name not in result.artifacts:
            errors.append(f"E_ARTIFACT_MISSING: 缺产物 {name}")
            continue
        art = result.artifacts[name]
        errors.extend(validate_artifact(art, expected_stage=stage))
        meta = (art.get("meta") or {}) if isinstance(art, dict) else {}
        if meta.get("run_id") not in (None, run_id):
            errors.append(f"E_SCHEMA_INVALID: {name}.meta.run_id={meta.get('run_id')} 与运行实例不符")
    return errors
