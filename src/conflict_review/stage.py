# -*- coding: utf-8 -*-
"""A 的统一调用入口：run(ctx) -> StageResult。

D 模块负责两个阶段，一个入口分发：

    P3  读 discussion_minutes → 扫冲突 → 三级裁决 → 写 conflict_register
    P5  读 solution_comparison / dev_task_list → 四要素校验 + 打分 + 否决项
        → 写 review_sheet（同时把 conflict_register 原样回传，见 STAGE_OUTPUTS）

本模块不调模型（budget_used 恒为 0）：评审与裁决全是规则，
这样门禁结果可复算、可审计，也不会因为模型随机性让同一次运行两次结论不同。
"""

from __future__ import annotations

from typing import Any

from . import rules
from .contracts import (
    PRODUCER,
    ErrorCode,
    GuardSignal,
    StageResult,
    ctx_artifact,
    ctx_artifact_data,
    ctx_get,
    make_artifact,
    make_error,
    make_event,
)


def run(ctx: Any) -> StageResult:
    """A 通过 modules.resolve 调到这里；stage 决定跑哪条分支。"""
    stage = str(ctx_get(ctx, "stage", "") or "")
    if stage == "P3":
        return run_p3(ctx)
    if stage == "P5":
        return run_p5(ctx)
    return StageResult(
        stage=stage or "?",
        status="failed",
        errors=[make_error(ErrorCode.SCHEMA_INVALID, "conflict_review 只负责 P3 / P5，收到 %r" % stage)],
    )


# ---------------------------------------------------------------- P3

def _stances_from_minutes(minutes: dict[str, Any]) -> list[dict[str, Any]]:
    """从 B 的 discussion_minutes 里抽出所有立场陈述。

    兼容三种写法：
      data.statements[]（B 当前实现，每条带 issue_id / topic_id）
      data.issues[].statements[]（多议题版本）
      data.stances[]（旧样例）
    """
    issue = minutes.get("issue") or {}
    default_topic = str(issue.get("topic_id") or issue.get("issue_id") or "")

    raw: list[dict[str, Any]] = []
    for item in minutes.get("issues") or []:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic_id") or item.get("issue_id") or default_topic)
        for st in item.get("statements") or []:
            if isinstance(st, dict):
                raw.append({**st, "topic_id": st.get("topic_id") or topic})
    for st in minutes.get("statements") or []:
        if isinstance(st, dict):
            raw.append(st)
    for st in minutes.get("stances") or []:
        if isinstance(st, dict):
            raw.append(st)

    out: list[dict[str, Any]] = []
    for st in raw:
        topic = str(st.get("topic_id") or st.get("issue_id") or default_topic or "unnamed")
        stance = rules.Stance.from_any(st, topic_id=topic)
        out.append(stance.to_dict())
    return out


def run_p3(ctx: Any) -> StageResult:
    run_id = str(ctx_get(ctx, "run_id", "R0001"))
    minutes = ctx_artifact_data(ctx, "discussion_minutes")
    stances = _stances_from_minutes(minutes)

    if not stances:
        return StageResult(
            stage="P3",
            status="blocked",
            errors=[make_error(
                ErrorCode.ARTIFACT_MISSING,
                "discussion_minutes 里没有可用的立场陈述（statements/stances 均为空）",
            )],
        )

    prev = ctx_artifact_data(ctx, "conflict_register").get("conflicts") or []
    prev = [c for c in prev if isinstance(c, dict)]
    seen_topics = {str(c.get("topic_id")) for c in prev}

    fresh = [c for c in rules.detect_conflicts(stances, existing=len(prev))
             if str(c.get("topic_id")) not in seen_topics]

    conflicts: list[dict[str, Any]] = list(prev)
    events = []
    escalated: list[str] = []
    level_counter: dict[str, int] = {}

    for conflict in fresh:
        verdict = rules.adjudicate(conflict)
        merged = {**conflict, **verdict}
        conflicts.append(merged)
        level_counter[verdict["level"]] = level_counter.get(verdict["level"], 0) + 1

        events.append(make_event(run_id, "P3", "conflict.registered",
                                 payload_ref="conflict_register:%s" % merged["conflict_id"]))
        if verdict["status"] == "suspended":
            escalated.append(merged["conflict_id"])
            events.append(make_event(run_id, "P3", "conflict.escalated",
                                     payload_ref="conflict_register:%s" % merged["conflict_id"]))
        else:
            events.append(make_event(run_id, "P3", "conflict.resolved",
                                     payload_ref="conflict_register:%s" % merged["conflict_id"]))

    open_count = sum(1 for c in conflicts if str(c.get("status")) == "open")
    for c in prev:
        if str(c.get("status")) == "suspended" and c.get("conflict_id") not in escalated:
            escalated.append(str(c.get("conflict_id")))

    register = {
        "conflicts": conflicts,
        "open_count": open_count,
        "escalated_ids": sorted(set(escalated)),
        "stats": {
            "topic_count": len({str(s.get("topic_id")) for s in stances}),
            "stance_count": len(stances),
            "new_conflict_count": len(fresh),
            "by_level": level_counter,
        },
    }

    artifacts = {
        "conflict_register": make_artifact(register, run_id, "P3", PRODUCER),
    }
    # 本阶段 output_artifacts 还包含 B 的 discussion_minutes，原样透传，避免 A 校验缺产物
    minutes_art = ctx_artifact(ctx, "discussion_minutes")
    if minutes_art:
        artifacts["discussion_minutes"] = minutes_art

    guard = []
    if escalated:
        guard.append(GuardSignal(
            signal="conflict_escalated",
            value=float(len(escalated)),
            reason="有 %d 条冲突判不动，已挂起等人工裁决" % len(escalated),
        ))

    return StageResult(
        stage="P3",
        status="need_human" if escalated else "ok",
        artifacts=artifacts,
        events=events,
        guard_signals=guard,
        budget_used=0,
    )


