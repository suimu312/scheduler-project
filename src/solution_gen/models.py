from typing import Dict, Any
import jsonschema

TASK_SCHEMA = {
    "type": "object",
    "required": ["task_id", "feature_ref", "module_id", "title", "depends_on",
                 "estimate_days", "owner_role", "acceptance_criteria", "source_refs"],
    "properties": {
        "task_id": {"type": "string"},
        "feature_ref": {"type": "string"},
        "module_id": {"type": "string"},
        "title": {"type": "string"},
        "depends_on": {"type": "array", "items": {"type": "string"}},
        "estimate_days": {"type": "number", "minimum": 0.5, "maximum": 3.0},
        "owner_role": {"type": "string"},
        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
        "source_refs": {"type": "array", "items": {"type": "string"}}
    }
}

BREAKDOWN_SCHEMA = {
    "type": "object",
    "required": ["plan_id", "plan_type", "description", "feature_ids", "tasks", "total_days"],
    "properties": {
        "plan_id": {"type": "string"},
        "plan_type": {"type": "string"},
        "description": {"type": "string"},
        "feature_ids": {"type": "array", "items": {"type": "string"}},
        "tasks": {"type": "array", "items": TASK_SCHEMA},
        "total_days": {"type": "number"}
    }
}

def validate_breakdown(plan: Dict[str, Any]):
    jsonschema.validate(instance=plan, schema=BREAKDOWN_SCHEMA)
    feature_ids = plan["feature_ids"]
    task_fids = sorted({t["feature_ref"] for t in plan["tasks"]})
    if sorted(feature_ids) != task_fids:
        raise ValueError("C4校验失败：三套方案feature_ids不一致")
    all_tids = [t["task_id"] for t in plan["tasks"]]
    for t in plan["tasks"]:
        for d in t["depends_on"]:
            if d not in all_tids:
                raise ValueError(f"依赖悬空：{t['task_id']} 依赖 {d} 不存在")
