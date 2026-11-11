"""优先级判定：纯代码，不调模型。

docx §3.6 的口径：
- Must  = 其他任务的依赖前置，或被别的任务验收口径直接引用；
- Should= 本轮必须交付但可并行推迟；
- Could = 不影响首版可用；
- Wont  = 本期明确不做。

模型不参与打分：让模型给自己写的卡排优先级，七成都会写成 Must。
本文件只输出"等级 + 理由"，理由必须写明命中的规则编号与信号取值，供人复核。
"""

from __future__ import annotations

from typing import Any, Iterable

from .models import DevTask, priority_rank

# 规则编号（写进 priority_reason，评审时可按号核对）
R_DEP_PRECEDE = "P1"  # 被依赖前置
R_AC_REFERENCE = "P2"  # 被验收口径直接引用
R_SCOPE_OUT = "P3"  # 明确排除
R_HUMAN_LOCK = "P4"  # 人工锁定
R_SCORE = "P5"  # 加权得分

HARD_GRADE_KEYWORDS = ("鉴权", "权限", "登录", "隐私", "合规", "支付", "资金", "删除", "审计", "备份")
SCORE_MUST = 0.75
SCORE_SHOULD = 0.55
SCORE_COULD = 0.30


def _scope_ids(spec: dict[str, Any], key: str) -> list[str]:
    values = spec.get(key) or []
    out: list[str] = []
    for item in values:
        if isinstance(item, dict):
            ref = item.get("feature_id") or item.get("id") or item.get("name")
            if ref:
                out.append(str(ref))
        elif item is not None:
            out.append(str(item))
    return out


def dependents_map(tasks: Iterable[DevTask]) -> dict[str, set[str]]:
    """直接依赖的反向索引：task_id -> 依赖它的任务集合。"""
    reverse: dict[str, set[str]] = {t.task_id: set() for t in tasks}
    for task in tasks:
        for dep in task.depends_on:
            reverse.setdefault(dep, set()).add(task.task_id)
    return reverse


def transitive_dependents(tasks: Iterable[DevTask]) -> dict[str, set[str]]:
    """传递闭包：某卡一旦延期，所有下游都会跟着延期。"""
    reverse = dependents_map(tasks)
    result: dict[str, set[str]] = {}
    for task_id in reverse:
        seen: set[str] = set()
        stack = list(reverse.get(task_id, ()))
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            stack.extend(reverse.get(node, ()))
        result[task_id] = seen
    return result


def tasks_referencing_acceptance(tasks: Iterable[DevTask]) -> dict[str, set[str]]:
    """验收口径里被点名引用的任务：T 的验收写着"DEV-002 返回 20 条"就算引用。"""
    task_list = list(tasks)
    references: dict[str, set[str]] = {t.task_id: set() for t in task_list}
    for task in task_list:
        for text in task.acceptance_texts():
            for other in task_list:
                if other.task_id != task.task_id and other.task_id in text:
                    references.setdefault(other.task_id, set()).add(task.task_id)
    return references


def score_task(
    task: DevTask,
    spec: dict[str, Any],
    downstream: int,
) -> tuple[float, dict[str, float]]:
    """加权得分（三路信号，权重固定，写死便于复核）。"""
    scope_in = set(_scope_ids(spec, "scope_in"))
    feature_ids = {ref.ref_id for ref in task.source_refs if ref.kind == "feature"}
    constraint_count = sum(1 for ref in task.source_refs if ref.kind == "constraint")

    if feature_ids & scope_in:
        user_value = 1.0
    elif feature_ids:
        user_value = 0.6
    elif constraint_count:
        user_value = 0.5
    else:
        user_value = 0.0

    dependency_pressure = min(1.0, downstream / 2)
    cost_pressure = 1.0 - min(1.0, float(task.estimate_days) / 3.0)

    score = 0.5 * user_value + 0.3 * dependency_pressure + 0.2 * cost_pressure
    return score, {
        "user_value": user_value,
        "downstream": downstream,
        "cost": cost_pressure,
        "score": score,
    }


def compute_priorities(tasks: list[DevTask], spec: dict[str, Any] | None = None) -> dict[str, str]:
    """就地写入 priority / priority_reason / priority_source，返回 task_id -> 等级。"""
    spec = spec or {}
    scope_out = set(_scope_ids(spec, "scope_out"))
    trans = transitive_dependents(tasks)
    ac_refs = tasks_referencing_acceptance(tasks)

    for task in tasks:
        # P4 人工锁定：算法此后只读不写
        if task.priority_source == "human":
            task.priority_reason = f"{R_HUMAN_LOCK} 人工锁定，算法不改写"
            continue

        feature_ids = {ref.ref_id for ref in task.source_refs if ref.kind == "feature"}

        # P3 明确排除：来源功能点在 scope_out 里
        if feature_ids & scope_out:
            task.priority = "wont"
            task.priority_reason = f"{R_SCOPE_OUT} 来源功能点在 scope_out，本期明确不做"
            continue

        downstream = len(trans.get(task.task_id, set()))
        ac_ref = len(ac_refs.get(task.task_id, set()))
        hit_hard = any(word in task.title for word in HARD_GRADE_KEYWORDS)

        # P1 依赖前置：被任何任务（直接或间接）依赖
        if downstream >= 1 or hit_hard:
            task.priority = "must"
            reason = []
            if downstream >= 1:
                reason.append(f"{R_DEP_PRECEDE} 被 {downstream} 张卡依赖")
            if hit_hard:
                reason.append("命中硬规则词（鉴权/权限/合规类）")
            task.priority_reason = "；".join(reason)
            continue

        # P2 验收引用：别的卡验收口径里点了这张卡的名
        if ac_ref >= 1:
            task.priority = "must"
            task.priority_reason = f"{R_AC_REFERENCE} 被 {ac_ref} 条验收口径直接引用"
            continue

        # P5 加权得分
        score, signals = score_task(task, spec, downstream)
        if score >= SCORE_MUST:
            level = "must"
        elif score >= SCORE_SHOULD:
            level = "should"
        elif score >= SCORE_COULD:
            level = "could"
        else:
            # 得分再低也不自动判 Wont：Wont 是人的主动排除，不是算法结论
            level = "could"
        task.priority = level
        task.priority_reason = (
            f"{R_SCORE} 得分 {score:.2f}"
            f"（用户价值 {signals['user_value']:.2f} / 下游 {signals['downstream']}"
            f" / 工期压力 {signals['cost']:.2f}）"
        )

    return {task.task_id: task.priority for task in tasks}


def priority_summary(tasks: list[DevTask]) -> dict[str, int]:
    summary = {"must": 0, "should": 0, "could": 0, "wont": 0}
    for task in tasks:
        summary[task.priority] = summary.get(task.priority, 0) + 1
    return summary


def sort_by_priority(tasks: list[DevTask]) -> list[DevTask]:
    return sorted(tasks, key=lambda t: (-priority_rank(t.priority), t.task_id))
