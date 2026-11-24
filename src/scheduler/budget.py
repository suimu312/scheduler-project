"""计费与终止判定（architecture §7）。

口径（§7.1）：只有 LLM 调用计费，一次调用 = 1；闸门、覆盖率、打分统计等纯代码判定不计费。
模块不自己记账，StageResult.budget_used 由 A 在 step() 结束时统一 charge。
"""

from __future__ import annotations

from typing import Any

from .context import BudgetLedger, RunConfig, RunState


def build_ledger(config: RunConfig) -> BudgetLedger:
    return BudgetLedger(
        global_limit=config.global_llm_calls,
        per_stage_limit=config.per_stage_budget,
        per_stage_used={},
        rounds={},
    )


def charge(ledger: BudgetLedger, stage: str, used: int) -> int:
    """把本次真实调用数记进台账，返回累计已用。"""
    return ledger.charge(stage, used)


def budget_exhausted(ledger: BudgetLedger) -> bool:
    return ledger.exhausted()


def should_terminate(state: RunState, ledger: BudgetLedger, config: RunConfig) -> str | None:
    """返回终止原因，None 表示继续跑（§7.4）。"""
    if state.status == "frozen" or state.phase == "BASELINE_FROZEN":
        return "baseline_signed"

    if any(str(d.get("action")) in ("abort", "aborted") for d in state.human_decisions):
        return "human_abort"

    if state.status == "aborted" or state.phase == "ABORTED":
        return "human_abort" if state.termination_reason == "human_abort" else "error"

    if ledger.global_used >= ledger.global_limit:
        return "budget_exhausted"

    max_reworks = int(config.limit("max_reworks", 2))
    if ledger.rework_count > max_reworks:
        return "rework_limit"

    max_rounds = int(config.limit("max_discussion_rounds", 3))
    # P3 满轮本身由 G_P3_P4 放行，这里只兜「超过上限还停在 P3」的死循环
    if state.phase == "P3" and ledger.round_of("P3") > max_rounds + 1:
        return "max_rounds"

    if state.status == "escalated":
        reason = (state.last_gate_verdict or {}).get("reason")
        if reason in ("budget_exhausted", "budget"):
            return "budget_exhausted"
        if reason in ("rework_limit", "human_rework"):
            return "rework_limit"
        return "error"

    return None
