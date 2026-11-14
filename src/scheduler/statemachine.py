"""编排引擎主循环：状态机 + 闸门 + 计费 + 事件（architecture §3 / §4 / §7 / §11）。

不变式（§11 结尾，单测覆盖）：
1. 任一时刻 state.phase 只能由 apply_transition() 修改；
2. 任一产物只能由 store.write_artifacts() 落盘；
3. 任一 LLM 调用只能通过 Gateway 计费。
"""

from __future__ import annotations

from typing import Any

from . import budget as budget_mod
from .context import (
    STAGES,
    RunConfig,
    RunState,
    StageContext,
    StageResult,
    make_artifact,
    now_iso,
    validate_stage_result,
)
from .dispatcher import build_dispatch_plan
from .events import EventEmitter
from .flow_loader import Flow, load_agent_config, load_flow, resolve_asset, validate_flow
from .gate_rule import GateEvaluator, RuleSet, load_rules, load_rules_dict
from .modules import resolve as resolve_module
from .store import RunStore


class ConfigError(RuntimeError):
    """启动期校验失败（fail-fast，不允许带病运行）。"""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("配置校验失败:\n  - " + "\n  - ".join(errors))


class Runtime:
    def __init__(
        self,
        config: RunConfig,
        flow: Flow,
        rules: RuleSet,
        agent_config: dict[str, Any] | None = None,
    ) -> None:
        self.config = config
        self.flow = flow
        self.rules = rules
        self.agent_config = agent_config or {}
        self.store = RunStore(config.run_root)
        self.evaluator = GateEvaluator(rules)

    # ------------------------------------------------------------ 启动
    @classmethod
    def boot(cls, config: RunConfig) -> "Runtime":
        flow = load_flow(resolve_asset(config.flow_ref))
        rules_path = resolve_asset(config.gate_ref)
        rules = load_rules(rules_path)
        agent_config = load_agent_config(config.agent_ref)
        errors = validate_flow(
            flow,
            load_rules_dict(rules_path),
            agent_config,
            global_llm_calls=config.global_llm_calls,
        )
        if errors:
            raise ConfigError(errors)
        return cls(config, flow, rules, agent_config)

    # ------------------------------------------------------------ 状态
    def init_state(self) -> RunState:
        ledger = budget_mod.build_ledger(self.config)
        return RunState(
            run_id=self.config.run_id,
            status="running",
            phase="P1",
            round_index=1,
            ledger=ledger,
        )

    def load_or_init(self) -> RunState:
        state = self.store.load_state(self.config.run_id)
        if state is None:
            self.store.ensure(self.config.run_id)
            self.store.save_config(self.config.run_id, self.config.to_dict())
            state = self.init_state()
            self.store.save_state(state)
        # 阈值口径以本次配置为准（改 config 重跑时不要沿用旧的 per_stage_limit）
        state.ledger.per_stage_limit = self.config.per_stage_budget
        state.ledger.global_limit = self.config.global_llm_calls
        return state

    # ------------------------------------------------------------ 产物快照
    def _all_artifacts(self, state: RunState) -> dict[str, Any]:
        names = set(state.artifacts_index.keys())
        for spec in self.flow.stages.values():
            names.update(spec.output_artifacts)
        return self.store.load_artifacts(state.run_id, sorted(names), state.artifacts_index)

    # ------------------------------------------------------------ 单步
    def step(self, state: RunState) -> dict[str, Any]:
        phase = state.phase
        if phase not in self.flow.stages:
            raise RuntimeError(f"非法阶段 {phase}（只允许 P1..P7）")

        spec = self.flow.stage(phase)
        emitter = EventEmitter(self.store, state.run_id, state.ledger.global_used)

        # 单阶段预算用超 → 本阶段停机，交人工（不中断整轮，§7.2）
        if state.ledger.stage_exhausted(phase):
            state.status = "waiting_human"
            state.phase = "HUMAN_GATE"
            state.pending = {
                "from": phase,
                "to": phase,
                "gate_id": "T_STAGE_BUDGET",
                "reason": "stage_budget_exhausted",
                "used": state.ledger.used_of(phase),
                "limit": state.ledger.limit_of(phase),
            }
            self.store.save_state(state)
            emitter.emit("run.blocked", stage=phase, actor="human_gate", payload_ref="stage_budget_exhausted")
            return {"action": "wait_human", "reason": "stage_budget_exhausted"}

        snapshot = self.store.load_artifacts(state.run_id, spec.input_artifacts, state.artifacts_index)
        plan = build_dispatch_plan(state, self.flow, snapshot, self.config, self.agent_config)
        ctx = StageContext(
            run_id=state.run_id,
            stage=phase,
            round_index=state.round_index,
            dispatch_plan=plan,
            artifacts=snapshot,
            config={**spec.to_context_config(), "shared_constraints": self.flow.shared_constraints},
            ledger=state.ledger,
            constraints=list(state.constraints),
            human_decisions=list(state.human_decisions),
        )

        emitter.emit("stage.enter", stage=phase, actor="scheduler", payload_ref=spec.owner)
        if plan.get("on_stage"):
            emitter.emit("agent.on_stage", stage=phase, actor="scheduler", payload_ref=",".join(plan["on_stage"]))

        result = self._invoke_module(state, spec, ctx, emitter)
        if result is None or state.phase == "ABORTED":
            state.status = "aborted"
            state.termination_reason = "error"
            state.phase = "ABORTED"
            self.store.save_state(state)
            return {"action": "abort", "reason": "error"}

        # 非 ok：blocked / need_human 不重试，直接进人工闸门（§8.4）
        if result.status in ("blocked", "need_human"):
            state.status = "waiting_human"
            state.phase = "HUMAN_GATE"
            state.pending = {"from": phase, "to": phase, "reason": result.status, "errors": result.errors}
            self.store.save_state(state)
            emitter.emit("run.blocked", stage=phase, actor="human_gate", payload_ref=result.status)
            return {"action": "wait_human", "reason": result.status}

        # 落盘 + 索引
        index = self.store.write_artifacts(state.run_id, result.artifacts)
        state.artifacts_index.update(index)

        # 计费（重试也计费）
        budget_mod.charge(state.ledger, phase, result.budget_used)
        emitter.set_budget_hint(state.ledger.global_used)
        emitter.emit("budget.charged", stage=phase, actor="scheduler", payload_ref=str(result.budget_used))

        for e in result.events:
            emitter.emit(e.event_type, stage=phase, actor=e.actor or spec.owner, payload_ref=e.payload_ref)

        # 防退化信号下一轮生效
        state.guard_signals = [s.to_dict() for s in result.guard_signals]
        for sig in state.guard_signals:
            emitter.emit("guard.signal", stage=phase, actor="scheduler", payload_ref=str(sig.get("signal")))

        # 闸门求值 + 状态转移
        verdict = self.evaluate(state)
        self.apply_transition(state, verdict)
        emitter.emit("gate.evaluated", stage=phase, actor="scheduler", payload_ref=verdict.get("gate_id", ""))
        emitter.emit("stage.exit", stage=phase, actor="scheduler", payload_ref=verdict.get("action", ""))

        self.store.save_state(state)
        return verdict

    def _invoke_module(
        self, state: RunState, spec: Any, ctx: StageContext, emitter: EventEmitter
    ) -> StageResult | None:
        """调用模块；失败重试 1 次，仍失败则终止（§8.4）。"""
        if state.phase == "P1":
            return self._run_p1(state)

        fn = resolve_module(spec.owner, self.config.provider)
        if fn is None:
            return StageResult(
                stage=state.phase,
                status="failed",
                errors=[{"code": "E_AGENT_UNAVAILABLE", "message": f"{spec.owner} 不可用"}],
            )

        total_budget = 0
        last_result: StageResult | None = None
        for attempt in range(2):
            try:
                result = fn(ctx)
            except Exception as exc:
                result = StageResult(
                    stage=state.phase,
                    status="failed",
                    errors=[{"code": "E_AGENT_UNAVAILABLE", "message": f"{type(exc).__name__}: {exc}"}],
                )
            total_budget += int(result.budget_used or 0)
            result.budget_used = total_budget
            last_result = result

            if result.status != "ok":
                break
            errors = validate_stage_result(result, spec.output_artifacts, state.run_id, state.phase)
            if not errors:
                return result
            for err in errors:
                emitter.emit("artifact.invalid", stage=state.phase, actor=spec.owner, payload_ref=err)
            if attempt == 1:
                state.status = "aborted"
                state.termination_reason = "error"
                state.phase = "ABORTED"
                state.pending = {"errors": errors}
                break

        if last_result is not None and last_result.status == "failed":
            state.status = "aborted"
            state.termination_reason = "error"
            state.phase = "ABORTED"
            state.pending = {"errors": last_result.errors}
            return None
        return last_result

    def _run_p1(self, state: RunState) -> StageResult:
        """P1 由编排层自己产 idea_card：想法是人给的，机器不代笔（§6.1）。"""
        idea = {
            "idea_text": self.config.idea_text,
            "author": self.config.author,
            "constraints": list(state.constraints),
        }
        return StageResult(
            stage="P1",
            status="ok",
            artifacts={"idea_card": make_artifact(idea, state.run_id, "P1", "scheduler")},
            budget_used=0,
        )

    # ------------------------------------------------------------ 闸门
    def evaluate(self, state: RunState) -> dict[str, Any]:
        artifacts = self._all_artifacts(state)
        return self.evaluator.evaluate(state, artifacts, state.ledger, self.flow, self.config)

    def apply_transition(self, state: RunState, verdict: dict[str, Any]) -> None:
        action = str(verdict.get("action") or "stay")
        transition = dict(verdict.get("transition") or {})
        to_phase = str(verdict.get("to") or state.phase)

        if action in ("advance", "rework"):
            if to_phase not in STAGES:
                raise RuntimeError(f"非法转移目标 {to_phase}")
            state.phase = to_phase
            state.status = "running"
            state.round_index = state.ledger.round_of(to_phase) + 1
            state.guard_signals = []
            state.pending = {}

        elif action == "freeze":
            state.phase = "BASELINE_FROZEN"
            state.status = "frozen"
            state.termination_reason = str(transition.get("reason") or "baseline_signed")
            state.pending = {}

        elif action == "wait_human":
            state.phase = "HUMAN_GATE"
            state.status = "waiting_human"
            state.pending = {
                "from": verdict.get("from"),
                "to": to_phase,
                "gate_id": verdict.get("gate_id"),
                "reason": verdict.get("reason"),
            }

        elif action == "escalate":
            state.status = "escalated"
            phase = str(transition.get("phase") or "HUMAN_GATE")
            state.phase = phase if phase in ("HUMAN_GATE", "ABORTED") else "HUMAN_GATE"
            state.termination_reason = str(transition.get("reason") or verdict.get("reason") or "error")
            state.pending = {
                "from": verdict.get("from"),
                "to": to_phase,
                "gate_id": verdict.get("gate_id"),
                "reason": verdict.get("reason"),
            }

        elif action == "abort":
            state.status = "aborted"
            state.phase = "ABORTED"
            state.termination_reason = str(transition.get("reason") or "human_abort")

        else:  # stay
            if transition.get("increment_round"):
                state.ledger.bump_round(state.phase)
                state.round_index = state.ledger.round_of(state.phase) + 1

        # increment：改计数器，不改阶段
        increment = transition.get("increment")
        if increment:
            if increment == "p5_revision_count":
                state.ledger.p5_revision_count += 1
            elif increment == "rework_count":
                state.ledger.rework_count += 1
            elif isinstance(increment, str) and hasattr(state.ledger, increment):
                setattr(state.ledger, increment, getattr(state.ledger, increment) + 1)

        # 人工原因广播成新约束
        broadcast = transition.get("broadcast_constraints_from")
        if broadcast:
            last = state.human_decisions[-1] if state.human_decisions else {}
            reason = str(last.get("reason") or "")
            if reason and reason not in state.constraints:
                state.constraints.append(reason)

        # 一条人工动作只能被闸门消费一次：否则同一条 reject/sign 会在下一轮被反复判
        human_checks = {str(c.get("fn")) for c in (verdict.get("checks") or [])}
        if action != "wait_human" and human_checks & {"human_rejected", "human_signed"}:
            for decision in reversed(state.human_decisions):
                if str(decision.get("action")) in ("reject", "sign"):
                    decision["consumed"] = True
                    break

        state.last_gate_verdict = verdict
        state.touch()

    # ------------------------------------------------------------ 主循环
    def run(
        self,
        state: RunState | None = None,
        until: str | None = None,
        max_steps: int | None = None,
        human_script: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        from .human_gate import apply_human_decision, resolve_after_decision

        state = state or self.load_or_init()
        max_steps = int(max_steps or self.config.limit("max_steps", 60))
        script = list(human_script or [])
        steps = 0

        while True:
            reason = budget_mod.should_terminate(state, state.ledger, self.config)
            if reason:
                return self.finalize(state, reason)

            if until and state.phase == until:
                return self.finalize(state, None, note=f"stopped_at_{until}")

            if state.status == "waiting_human":
                if script:
                    item = script.pop(0)
                    apply_human_decision(
                        state,
                        str(item.get("action")),
                        reason=str(item.get("reason") or ""),
                        by=str(item.get("by") or "script"),
                    )
                    resolve_after_decision(state, self.config)
                    self.store.save_state(state)
                    continue
                self.store.save_state(state)
                return self.finalize(state, None)

            if state.status in ("frozen", "aborted", "escalated"):
                return self.finalize(state, state.termination_reason)

            if steps >= max_steps:
                state.status = "aborted"
                state.phase = "ABORTED"
                state.termination_reason = "error"
                state.pending = {"error": f"超过最大步数 {max_steps}，疑似死循环"}
                return self.finalize(state, "error")

            self.store.save_state(state)
            self.step(state)
            self.store.save_state(state)
            steps += 1

    # ------------------------------------------------------------ 收尾
    def finalize(self, state: RunState, reason: str | None, note: str | None = None) -> dict[str, Any]:
        if reason == "baseline_signed":
            state.status = "frozen"
            state.phase = "BASELINE_FROZEN"
        elif reason == "human_abort":
            state.status = "aborted"
            state.phase = "ABORTED"
        elif reason:
            state.termination_reason = state.termination_reason or reason

        self.store.save_state(state)
        emitter = EventEmitter(self.store, state.run_id, state.ledger.global_used)
        emitter.emit("run.terminated", stage=state.phase, actor="scheduler", payload_ref=str(reason or state.status))

        baseline = self.store.load_artifact(state.run_id, "baseline", state.artifacts_index) or {}
        baseline_ref = ""
        if isinstance(baseline, dict) and (baseline.get("data") or {}).get("version"):
            baseline_ref = f"baseline:{baseline['data']['version']}"

        result: dict[str, Any] = {
            "run_id": state.run_id,
            "status": state.status,
            "final_phase": state.phase,
            "termination_reason": state.termination_reason or reason,
            "baseline_ref": baseline_ref,
            "artifacts_index": dict(state.artifacts_index),
            "budget": {
                "global_used": state.ledger.global_used,
                "global_limit": state.ledger.global_limit,
                "per_stage_used": dict(state.ledger.per_stage_used),
            },
            "rounds": dict(state.ledger.rounds),
            "human_decisions": list(state.human_decisions),
            "events_ref": self.store.events_path(state.run_id).as_posix(),
            "errors": list(((state.pending or {}).get("errors") or [])),
            "gate": state.last_gate_verdict,
            "updated_at": now_iso(),
        }
        if note:
            result["note"] = note
        if (state.pending or {}).get("error"):
            result["errors"] = [{"code": "E_GATE_UNSATISFIED", "message": state.pending["error"]}]
        return result

    # ------------------------------------------------------------ 断点
    def status(self, run_id: str | None = None) -> dict[str, Any]:
        state = self.store.load_state(run_id or self.config.run_id)
        if state is None:
            return {"run_id": run_id or self.config.run_id, "status": "not_found"}
        return {
            "run_id": state.run_id,
            "status": state.status,
            "phase": state.phase,
            "round_index": state.round_index,
            "budget": {
                "global_used": state.ledger.global_used,
                "global_limit": state.ledger.global_limit,
                "per_stage_used": dict(state.ledger.per_stage_used),
            },
            "rounds": dict(state.ledger.rounds),
            "rework_count": state.ledger.rework_count,
            "p5_revision_count": state.ledger.p5_revision_count,
            "last_gate": state.last_gate_verdict,
            "constraints": list(state.constraints),
            "artifacts": sorted(state.artifacts_index.keys()),
        }


def boot(config: RunConfig) -> Runtime:
    return Runtime.boot(config)
