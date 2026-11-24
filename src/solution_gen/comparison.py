from .models import SolutionResult


def validate_compare_table(res: SolutionResult) -> bool:
    required_dims = ["实现难度", "成本", "风险", "交付周期", "推荐优先级"]
    got_dims = [row.dim for row in res.compare_table]
    return set(required_dims) == set(got_dims)


def calc_p4_metrics(solutions, comparison) -> dict:
    """计算 P4 核心指标，纯算术，不调模型。"""
    all_tasks = []
    for sol in solutions:
        tasks = getattr(sol, "tasks", None) or []
        all_tasks.extend(tasks)

    conflict_count = 0
    for row in comparison.get("rows", []):
        adopted = row.get("adopted") or {}
        if adopted.get("has_arch_conflict"):
            conflict_count += 1

    total_task_count = len(all_tasks)
    total_days = sum(
        getattr(t, "estimate_days", 0) or 0
        for t in all_tasks
    )

    return {
        "p4_total_task_count": total_task_count,
        "p4_arch_conflict_count": conflict_count,
        "p4_avg_task_days": round(total_days / total_task_count, 2) if total_task_count else 0,
        "p4_llm_call_count": 3,
    }
