import datetime
from typing import Dict, Any, List

def _wrap_artifact(data: dict, run_id: str, stage: str, produced_by: str) -> dict:
    return {
        "meta": {
            "schema_version": "1.0",
            "run_id": run_id,
            "stage": stage,
            "produced_by": produced_by,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat() + "Z"
        },
        "data": data
    }

def build_confirmation_sheet(ctx: Any) -> dict:
    artifacts = ctx.artifacts
    run_id = ctx.run_id
    dev_task_list = artifacts.get("dev_task_list", {}).get("data", {})
    schedule = artifacts.get("schedule", {}).get("data", {})
    coverage = artifacts.get("task_coverage", {}).get("data", {})
    review = artifacts.get("review_sheet", {}).get("data", {})
    sheet_data = {
        "summary": {
            "total_tasks": len(dev_task_list.get("tasks", [])),
            "total_waves": len(schedule.get("waves", [])),
            "total_days": schedule.get("total_days", 0),
        },
        "tasks": dev_task_list.get("tasks", []),
        "schedule": schedule,
        "coverage_status": {
            "coverage_rate": coverage.get("coverage_rate", 0),
            "gap_count": coverage.get("gap_count", 0)
        },
        "review_opinions": review.get("challenges", []),
        "signature_block": {"required": True, "hint": "请输入签字理由"}
    }
    artifact = _wrap_artifact(sheet_data, run_id, "P7", "F")
    return {
        "artifacts": {"confirmation_sheet": artifact},
        "events": [{"type": "human.decision", "payload": {"action": "awaiting_signature"}}],
        "usage": 0
    }

def freeze_baseline(ctx: Any) -> dict:
    artifacts = ctx.artifacts
    run_id = ctx.run_id
    human_decision = artifacts.get("HumanDecision", {})
    dev_task_list = artifacts.get("dev_task_list", {}).get("data", {})
    coverage = artifacts.get("task_coverage", {}).get("data", {})
    if coverage.get("gap_count", 0) > 0:
        raise ValueError("E_SCHEMA_INVALID: 存在覆盖空缺，不允许冻结基线")
    for task in dev_task_list.get("tasks", []):
        if not task.get("acceptance"):
            raise ValueError(f"E_SCHEMA_INVALID: 任务 {task.get(task_id)} 缺失验收标准")
    baseline_data = {
        "version": "v1.0",
        "status": "frozen",
        "task_ids": [t["task_id"] for t in dev_task_list.get("tasks", [])],
        "signed_by": human_decision.get("signed_by", "user"),
        "signed_at": datetime.datetime.now(datetime.timezone.utc).isoformat() + "Z",
        "source_refs": [
            "dev_task_list:v1.0", 
            "schedule:v1.0", 
            f"confirmation_sheet:{human_decision.get(decision_id, HD-001)}"
        ]
    }
    artifact = _wrap_artifact(baseline_data, run_id, "P7", "F")
    return {
        "artifacts": {"baseline": artifact},
        "events": [{"type": "human.decision", "payload": {"action": "approve", "baseline_version": "v1.0"}}],
        "usage": 0
    }

def apply_human_decision(decision: dict) -> dict:
    comment = decision.get("comment", "")
    source_ref = decision.get("source_ref", "confirmation_sheet:unknown")
    opinion_data = {
        "opinion_id": f"OPN-{int(datetime.datetime.now().timestamp())}",
        "source_ref": source_ref,
        "comment": comment,
        "decision": "adopt" if decision.get("action") == "reject" else "pending",
        "action_taken": "打回 P3 补质询/P6 重拆任务",
        "handled_by": "A6"
    }
    return {
        "artifacts": {"opinion_handling": opinion_data},
        "events": [{"type": "human.decision", "payload": {"action": "reject", "opinion_id": opinion_data["opinion_id"]}}]
    }

def create_change_request(cr_input: dict) -> dict:
    cr_id = cr_input.get("cr_id", "CR-001")
    cr_data = {
        "cr_id": cr_id,
        "from_version": cr_input.get("from_version", "v1.0"),
        "reason": cr_input.get("reason", ""),
        "impact_analysis": {
            "affected_tasks": cr_input.get("affected_tasks", []),
            "new_tasks": cr_input.get("new_tasks", []),
            "estimated_effort_hours": 8
        },
        "review_result": "pending",
        "to_version": None
    }
    return {
        "artifacts": {"change_request": cr_data},
        "events": [{"type": "human.decision", "payload": {"action": "create_cr", "cr_id": cr_id}}]
    }
