"""任务卡八条硬规则校验：纯代码，逐条给结论。

对应 docx §3.6 的八条规则，编号 T1–T8 与文档一一对应。
校验只判"这张卡合不合格"，不判"要不要做"——后者是人的事。
第 8 条（同模块同文件不跨波并行）依赖 schedule 的波次信息，P6 阶段拿不到，
由 A 在 P7 校验，本模块只在文档里注明口径，不假装校验过。
"""

from __future__ import annotations

from typing import Iterable

from .contracts import ErrorCode
from .models import AcceptanceCriterion, DevTask, Gap

MIN_DAYS = 0.5
MAX_DAYS = 3.0

VAGUE_TITLE_WORDS = ("联调", "优化", "杂项", "其他", "重构", "完善", "临时", "待定", "todo")
FUZZY_AC_WORDS = ("尽量", "适当", "合理", "友好", "高效", "较好", "等等", "若干", "大概", "良好", "顺畅", "尽可能")
OBSERVABLE_HINTS = ("后", "返回", "出现", "显示", "提示", "加 1", "加1", "清零", "不超过", "≤", "<=", "秒内", "毫秒", "ms", "条")
MULTI_ACTION_WORDS = ("以及", "同时", "并且", "，和", "、和")
OWNER_BAD_WORDS = ("/", "、", "多人", "共同", "与", "和")


def validate_acceptance(ac: AcceptanceCriterion) -> str | None:
    """返回不合格原因；合格返回 None。"""
    text = ac.text().strip()
    if len(text) < 6:
        return "验收口径过短，写不清'什么操作之后看到什么结果'"
    for word in FUZZY_AC_WORDS:
        if word in text:
            return f"验收口径命中模糊词「{word}」"
    if not any(hint in text for hint in OBSERVABLE_HINTS):
        return "验收口径不可观测：缺少返回/出现/显示/数值等可测结果"
    if ac.kind in ("performance", "nfr") and not ac.metric:
        return "非功能验收缺少 metric，无法量化"
    return None


def find_cycle(tasks: Iterable[DevTask]) -> list[str]:
    """Kahn 拓扑排序排不完的部分就是环，返回环上的任务（按原顺序）。"""
    task_list = list(tasks)
    indegree = {t.task_id: 0 for t in task_list}
    edges: dict[str, list[str]] = {t.task_id: [] for t in task_list}
    for task in task_list:
        for dep in task.depends_on:
            if dep in edges:
                edges[dep].append(task.task_id)
                indegree[task.task_id] += 1

    queue = [tid for tid, deg in indegree.items() if deg == 0]
    visited = 0
    while queue:
        node = queue.pop()
        visited += 1
        for nxt in edges.get(node, []):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)

    if visited == len(task_list):
        return []
    return [tid for tid in indegree if indegree[tid] > 0]


