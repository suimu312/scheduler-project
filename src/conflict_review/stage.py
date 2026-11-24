# -*- coding: utf-8 -*-
"""A 的统一调用入口：run(ctx) -> StageResult。

D 模块负责两个阶段，一个入口分发：

    P3  读 discussion_minutes → 扫冲突 → 三级裁决 → 写 conflict_register
    P5  读 solution_comparison / conflict_register（+ 有的话 dev_task_list）
        → 取分（显式给 > review_input 产物 > 按产物算）→ 四要素校验 + 打分 + 否决项
        → 写 review_sheet（同时把 conflict_register 原样回传，见 STAGE_OUTPUTS）

本模块不调模型（budget_used 恒为 0）：评审与裁决全是规则，
这样门禁结果可复算、可审计，也不会因为模型随机性让同一次运行两次结论不同。
"""

from __future__ import annotations

from typing import Any

from . import opinions as opinion_rules
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


def _suspended_ids(ctx: Any) -> list[str]:
    """还挂着等人拍板的冲突编号 —— 没收敛的问题本身就是风险，也决定回炉归因。"""
    conflicts = ctx_artifact_data(ctx, "conflict_register").get("conflicts") or []
    return sorted({str(c.get("conflict_id")) for c in conflicts
                   if isinstance(c, dict) and str(c.get("status")) == "suspended"
                   and c.get("conflict_id")})


def _review_input(ctx: Any) -> dict[str, Any]:
    """评审输入的三级取法里，前两级：显式配置 > review_input 产物。"""
    config = ctx_get(ctx, "config", {}) or {}
    for candidate in (
        config.get("review") if isinstance(config, dict) else None,
        ctx_artifact_data(ctx, "review_input"),
    ):
        if isinstance(candidate, dict) and candidate.get("scores"):
            return candidate
    return {}


def _merge_challenges(manual: Any, auto: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """手写质疑优先；自动生成的那条如果已经有人指到同一个对象，就不重复提。"""
    manual = [c for c in (manual or []) if isinstance(c, dict)]
    taken = {str(c.get("impact_ref") or "") for c in manual}
    return manual + [c for c in auto if str(c.get("impact_ref") or "") not in taken]


def run_p5(ctx: Any) -> StageResult:
    run_id = str(ctx_get(ctx, "run_id", "R0001"))

    tasks = _tasks_of(ctx)
    comparison = ctx_artifact_data(ctx, "solution_comparison")

    # 评审依据：不管分数从哪来，产物的清点结果和否决项都要照算
    derived = rules.score_from_artifacts(
        comparison, tasks, suspended_ids=_suspended_ids(ctx)
    )

    review_input = _review_input(ctx)
    scores = _score_dict(review_input.get("scores") or {})
    origin = "explicit"
    if not scores:
        # 第三级：没人给分，就按产物算 —— 这才是 flow.yaml 里 P5 的常态
        scores = derived["scores"]
        origin = "derived"

    if not scores:
        return StageResult(
            stage="P5",
            status="blocked",
            errors=[make_error(
                ErrorCode.ARTIFACT_MISSING,
                "四维分数既没给也没法从产物算出来：solution_comparison 里读不到 "
                "feature_items / rows / 人天估算（可查 solution_comparison 是否已产出）",
            )],
        )

    evidence = derived["evidence"] if origin == "derived" else {
        str(k): str(v) for k, v in (review_input.get("evidence") or {}).items()
    }
    # unavailable 只报「最终没有分数」的维度：上游把四维都给了，就不存在未评的维度
    scored = rules.canon_scores(scores)
    unavailable = [u for u in derived["unavailable"] if u["dimension"] not in scored]

    challenges = _merge_challenges(
        review_input.get("challenges"),
        rules.challenges_from_findings(derived),
    )
    triage = rules.triage_challenges(challenges)

    revision_count = int(review_input.get("revision_count") or 0)
    sheet = rules.review(
        scores=scores,
        veto=bool(review_input.get("veto")),
        veto_items=derived["veto_items"],
        revision_count=revision_count,
        score_id=str(review_input.get("score_id") or "SCR-001"),
        evidence=evidence,
        unavailable=unavailable,
        scope=derived["scope"],
        score_origin=origin,
        triage=triage,
    )

    events = [make_event(run_id, "P5", "score.published", payload_ref="review_sheet")]
    if sheet.passed:
        events.append(make_event(run_id, "P5", "review.passed", payload_ref="review_sheet"))
    else:
        events.append(make_event(run_id, "P5", "review.rework", payload_ref="review_sheet"))
        if sheet.rework_target == "HUMAN":
            events.append(make_event(run_id, "P5", "review.escalated", payload_ref="review_sheet"))

    guard: list[GuardSignal] = []
    if triage.get("guard"):
        g = triage["guard"]
        guard.append(GuardSignal(signal=g["signal"], value=g["value"], reason=g["reason"]))
    if unavailable:
        guard.append(GuardSignal(
            signal="review_blind_spot",
            value=float(len(unavailable)),
            reason="有 %d 个维度本次没有依据可判：%s（已在评审单上标注，未补分）"
            % (len(unavailable), "、".join(u["dimension"] for u in unavailable)),
        ))

    prev = ctx_artifact_data(ctx, "conflict_register").get("conflicts") or []
    prev = [c for c in prev if isinstance(c, dict)]
    register = {
        "conflicts": prev,
        "open_count": sum(1 for c in prev if str(c.get("status")) == "open"),
        "escalated_ids": sorted({
            str(c.get("conflict_id")) for c in prev if str(c.get("status")) == "suspended"
        }),
    }

    # 意见处理表：评审单上的每条质疑，按 P5 手上能读到的产物判成三态处置。
    # 处置结果同时回填到 challenges[].verdict —— 评审单只留一个结论口径，
    # 不让"质疑说 PENDING、处理表说 ACCEPTED"这种自相矛盾出现在同一份产物里。
    opinions = opinion_rules.build_opinions(sheet.challenges, comparison, register)
    dropped = opinion_rules.count_dropped(sheet.challenges)
    by_challenge = {o["challenge_id"]: o["verdict"] for o in opinions}
    for challenge in sheet.challenges:
        if challenge.challenge_id in by_challenge:
            challenge.verdict = by_challenge[challenge.challenge_id]

    opinion_errors = opinion_rules.validate_opinions(opinions)
    if opinion_errors:
        guard.append(GuardSignal(
            signal="opinion_uncheckable",
            value=float(len(opinion_errors)),
            reason="有 %d 条意见的处置理由不含可核对内容：%s"
            % (len(opinion_errors), "；".join(opinion_errors[:3])),
        ))

    sheet_data = sheet.to_review_sheet_data()
    sheet_data["opinion_handling"] = opinions
    sheet_data["opinion_summary"] = opinion_rules.summarize(opinions, dropped=dropped)

    artifacts = {
        "review_sheet": make_artifact(sheet_data, run_id, "P5", PRODUCER),
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
