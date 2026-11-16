from typing import Dict, Any
from .generator import generate_breakdowns
from .comparison import build_comparison
from .models import validate_breakdown

def run(ctx: Dict[str, Any]) -> Dict[str, Any]:
    artifacts = ctx["artifacts"]
    design_spec = artifacts["design_spec"]
    feature_baseline = artifacts["feature_baseline"]
    conflict_register = artifacts["conflict_register"]

    plans = generate_breakdowns(feature_baseline, design_spec, conflict_register)

    for key, plan in plans.items():
        validate_breakdown(plan)

    compare_result = build_comparison(plans, feature_baseline)

    meta = {
        "schema_version": "v1",
        "run_id": ctx["run_id"],
        "stage": "solution_gen",
        "produced_by": "C",
        "created_at": ctx["created_at"]
    }

    output = {
        "breakdown_a": {**plans["a"], "meta": meta},
        "breakdown_b": {**plans["b"], "meta": meta},
        "breakdown_c": {**plans["c"], "meta": meta},
        "solution_comparison": {**compare_result, "meta": meta}
    }

    return {
        "artifacts": output,
        "events": [{"type": "SOLUTION_GENERATED", "run_id": ctx["run_id"]}],
        "usage": {"count": 1}
    }
