"""A 的 B/D 组合适配：B 不直接调用 D，状态转移仍由 Runtime 执行。"""
from __future__ import annotations

import copy

from .context import StageResult, make_artifact


def finalize_register(minutes, registered):
    """保留原始立场，按最新原始票型交 D 裁决；也用于超时票的恢复。"""
    from src.conflict_review import adjudicate
    votes = {v["agent_id"]: v for v in minutes["votes"]}
    final = []
    for conflict in registered:
        voted = copy.deepcopy(conflict)
        voted["extensions"] = {"original_stances": copy.deepcopy(conflict["stances"])}
        for stance in voted["stances"]:
            vote = votes.get(stance["agent_id"])
            stance["position"] = {"FOR": "for", "AGAINST": "against", "ABSTAIN": "neutral"}.get(vote["ballot"], "neutral") if vote else "neutral"
            stance["claim"] = vote["reason"] if vote else "缺席"
            stance["source_refs"] = vote["source_refs"] if vote else []
        final.append({**voted, **adjudicate(voted)})
    return {"conflicts": final, "open_count": sum(c["status"] == "open" for c in final),
            "escalated_ids": [c["conflict_id"] for c in final if c["status"] in ("suspended", "escalated")]}


def run(ctx, gateway=None):
    from src.agents import core
    from src.agents.providers import SchedulerGatewayModel
    from src.agents.stage import prepare_context, run as run_b, to_scheduler
    if ctx.stage == "P2":
        return run_b(ctx, gateway=gateway)
    model = None
    before = 0
    raw = {"artifacts": {}, "events": [], "usage": 0}
    try:
        from src.conflict_review import adjudicate, detect_conflicts
        low = prepare_context(ctx)
        # 构造任何网络请求前先检查材料，避免输入缺失仍扣模型额度。
        for aid in core.DELIBERATIVE:
            core.render_prompt(aid, "statement", low)
        model = SchedulerGatewayModel(gateway, "P3") if gateway else SchedulerGatewayModel.from_env("P3")
        before = model.gateway.usage()["calls"]
        registered = []
        for step in core.STEPS:
            low["config"]["protocol_step"] = step
            one = core.run_discussion(low, model=model)
            raw["artifacts"].update(one["artifacts"])
            raw["events"].extend(one["events"])
            raw["usage"] += one["usage"]
            low["budget_left"] -= one["usage"]
            low["artifacts"].update(one["artifacts"])
            minutes = one["artifacts"]["discussion_minutes"]["data"]
            if minutes.get("suspended"):
                return to_scheduler(ctx, raw, "blocked", [{"code": "E_GATE_UNSATISFIED", "message": "B 讨论挂起，请查看 guard_signals"}])
            if step == "statement":
                topic = minutes["issue"]["topic_id"]
                statements = [{**s, "topic_id": topic, "quote_ref": "discussion_minutes:" + s["id"]} for s in minutes["statements"]]
                # D 先登记，不在质询之前裁决。
                registered = detect_conflicts(statements)
                items = []
                for c in registered:
                    raw["events"].append({"type": "conflict.detected", "payload": {"id": c["conflict_id"], "agent_id": "conflict_review"}})
                    items.append({"conflict_id": c["conflict_id"], "topic_id": topic,
                                  "stances": [{"source_ref": s["quote_ref"]} for s in c["stances"]],
                                  "extensions": {"issue_id": minutes["issue"]["issue_id"]}})
                low["artifacts"]["conflict_register"] = make_artifact({"conflicts": registered, "items": items}, ctx.run_id, "P3", "conflict_review")
        minutes = raw["artifacts"]["discussion_minutes"]["data"]
        register = finalize_register(minutes, registered)
        for conflict in register["conflicts"]:
            raw["events"].append({"type": "conflict.resolved" if conflict["status"] == "resolved" else "guard.signal", "payload": {"id": conflict["conflict_id"], "agent_id": "conflict_review"}})
        output = to_scheduler(ctx, raw)
        from src.agents.agent_signals import build_signals, redact
        output.artifacts["conflict_register"] = make_artifact(redact(register), ctx.run_id, "P3", "conflict_review")
        output.artifacts["agent_signals"] = make_artifact(build_signals(minutes, register, ctx.run_id), ctx.run_id, "P3", "agents")
        return output
    except (core.AgentError, KeyError, TypeError) as exc:
        raw["usage"] = model.gateway.usage()["calls"] - before if model else 0
        return to_scheduler(ctx, raw, "blocked", [{"code": getattr(exc, "code", "E_SCHEMA_INVALID"), "message": str(exc)}])