# ---------------------------------------------------------------- P5

def _score_dict(raw: Any) -> dict[str, float]:
    """兼容两种打分写法：{维度: 分} 或 [{dimension, score}]。"""
    if isinstance(raw, dict):
        return dict(raw)
    out: dict[str, float] = {}
    for card in raw or []:
        if not isinstance(card, dict):
            continue
        name = card.get("dimension") or card.get("dim") or card.get("name")
        value = card.get("score")
        if name and value is not None:
            out[str(name)] = value
    return out


def _tasks_of(ctx: Any) -> list[dict[str, Any]]:
    """否决项判定要看的任务清单：优先 dev_task_list，退到 solution_comparison。"""
    for name in ("dev_task_list", "solution_comparison"):
        data = ctx_artifact_data(ctx, name)
        for key in ("tasks", "dev_tasks", "task_list"):
            value = data.get(key)
            if isinstance(value, list) and value:
                return [t for t in value if isinstance(t, dict)]
    return []


def run_p5(ctx: Any) -> StageResult:
    run_id = str(ctx_get(ctx, "run_id", "R0001"))
    config = ctx_get(ctx, "config", {}) or {}
    review_input = config.get("review") if isinstance(config, dict) else None
    if not isinstance(review_input, dict) or not review_input.get("scores"):
        review_input = ctx_artifact_data(ctx, "review_input") or review_input or {}

    scores = _score_dict(review_input.get("scores") or {})
    if not scores:
        return StageResult(
            stage="P5",
            status="blocked",
            errors=[make_error(
                ErrorCode.ARTIFACT_MISSING,
                "缺少五个维度的打分输入（review_input.scores 或 ctx.config.review.scores）",
            )],
        )

    tasks = _tasks_of(ctx)
    veto_items = rules.find_veto_items(tasks)
    challenges = review_input.get("challenges") or []
    triage = rules.triage_challenges(challenges)

    revision_count = int(review_input.get("revision_count") or 0)
    sheet = rules.review(
        scores=scores,
        veto=bool(review_input.get("veto")),
        veto_items=veto_items,
        challenges=triage["accepted"],
        revision_count=revision_count,
        score_id=str(review_input.get("score_id") or "SCR-001"),
    )

    events = [make_event(run_id, "P5", "score.published", payload_ref="review_sheet")]
    if sheet.passed:
        events.append(make_event(run_id, "P5", "review.passed", payload_ref="review_sheet"))
    else:
        events.append(make_event(run_id, "P5", "review.rework", payload_ref="review_sheet"))

    guard: list[GuardSignal] = []
    if triage.get("guard"):
        g = triage["guard"]
        guard.append(GuardSignal(signal=g["signal"], value=g["value"], reason=g["reason"]))

    prev = ctx_artifact_data(ctx, "conflict_register").get("conflicts") or []
    prev = [c for c in prev if isinstance(c, dict)]
    register = {
        "conflicts": prev,
        "open_count": sum(1 for c in prev if str(c.get("status")) == "open"),
        "escalated_ids": sorted({
            str(c.get("conflict_id")) for c in prev if str(c.get("status")) == "suspended"
        }),
    }

    artifacts = {
        "review_sheet": make_artifact(sheet.to_review_sheet_data(), run_id, "P5", PRODUCER),
        "conflict_register": make_artifact(register, run_id, "P5", PRODUCER),
    }

    return StageResult(
        stage="P5",
        status="ok",
        artifacts=artifacts,
        events=events,
        guard_signals=guard,
        budget_used=0,
    )
