"""F 模块业务函数：确认单、基线冻结、人工意见与变更后变更请求。

本文件只处理数据，不写 runs/、不判状态机；统一入口见 stage.py。
"""

from __future__ import annotations

import datetime
from typing import Any

TZ = datetime.timezone(datetime.timedelta(hours=8))


def _iso() -> str:
    return datetime.datetime.now(TZ).replace(microsecond=0).isoformat()


def _data(artifacts: dict[str, Any], name: str) -> dict[str, Any]:
    item = artifacts.get(name)
    if not isinstance(item, dict):
        return {}
    data = item.get("data")
    return data if isinstance(data, dict) else item


def _sign_decision(ctx: Any) -> dict[str, Any]:
    decisions = getattr(ctx, "human_decisions", None) or []
    for decision in reversed(list(decisions)):
        if isinstance(decision, dict) and str(decision.get("action")) == "sign":
            return decision
    return {}


def build_confirmation_sheet(ctx: Any) -> dict[str, Any]:
    artifacts = getattr(ctx, "artifacts", {}) or {}
    dev_task_list = _data(artifacts, "dev_task_list")
    schedule = _data(artifacts, "schedule")
    coverage = _data(artifacts, "coverage_matrix") or _data(artifacts, "task_coverage")
    review = _data(artifacts, "review_sheet")
    sheet_data = {
        "run_id": getattr(ctx, "run_id", ""),
        "summary": {
            "total_tasks": len(dev_task_list.get("tasks") or []),
            "total_waves": len(schedule.get("waves") or []),
            "total_days": schedule.get("total_days", 0),
            "critical_path": (schedule.get("critical_path") or {}).get("task_ids") or [],
        },
        "tasks": dev_task_list.get("tasks") or [],
        "schedule": schedule,
        "coverage_status": {
            "coverage_rate": coverage.get("coverage_rate", 0),
            "gap_count": coverage.get("gap_count", 0),
        },
        "review_opinions": review.get("challenges") or [],
        "signature_block": {"required": True, "hint": "请输入签字理由"},
    }
    return {"artifacts": {"confirmation_sheet": sheet_data}, "events": [], "usage": 0}


def freeze_baseline(ctx: Any) -> dict[str, Any]:
    artifacts = getattr(ctx, "artifacts", {}) or {}
    decision = _sign_decision(ctx)
    dev_task_list = _data(artifacts, "dev_task_list")
    coverage = _data(artifacts, "coverage_matrix") or _data(artifacts, "task_coverage")
    requirement_spec = _data(artifacts, "requirement_spec")
    schedule = _data(artifacts, "schedule")
    if int(coverage.get("gap_count") or 0) > 0:
        raise ValueError("E_SCHEMA_INVALID: 存在覆盖空缺，不允许冻结基线")
    tasks = dev_task_list.get("tasks") or []
    for task in tasks:
        if not task.get("acceptance"):
            raise ValueError("E_SCHEMA_INVALID: 任务 %s 缺失验收标准" % task.get("task_id"))
    baseline_data = {
        "version": "v1.0",
        "status": "frozen" if decision else "pending",
        "task_ids": [t.get("task_id") for t in tasks if t.get("task_id")],
        "requirement_ids": [
            r.get("req_id") for r in (requirement_spec.get("requirements") or []) if isinstance(r, dict) and r.get("req_id")
        ],
        "schedule_ref": f"schedule:{len(schedule.get('waves') or [])}",
        "total_days": schedule.get("total_days"),
        "critical_path": (schedule.get("critical_path") or {}).get("task_ids") or [],
        "signed_by": str(decision.get("decided_by") or "user") if decision else None,
        "signed_at": str(decision.get("decided_at") or _iso()) if decision else None,
        "source_refs": ["dev_task_list:v1.0", "schedule:v1.0", "confirmation_sheet:v1.0"],
        "frozen": bool(decision),
    }
    return {"artifacts": {"baseline": baseline_data}, "events": [], "usage": 0}


def apply_human_decision(decision: dict[str, Any]) -> dict[str, Any]:
    comment = str(decision.get("reason") or decision.get("comment") or "")
    opinion_data = {
        "opinions": [{
            "opinion_id": str(decision.get("decision_id") or "OPN-001"),
            "source_ref": str(decision.get("payload_ref") or "confirmation_sheet:v1.0"),
            "comment": comment,
            "decision": "adopt" if str(decision.get("action")) == "sign" else "pending",
            "action_taken": "打回 P3 补质询/P6 重拆任务" if str(decision.get("action")) == "reject" else "留痕",
            "handled_by": "A6",
        }],
        "note": "P5 意见在 P7 由 F 汇总处理（通过/驳回/待定）",
    }
    return {"artifacts": {"opinion_handling": opinion_data}, "events": [], "usage": 0}


def create_change_request(cr_input: dict[str, Any]) -> dict[str, Any]:
    cr_data = {
        "cr_id": str(cr_input.get("cr_id") or "CR-001"),
        "from_version": str(cr_input.get("from_version") or "v1.0"),
        "reason": str(cr_input.get("reason") or ""),
        "impact_analysis": {
            "affected_tasks": cr_input.get("affected_tasks") or [],
            "new_tasks": cr_input.get("new_tasks") or [],
            "estimated_effort_hours": 8,
        },
        "review_result": "pending",
        "to_version": None,
    }
    return {"artifacts": {"change_request": cr_data}, "events": [], "usage": 0}
