"""P6 主流程：build_task_list(ctx, adopted_plan) -> StageResult。

职责边界（只做结构化，不造任务）：
- 入：C 的 solution_comparison（采纳方案）+ A1 在 P6 产出的卡片草稿 + design_spec；
- 事：编号归一、字段补齐、优先级纯代码判定、八条规则校验、覆盖矩阵计算；
- 出：dev_task_list.json + task_coverage.json（都带 meta 信封）。
本模块不改方案、不裁决冲突、不判门禁、不写 runs/ —— 落盘与计费归 A。
"""

from __future__ import annotations

import re
from typing import Any

from .contracts import ErrorCode, StageContext, StageResult, artifact_data, make_artifact
from .coverage import compute_coverage, gate_inputs
from .models import DevTask
from .priority import compute_priorities, priority_summary
from .validators import acceptance_coverage, validate_task_list

DEV_ID_RE = re.compile(r"DEV-\d{3,}")


def _spec_of(ctx: StageContext) -> dict[str, Any]:
    for key in ("design_spec", "spec", "design_spec_normalized"):
        artifact = ctx.artifacts.get(key)
        if artifact:
            return artifact_data(artifact)
    config_spec = ctx.config.get("design_spec")
    if isinstance(config_spec, dict):
        return artifact_data(config_spec)
    return {}


def _drafts_of(plan: Any) -> list[dict[str, Any]]:
    """从采纳方案里取卡片草稿，容忍几种常见写法。"""
    if not plan:
        return []
    data = artifact_data(plan) if isinstance(plan, dict) else {}
    for key in ("tasks", "task_drafts", "adopted_tasks", "cards"):
        value = data.get(key)
        if isinstance(value, list) and value:
            return [v for v in value if isinstance(v, dict)]
    if isinstance(plan, dict):
        for key in ("tasks", "task_drafts", "adopted_tasks"):
            value = plan.get(key)
            if isinstance(value, list) and value:
                return [v for v in value if isinstance(v, dict)]
    return []


def structure_tasks(drafts: list[dict[str, Any]], start_index: int = 1) -> list[DevTask]:
    """编号归一 + 字段补齐。已有合法 DEV 号就保留，否则按顺序补号。"""
    tasks: list[DevTask] = []
    used = set()
    next_index = start_index
    for draft in drafts:
        task = DevTask.from_dict(draft, index=next_index)
        if not DEV_ID_RE.fullmatch(task.task_id) or task.task_id in used:
            while f"DEV-{next_index:03d}" in used:
                next_index += 1
            task.task_id = f"DEV-{next_index:03d}"
        used.add(task.task_id)
        next_index = max(next_index, int(task.task_id.split("-")[1]) + 1)
        tasks.append(task)
    return tasks


def build_task_list(ctx: Any, adopted_plan: Any = None) -> StageResult:
    """E 的 P6 入口（docx 表 9）。"""
    context = StageContext.from_any(ctx)
    result = StageResult(stage=context.stage, status="ok")

    spec = _spec_of(context)
    if not spec:
        result.add_error(
            ErrorCode.ARTIFACT_MISSING,
            "缺少 design_spec：P6 需要规格里的 features / constraints 才能算覆盖",
            artifact="design_spec",
        )
        return result

    plan = adopted_plan if adopted_plan is not None else context.artifacts.get("solution_comparison")
    drafts = _drafts_of(plan)
    if not drafts:
        drafts = [
            v for v in (_drafts_of(context.artifacts.get(name)) for name in ("task_drafts", "dev_task_drafts"))
            for v in v
        ]
    if not drafts:
        result.add_error(
            ErrorCode.ARTIFACT_MISSING,
            "缺少任务卡草稿：P6 只做结构化，不替 A1 造卡",
            artifact="solution_comparison.tasks|task_drafts",
        )
        return result

    tasks = structure_tasks(drafts)
    compute_priorities(tasks, spec)

    gaps, checks = validate_task_list(tasks, spec)
    report = compute_coverage(tasks, spec, context.artifacts)
    gate = gate_inputs(tasks, report)

    dev_task_list = {
        "tasks": [task.to_dict() for task in tasks],
        "summary": {
            "task_count": len(tasks),
            "total_estimate_days": round(sum(t.estimate_days for t in tasks), 2),
            "priority": priority_summary(tasks),
            "modules": sorted({t.module_id for t in tasks}),
        },
        "hard_validation": {
            **checks,
            **acceptance_coverage(tasks),
            "error_gap_count": sum(1 for g in gaps if g.severity == "ERROR"),
            "all_tasks_traceable": checks["T6_traceable"],
            "no_dep_cycle": checks["T4_no_cycle"],
        },
    }
    task_coverage = {
        **report.to_dict(),
        "gate": gate,
        "generated_from": {
            "spec": "design_spec",
            "plan": "solution_comparison",
        },
    }

    result.artifacts = {
        "dev_task_list": make_artifact(dev_task_list, context.run_id, "P6"),
        "task_coverage": make_artifact(task_coverage, context.run_id, "P6"),
    }
    result.budget_used = 0  # 纯代码，不计费

    if not gate["self_check_pass"]:
        serious = [g for g in gaps if g.severity == "ERROR"]
        if serious:
            result.add_error(
                ErrorCode.TASK_INVALID,
                f"{len(serious)} 条 ERROR 级缺口，不予交付",
                gaps=[g.to_dict() for g in serious[:10]],
            )
        result.status = "blocked" if result.errors else "ok"
    return result


def run(ctx: Any) -> StageResult:
    """A 的统一调用签名：run(ctx: StageContext) -> StageResult。"""
    return build_task_list(ctx)


def load_task_list(artifact: Any) -> list[DevTask]:
    """读回产物：给可视化与测试用。"""
    data = artifact_data(artifact)
    return [DevTask.from_dict(item, index=i) for i, item in enumerate(data.get("tasks", []), start=1)]
