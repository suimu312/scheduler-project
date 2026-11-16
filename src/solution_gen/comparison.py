from .models import SolutionResult

def validate_compare_table(res: SolutionResult) -> bool:
    required_dims = ["实现难度", "成本", "风险", "交付周期", "推荐优先级"]
    got_dims = [row.dim for row in res.compare_table]
    return set(required_dims) == set(got_dims)
