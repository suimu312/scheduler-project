"""Agent 上场调度：决定谁上场、谁不上场、按什么协议说话（architecture §6）。

边界（§6.2）：A 只做「谁在什么时候说话、说几次、可见性、计数」；
提示词内容、立场、知识边界全部归 B。A 不读也不改 prompt_templates/。
"""

from __future__ import annotations

from typing import Any

from .context import AGENT_FAMILY, AGENT_IDS, RunConfig, RunState

# B 的 agent_config.yaml#mandatory_by_type 缺失时的兜底（§6.2 必到角色绑定）
DEFAULT_MANDATORY_BY_TYPE = {
    "functional": "A2",
    "technical": "A3",
    "cost": "A1",
    "risk": "A4",
}


def mandatory_by_type(agent_config: dict[str, Any] | None) -> dict[str, str]:
    mapping = (agent_config or {}).get("mandatory_by_type")
    if isinstance(mapping, dict) and mapping:
        return {str(k): str(v) for k, v in mapping.items()}
    return dict(DEFAULT_MANDATORY_BY_TYPE)


def _problems(artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    art = artifacts.get("problem_list") or {}
    data = art.get("data") if isinstance(art, dict) else {}
    return [p for p in ((data or {}).get("problems") or []) if isinstance(p, dict)]


def _types_of(problem: dict[str, Any]) -> list[str]:
    raw = problem.get("types") or problem.get("problem_types") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(t).lower() for t in raw if t]


def _protocol_step(spec: Any, round_index: int) -> str | None:
    protocol = list(getattr(spec, "protocol", []) or [])
    if not protocol:
        return None
    idx = max(0, int(round_index) - 1) % len(protocol)
    return protocol[idx]


def _query_pairs(on_stage: list[str], limits: dict[str, Any], max_pairs: int) -> list[dict[str, Any]]:
    """跨立场优先配对：协商族之间两两配对，加工族不参与质询。"""
    deliberative = [a for a in on_stage if AGENT_FAMILY.get(a) == "deliberative"]
    pairs: list[dict[str, Any]] = []
    max_chars = int(limits.get("query_max_chars", 200))
    for i, asker in enumerate(deliberative):
        for responder in deliberative[i + 1:]:
            pairs.append({"asker": asker, "responder": responder, "must_quote": True, "max_chars": max_chars})
            if len(pairs) >= max_pairs:
                return pairs
    return pairs


def apply_guard_signals(on_stage: list[str], guard_signals: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """force_speak：连续两轮没被点名的角色下一轮强制给一次发言权（§6.2）。"""
    reasons: list[str] = []
    for sig in guard_signals or []:
        if str(sig.get("signal")) == "force_speak" and sig.get("agent_id") in AGENT_IDS:
            agent = str(sig["agent_id"])
            if agent not in on_stage:
                on_stage.append(agent)
                reasons.append(f"force_speak:{agent}")
    return on_stage, reasons


def apply_recusal(on_stage: list[str], spec: Any) -> tuple[list[str], str | None]:
    """作者不评自己的稿：P5 把 A1 摘出去（§6.1）。"""
    recusal = getattr(spec, "agents", {}).get("recuse_reason")
    off = list(getattr(spec, "off_stage", []) or [])
    on = [a for a in on_stage if a not in off]
    return on, str(recusal) if recusal else None


def build_dispatch_plan(
    state: RunState,
    flow: Any,
    artifacts: dict[str, Any],
    config: RunConfig,
    agent_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = flow.stage(state.phase)
    on_stage: list[str] = []
    reason_parts: list[str] = []
    protocol_step = _protocol_step(spec, state.round_index)

    if state.phase == "P2":
        mapping = mandatory_by_type(agent_config)
        low_confidence = False
        for p in _problems(artifacts):
            for t in _types_of(p):
                role = mapping.get(t)
                if role and role not in on_stage:
                    on_stage.append(role)
            if str(p.get("confidence") or p.get("confidence_band") or "").lower() == "low":
                low_confidence = True
        if low_confidence:
            on_stage = list(AGENT_IDS)
            reason_parts.append("low_confidence_all_hands")
        else:
            reason_parts.append("mandatory_role_binding:" + ",".join(on_stage or ["classifier_only"]))
        return {
            "run_id": state.run_id,
            "stage": state.phase,
            "round_index": state.round_index,
            "classifier": True,
            "on_stage": on_stage,
            "off_stage": [a for a in AGENT_IDS if a not in on_stage],
            "reason": "; ".join(reason_parts),
            "protocol_step": protocol_step,
            "visibility": {"parallel_statement": "isolated", "after_release": "all"},
            "query_pairs": [],
            "supplement_window": {"open": False},
            "constraints": list(state.constraints),
        }

    # 其余阶段：flow.yaml 声明的上场集合
    on_stage = list(spec.on_stage)
    on_stage, recusal = apply_recusal(on_stage, spec)
    on_stage, guard_reasons = apply_guard_signals(on_stage, state.guard_signals)
    reason_parts.extend(guard_reasons)
    if recusal:
        reason_parts.append(f"recusal:{recusal}")

    off_stage = [a for a in AGENT_IDS if a not in on_stage]
    stage_limits = dict(getattr(spec, "limits", {}) or {})
    max_pairs = int(stage_limits.get("max_query_pairs", 4))

    plan: dict[str, Any] = {
        "run_id": state.run_id,
        "stage": state.phase,
        "round_index": state.round_index,
        "on_stage": on_stage,
        "off_stage": off_stage,
        "reason": "; ".join(reason_parts) or "flow_on_stage",
        "protocol_step": protocol_step,
        "visibility": {"parallel_statement": "isolated", "after_release": "all"},
        "query_pairs": _query_pairs(on_stage, stage_limits, max_pairs) if state.phase == "P3" else [],
        "supplement_window": {
            "open": state.phase == "P3",
            "max_speakers": int(config.limit("max_supplement_speakers", 2)),
            "max_total": int(config.limit("max_supplements_per_round", 3)),
            "max_chars": int(config.limit("supplement_max_chars", 80)),
        },
        "constraints": list(state.constraints),
    }
    mode = spec.agents.get("mode")
    if mode:
        plan["mode"] = mode
    if getattr(spec, "variants", None):
        plan["variants"] = list(spec.variants)
    return plan
