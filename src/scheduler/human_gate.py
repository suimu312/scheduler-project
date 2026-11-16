"""人工交互协议（architecture §9.3 / §9.4）。

人只做两件事：提想法（P1）、做取舍（P7 / 挂起裁决）。
human 命令只写 human_decisions，必须再 resume 才继续，避免误触。
"""

from __future__ import annotations

from typing import Any

from .context import STAGES, RunConfig, RunState, now_iso

HUMAN_ACTIONS = ("approve", "reject", "comment", "sign", "adjudicate", "abort")


def apply_human_decision(
    state: RunState,
    action: str,
    reason: str = "",
    payload_ref: str = "",
    by: str = "user",
    at_phase: str | None = None,
) -> dict[str, Any]:
    if action not in HUMAN_ACTIONS:
        raise ValueError(f"非法人工动作 {action}，允许：{HUMAN_ACTIONS}")
    decision = {
        "decision_id": f"HD-{len(state.human_decisions) + 1:03d}",
        "run_id": state.run_id,
        "at_phase": at_phase or state.phase,
        "action": action,
        "reason": reason,
        "payload_ref": payload_ref,
        "decided_by": by,
        "decided_at": now_iso(),
    }
    state.human_decisions.append(decision)
    if action == "reject" and reason:
        state.constraints.append(reason)
    return decision


def build_rework_order(
    state: RunState,
    reason: str,
    max_reworks: int,
    carry_over: list[str] | None = None,
) -> dict[str, Any]:
    """打回不是从头重来：带原因 + 已有方案 + 已有意见回 P3，原因作为新约束广播（§9.3）。"""
    return {
        "to_phase": "P3",
        "reason": reason,
        "carry_over": carry_over or ["solution_comparison", "review_sheet", "opinion_handling", "conflict_register"],
        "new_constraints": [reason] if reason else [],
        "rework_count": state.ledger.rework_count + 1,
        "max_reworks": max_reworks,
    }


def resolve_after_decision(state: RunState, config: RunConfig) -> dict[str, Any]:
    """把最近一次人工动作翻译成状态机动作（不直接跑，等 resume）。"""
    if not state.human_decisions:
        raise ValueError("没有可解析的人工动作")
    decision = state.human_decisions[-1]
    action = str(decision.get("action"))
    reason = str(decision.get("reason") or "")
    max_reworks = int(config.limit("max_reworks", 2))

    effect: dict[str, Any]
    if action == "abort":
        state.status = "aborted"
        state.phase = "ABORTED"
        state.termination_reason = "human_abort"
        effect = {"action": "abort", "phase": "ABORTED", "status": "aborted"}

    elif action == "sign":
        # 签署后回到 P7 再跑一次 F：由 F 冻结 baseline，随后 G_P7_BASELINE 放行
        state.status = "running"
        state.phase = "P7"
        effect = {"action": "sign", "phase": "P7", "status": "running"}

    elif action == "approve":
        target = str((state.pending or {}).get("to") or (state.last_gate_verdict or {}).get("to") or state.phase)
        state.status = "running"
        state.phase = target if target not in ("HUMAN_GATE", "None") else "P6"
        effect = {"action": "approve", "phase": state.phase, "status": "running"}

    elif action == "reject":
        # 打回要不要回 P3、计数是否超限，由 G_P7_REWORK 判（§4.1 判定权在代码），
        # 这里只把人送回被阻塞的那个阶段，让闸门看到这条未被消费的人工动作。
        target = str((state.pending or {}).get("from") or "P7")
        state.status = "running"
        state.phase = target if target in STAGES else "P7"
        effect = {
            "action": "reject",
            "phase": state.phase,
            "status": "running",
            "rework_order": build_rework_order(state, reason, max_reworks),
            "note": "由 G_P7_REWORK 判定回 P3 与计数",
        }

    elif action == "adjudicate":
        if reason and reason not in state.constraints:
            state.constraints.append(reason)
        state.status = "running"
        state.phase = "P3"
        effect = {"action": "adjudicate", "phase": "P3", "status": "running"}

    else:  # comment：只留痕，仍等人下一次动作
        state.status = "waiting_human"
        state.phase = "HUMAN_GATE"
        effect = {"action": "comment", "phase": "HUMAN_GATE", "status": "waiting_human"}

    state.touch()
    return effect
