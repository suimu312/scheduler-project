"""F 模块与编排器的唯一接口：run(ctx) -> StageResult。

F 只出确认单、意见处理表与基线；排期由 A 在 P7 入口纯代码算好塞进 ctx，
本模块只读，不修改 schedule。
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any

from . import manager

SCHEMA_VERSION = "1.0"
TZ = datetime.timezone(datetime.timedelta(hours=8))


def _now() -> str:
    return datetime.datetime.now(TZ).replace(microsecond=0).isoformat()


def make_artifact(data: dict[str, Any], run_id: str, stage: str, name: str) -> dict[str, Any]:
    return {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "stage": stage,
            "produced_by": "baseline_mgr",
            "created_at": _now(),
        },
        "data": data,
        "extensions": {"artifact_name": name},
    }


@dataclass
class ModuleEvent:
    event_type: str
    actor: str = "baseline_mgr"
    payload_ref: str = ""


@dataclass
class StageResult:
    stage: str
    status: str = "ok"
    artifacts: dict[str, Any] = field(default_factory=dict)
    events: list[Any] = field(default_factory=list)
    guard_signals: list[dict[str, Any]] = field(default_factory=list)
    budget_used: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


def run(ctx: Any) -> StageResult:
    stage = str(getattr(ctx, "stage", "") or "")
    run_id = str(getattr(ctx, "run_id", "R0001"))
    if stage != "P7":
        return StageResult(
            stage=stage or "?",
            status="failed",
            errors=[{"code": "E_SCHEMA_INVALID", "message": f"baseline_mgr 只负责 P7，收到 {stage!r}"}],
        )

    result = StageResult(stage="P7")
    sheet = manager.build_confirmation_sheet(ctx)["artifacts"]["confirmation_sheet"]
    result.artifacts["confirmation_sheet"] = make_artifact(sheet, run_id, "P7", "confirmation_sheet")

    decisions = getattr(ctx, "human_decisions", None) or []
    last = decisions[-1] if decisions and isinstance(decisions[-1], dict) else {}
    opinion = manager.apply_human_decision(last)["artifacts"]["opinion_handling"]
    result.artifacts["opinion_handling"] = make_artifact(opinion, run_id, "P7", "opinion_handling")

    try:
        baseline = manager.freeze_baseline(ctx)["artifacts"]["baseline"]
    except ValueError as exc:
        result.status = "failed"
        result.errors.append({"code": "E_SCHEMA_INVALID", "message": str(exc)})
        return result
    result.artifacts["baseline"] = make_artifact(baseline, run_id, "P7", "baseline")
    result.events.append(ModuleEvent("human.decision", payload_ref="baseline"))
    return result
