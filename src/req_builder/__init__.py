"""req_builder：E 模块（任务条目 · 覆盖矩阵 · 七视图渲染）。

R · 需求分析师（E）负责 P6 的任务卡结构化、覆盖矩阵与全部可视化。
对外只有三个入口：

    build_task_list(ctx, adopted_plan) -> StageResult    # P6 结构化 + 校验
    compute_coverage(tasks, spec, artifacts)            # 覆盖矩阵
    render_views(events, until_seq, artifacts) -> str   # 七视图静态页

不要在本包里 import A 的 scheduler，模块之间只通过信封传递数据。
"""

from .contracts import RunEvent, StageContext, StageResult, make_artifact
from .coverage import compute_coverage, gate_inputs
from .models import AcceptanceCriterion, CoverageReport, CoverageRow, DevTask, Gap, SourceRef
from .priority import compute_priorities, priority_summary
from .task_list import build_task_list, load_task_list, run, structure_tasks
from .validators import find_cycle, validate_acceptance, validate_task_list

__all__ = [
    "RunEvent",
    "StageContext",
    "StageResult",
    "make_artifact",
    "DevTask",
    "SourceRef",
    "AcceptanceCriterion",
    "CoverageRow",
    "CoverageReport",
    "Gap",
    "build_task_list",
    "compute_coverage",
    "compute_priorities",
    "priority_summary",
    "gate_inputs",
    "structure_tasks",
    "load_task_list",
    "run",
    "validate_task_list",
    "validate_acceptance",
    "find_cycle",
]
__version__ = "1.0.0"
