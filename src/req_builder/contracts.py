"""E 模块与编排器之间的信封适配层。

本文件只做三件事：
1. 复刻 architecture.md §0.4 的 StageContext / StageResult / RunEvent 信封，
   让 req_builder 在没有 A 的 scheduler 包时也能独立运行与自测；
2. 提供产物 meta 壳（make_artifact），保证落盘对象满足 C5「所有产物带 meta」；
3. 定义 E 侧错误码，避免魔法字符串。

注意：A 的 scheduler 发布后，这里只保留同名 dataclass 的字段顺序兼容，
import 路径由 `build_task_list` 的调用方注入，不在模块内部反向依赖 A。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

SCHEMA_VERSION = "1.0"
PRODUCER = "req_builder"
TZ = timezone(timedelta(hours=8))


class ErrorCode:
    """E 侧错误码（与 architecture.md §13 的 E_* 前缀一致）。"""

    ARTIFACT_MISSING = "E_ARTIFACT_MISSING"
    SCHEMA_INVALID = "E_SCHEMA_INVALID"
    DEP_CYCLE = "E_DEP_CYCLE"
    TASK_INVALID = "E_TASK_INVALID"
    COVERAGE_GAP = "E_COVERAGE_GAP"


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


# ---------------------------------------------------------------- meta 壳


def make_meta(run_id: str, stage: str, produced_by: str = PRODUCER) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "stage": stage,
        "produced_by": produced_by,
        "created_at": now_iso(),
    }


def make_artifact(
    data: dict[str, Any],
    run_id: str,
    stage: str = "P6",
    produced_by: str = PRODUCER,
    extensions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """套 meta 信封：{meta, data, extensions}。"""
    return {
        "meta": make_meta(run_id, stage, produced_by),
        "data": data,
        "extensions": extensions or {},
    }


def artifact_data(artifact: Any) -> dict[str, Any]:
    """容忍两种入参：完整信封（取 data）与裸数据（原样返回）。"""
    if isinstance(artifact, dict):
        data = artifact.get("data")
        if isinstance(data, dict):
            return data
        return artifact
    return {}


# ---------------------------------------------------------------- 事件


@dataclass
class RunEvent:
    """events.jsonl 的单行对象（只读，追加写）。

    seq 是重放的唯一依据：文件里显式给了就用，没给则按行号补 1..n。
    payload 允许两种来源：内联 payload 对象，或 payload_ref 指向的产物片段；
    渲染器只读 payload，不回溯产物文件，保证「界面是事件的投影」。
    """

    event_type: str
    event_id: str = ""
    seq: int = 0
    run_id: str = ""
    ts: str = ""
    stage: str = ""
    actor: str = ""
    budget_used: int = 0
    payload_ref: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], seq: int = 0) -> "RunEvent":
        payload = raw.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        # 允许字段平铺在行首（docx 表 17 的信号映射是按字段名列出的）
        for key, value in raw.items():
            if key in _EVENT_ENVELOPE_KEYS or key in payload:
                continue
            payload[key] = value
        return cls(
            event_type=str(raw.get("event_type") or raw.get("type") or "unknown"),
            event_id=str(raw.get("event_id", "")),
            seq=int(raw.get("seq", seq) or seq),
            run_id=str(raw.get("run_id", "")),
            ts=str(raw.get("ts", "")),
            stage=str(raw.get("stage", "")),
            actor=str(raw.get("actor", "")),
            budget_used=int(raw.get("budget_used", 0) or 0),
            payload_ref=str(raw.get("payload_ref", "")),
            payload=payload,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "seq": self.seq,
            "run_id": self.run_id,
            "ts": self.ts,
            "stage": self.stage,
            "event_type": self.event_type,
            "actor": self.actor,
            "budget_used": self.budget_used,
            "payload_ref": self.payload_ref,
            "payload": self.payload,
        }

    # 便捷取值：payload 优先，其次回落到信封字段
    def get(self, key: str, default: Any = None) -> Any:
        if key in self.payload:
            return self.payload[key]
        return getattr(self, key, default)


_EVENT_ENVELOPE_KEYS = {
    "event_id",
    "seq",
    "run_id",
    "ts",
    "stage",
    "event_type",
    "type",
    "actor",
    "budget_used",
    "payload_ref",
    "payload",
}


# ---------------------------------------------------------------- 上下文与结果


@dataclass
class StageContext:
    run_id: str
    stage: str = "P6"
    round_index: int = 1
    dispatch_plan: dict[str, Any] | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    ledger: dict[str, Any] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)

    @classmethod
    def from_any(cls, raw: Any) -> "StageContext":
        """接受 dict / 同构对象 / 已是 StageContext 的实例。"""
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, dict):
            return cls(
                run_id=str(raw.get("run_id", "R0001")),
                stage=str(raw.get("stage", "P6")),
                round_index=int(raw.get("round_index", 1) or 1),
                dispatch_plan=raw.get("dispatch_plan"),
                artifacts=dict(raw.get("artifacts") or {}),
                config=dict(raw.get("config") or {}),
                ledger=dict(raw.get("ledger") or {}),
                constraints=list(raw.get("constraints") or []),
            )
        return cls(
            run_id=str(getattr(raw, "run_id", "R0001")),
            stage=str(getattr(raw, "stage", "P6")),
            round_index=int(getattr(raw, "round_index", 1) or 1),
            dispatch_plan=getattr(raw, "dispatch_plan", None),
            artifacts=dict(getattr(raw, "artifacts", {}) or {}),
            config=dict(getattr(raw, "config", {}) or {}),
            ledger=dict(getattr(raw, "ledger", {}) or {}),
            constraints=list(getattr(raw, "constraints", []) or []),
        )


@dataclass
class StageResult:
    stage: str
    status: Literal["ok", "blocked", "need_human", "failed"] = "ok"
    artifacts: dict[str, Any] = field(default_factory=dict)
    events: list[RunEvent] = field(default_factory=list)
    guard_signals: list[dict[str, Any]] = field(default_factory=list)
    budget_used: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def add_error(self, code: str, message: str, **detail: Any) -> None:
        payload = {"code": code, "message": message}
        payload.update(detail)
        self.errors.append(payload)
        if code in (ErrorCode.ARTIFACT_MISSING, ErrorCode.TASK_INVALID, ErrorCode.DEP_CYCLE):
            self.status = "blocked"


def make_event(
    event_type: str,
    run_id: str,
    stage: str,
    actor: str,
    event_id: str = "",
    seq: int = 0,
    payload: dict[str, Any] | None = None,
    payload_ref: str = "",
    budget_used: int = 0,
) -> RunEvent:
    return RunEvent(
        event_type=event_type,
        event_id=event_id,
        seq=seq,
        run_id=run_id,
        ts=now_iso(),
        stage=stage,
        actor=actor,
        budget_used=budget_used,
        payload_ref=payload_ref,
        payload=payload or {},
    )
