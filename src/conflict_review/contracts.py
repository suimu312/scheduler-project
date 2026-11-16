# -*- coding: utf-8 -*-
"""D 模块与编排器之间的信封适配层。

本文件只做三件事（与 E 的 contracts.py 同构，全组口径一致）：

1. 复刻 A 的 scheduler.context 里的五个形状（产物信封 / RunEvent / GuardSignal /
   StageContext / StageResult），让 conflict_review 在没有 scheduler 包时也能独立
   运行与自测；
2. 提供产物 meta 壳（make_artifact），保证落盘对象满足「所有产物带 meta」硬规则，
   字段与 A 的 validate_artifact 逐字对齐，避免被 E_SCHEMA_INVALID 打回；
3. 提供对 dict / dataclass 两种 ctx 的容错读取，联调时 A 怎么传都能用。

依赖方向：只允许 D → A（运行期由 modules.resolve 注入），不反向 import scheduler。
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0"
PRODUCER = "conflict_review"
TZ = datetime.timezone(datetime.timedelta(hours=8))


class ErrorCode:
    """D 侧错误码，与 architecture.md §13 的 E_* 前缀一致。"""

    ARTIFACT_MISSING = "E_ARTIFACT_MISSING"
    SCHEMA_INVALID = "E_SCHEMA_INVALID"
    GATE_UNSATISFIED = "E_GATE_UNSATISFIED"
    AGENT_UNAVAILABLE = "E_AGENT_UNAVAILABLE"


def now_iso() -> str:
    """ISO 8601（+08:00），与产物 meta.created_at 口径一致（不带微秒）。"""
    return datetime.datetime.now(TZ).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- 产物信封

def make_artifact(
    data: dict[str, Any],
    run_id: str,
    stage: str,
    produced_by: str = PRODUCER,
    extensions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """所有产物的唯一造壳入口：{meta, data, extensions}。

    meta 五个字段一个都不能少：schema_version / run_id / stage / produced_by / created_at。
    """
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


# ---------------------------------------------------------------- ctx 容错读取

def ctx_get(ctx: Any, key: str, default: Any = None) -> Any:
    """同时支持 dict 与 dataclass 两种 ctx（A 传 StageContext，demo/test 传 dict）。"""
    if isinstance(ctx, dict):
        return ctx.get(key, default)
    return getattr(ctx, key, default)


def ctx_artifact_data(ctx: Any, name: str) -> dict[str, Any]:
    """读某个产物的 data 段。

    优先用 A 的 StageContext.artifact_data(name)；退化到手工解 dict，
    再退化到直接把裸 data 当产物（联调时常见）。
    """
    fn = getattr(ctx, "artifact_data", None)
    if callable(fn):
        try:
            data = fn(name)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    arts = ctx_get(ctx, "artifacts", {}) or {}
    if not isinstance(arts, dict):
        return {}
    art = arts.get(name)
    if isinstance(art, dict):
        inner = art.get("data")
        return inner if isinstance(inner, dict) else art
    return {}


def ctx_artifact(ctx: Any, name: str) -> dict[str, Any] | None:
    """取完整产物（含 meta），用于原样透传。"""
    arts = ctx_get(ctx, "artifacts", {}) or {}
    if isinstance(arts, dict) and isinstance(arts.get(name), dict):
        return arts[name]
    return None


# ---------------------------------------------------------------- 运行时对象

@dataclass
class RunEvent:
    """事件只描述「发生了什么」，event_id 由 A 的 EventEmitter 统一分配。"""

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
class GuardSignal:
    """防退化信号：A 只消费不解释语义（§6.2）。"""

    signal: str
    agent_id: str | None = None
    value: float | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal,
            "agent_id": self.agent_id,
            "value": self.value,
            "reason": self.reason,
        }


@dataclass
class StageContext:
    """A → 模块的唯一输入（§8.1），字段顺序与 A 的 context.StageContext 一致。"""

    run_id: str
    stage: str
    round_index: int = 1
    dispatch_plan: dict[str, Any] | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    ledger: Any = None
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


def make_event(run_id: str, stage: str, event_type: str, payload_ref: str = "") -> RunEvent:
    """造事件；event_id 故意留空，A 的 emitter 会重新编号。"""
    return RunEvent(
        event_id="",
        run_id=run_id,
        ts=now_iso(),
        stage=stage,
        event_type=event_type,
        actor=PRODUCER,
        payload_ref=payload_ref,
    )


def make_error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **extra}