def validate_task_list(tasks: list[DevTask], spec: dict | None = None) -> tuple[list[Gap], dict[str, bool]]:
    """逐条校验，返回 (缺口列表, 每项规则的布尔结论)。"""
    spec = spec or {}
    gaps: list[Gap] = []
    checks: dict[str, bool] = {
        "T1_granularity": True,
        "T2_unique_owner": True,
        "T3_dep_target": True,
        "T4_no_cycle": True,
        "T5_acceptance": True,
        "T6_traceable": True,
        "T7_no_vague": True,
    }
    ids = {t.task_id for t in tasks}

    # T1 一卡一件事且粒度 0.5–3 人天
    for task in tasks:
        if not (MIN_DAYS <= float(task.estimate_days) <= MAX_DAYS):
            checks["T1_granularity"] = False
            gaps.append(
                Gap(
                    kind="bad_granularity",
                    target=task.task_id,
                    severity="ERROR",
                    detail=f"工时 {task.estimate_days} 人天超出 0.5–3 区间，必须再拆或合并",
                    weight=3,
                    rule="T1",
                )
            )
        for word in MULTI_ACTION_WORDS:
            if word in task.title:
                checks["T1_granularity"] = False
                gaps.append(
                    Gap(
                        kind="multi_action_card",
                        target=task.task_id,
                        severity="ERROR",
                        detail=f"标题含「{word}」，一张卡只能做一件事",
                        weight=3,
                        rule="T1",
                    )
                )
                break

    # T2 负责人唯一
    for task in tasks:
        owner = task.owner_role.strip()
        if not owner or any(bad in owner for bad in OWNER_BAD_WORDS):
            checks["T2_unique_owner"] = False
            gaps.append(
                Gap(
                    kind="owner_not_unique",
                    target=task.task_id,
                    severity="ERROR",
                    detail=f"负责人「{owner}」不唯一或为空，禁止多人共同负责",
                    weight=3,
                    rule="T2",
                )
            )

    # T3 依赖只能指向存在的任务卡
    for task in tasks:
        for dep in task.depends_on:
            if dep not in ids:
                checks["T3_dep_target"] = False
                gaps.append(
                    Gap(
                        kind="dep_target_missing",
                        target=f"{task.task_id}->{dep}",
                        severity="ERROR",
                        detail=f"依赖目标 {dep} 不存在于任务清单",
                        weight=3,
                        rule="T3",
                    )
                )

    # T4 依赖不成环
    cycle = find_cycle(tasks)
    if cycle:
        checks["T4_no_cycle"] = False
        gaps.append(
            Gap(
                kind=ErrorCode.DEP_CYCLE,
                target=",".join(cycle),
                severity="ERROR",
                detail=f"依赖成环：{' -> '.join(cycle)}",
                weight=3,
                rule="T4",
            )
        )

    # T5 每卡至少一条可观测验收
    for task in tasks:
        if task.priority == "wont":
            continue
        if not task.acceptance:
            checks["T5_acceptance"] = False
            gaps.append(
                Gap(
                    kind="missing_acceptance",
                    target=task.task_id,
                    severity="ERROR",
                    detail="非 Wont 任务缺少验收口径",
                    weight=3,
                    rule="T5",
                )
            )
            continue
        for ac in task.acceptance:
            problem = validate_acceptance(ac)
            if problem:
                checks["T5_acceptance"] = False
                gaps.append(
                    Gap(
                        kind="unverifiable_ac",
                        target=ac.ac_id,
                        severity="ERROR",
                        detail=f"{task.task_id}：{problem}",
                        weight=3,
                        rule="T5",
                    )
                )

    # T6 每卡带来源引用（C1）
    for task in tasks:
        if not task.source_refs:
            checks["T6_traceable"] = False
            gaps.append(
                Gap(
                    kind="orphan_task",
                    target=task.task_id,
                    severity="ERROR",
                    detail="没有来源引用，属于凭空造的任务卡",
                    weight=3,
                    rule="T6",
                )
            )
        else:
            for ref in task.source_refs:
                if not ref.ref_id or ":" not in ref.to_string():
                    checks["T6_traceable"] = False
                    gaps.append(
                        Gap(
                            kind="bad_source_ref",
                            target=task.task_id,
                            severity="ERROR",
                            detail=f"来源引用格式不规范：{ref.to_string()}",
                            weight=3,
                            rule="T6",
                        )
                    )

    # T7 不许出现不可验收的卡
    for task in tasks:
        for word in VAGUE_TITLE_WORDS:
            if word in task.title:
                checks["T7_no_vague"] = False
                gaps.append(
                    Gap(
                        kind="vague_task",
                        target=task.task_id,
                        severity="ERROR",
                        detail=f"标题含「{word}」，属于不可验收的卡，需拆成具体任务",
                        weight=2,
                        rule="T7",
                    )
                )
                break

    # T8 同模块同文件名口径依赖 schedule 波次，P6 阶段不判定（见 docs/visualization.md §6）
    checks["T8_wave_file_rule"] = True

    return gaps, checks


def acceptance_coverage(tasks: list[DevTask]) -> dict[str, bool]:
    return {
        "all_have_acceptance": all(t.acceptance for t in tasks if t.priority != "wont"),
        "all_have_acceptance_verifiable": not any(
            gap.kind in ("missing_acceptance", "unverifiable_ac")
            for gap in validate_task_list(tasks)[0]
        ),
    }
