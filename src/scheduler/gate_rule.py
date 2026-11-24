"""闸门规则加载与求值（architecture §4.2 / §4.5）。

gate_rule.yaml 只写函数名和参数，函数体在 gate_check.py 的白名单注册表里，
所以这里没有任何 eval 或动态执行用户配置的代码。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .context import BudgetLedger, RunState, now_iso
from .gate_check import GateEnv, get_fn


@dataclass
class GateSpec:
    id: str
    from_phase: str
    to_phase: str
    priority: int
    checks: list[dict[str, Any]]
    on_pass: dict[str, Any] = field(default_factory=dict)
    on_fail: dict[str, Any] = field(default_factory=dict)


@dataclass
class TerminationSpec:
    id: str
    check: dict[str, Any]
    action: dict[str, Any]


@dataclass
class RuleSet:
    defaults: dict[str, Any]
    terminations: list[TerminationSpec]
    gates: list[GateSpec]
    path: Path

    def gates_from(self, phase: str) -> list[GateSpec]:
        return sorted(
            [g for g in self.gates if g.from_phase == phase],
            key=lambda g: g.priority,
            reverse=True,
        )

    def gate_ids(self) -> set[str]:
        return {g.id for g in self.gates}


def load_rules_dict(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"闸门规则不存在: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"闸门规则顶层必须是映射: {p}")
    return data


def load_rules(path: str | Path) -> RuleSet:
    p = Path(path)
    raw = load_rules_dict(p)
    terminations = [
        TerminationSpec(
            id=str(t.get("id")),
            check=dict(t.get("check") or {}),
            action=dict(t.get("action") or {}),
        )
        for t in (raw.get("terminations") or [])
    ]
    gates = [
        GateSpec(
            id=str(g.get("id")),
            from_phase=str(g.get("from")),
            to_phase=str(g.get("to")),
            priority=int(g.get("priority", 10)),
            checks=[dict(c) for c in (g.get("checks") or [])],
            on_pass=dict(g.get("on_pass") or {"type": "advance"}),
            # 注意：on_fail 缺省要留空 dict，不能给 {"type":"stay"}——
            # 否则「找第一条带 on_fail 的 gate」会把只有 on_pass 的 gate 也当成兜底，
            # 结果该追加轮次的 stay 变成了不追加轮次的 stay（死循环）。
            on_fail=dict(g.get("on_fail") or {}),
        )
        for g in (raw.get("gates") or [])
    ]
    return RuleSet(
        defaults=dict(raw.get("defaults") or {}),
        terminations=terminations,
        gates=gates,
        path=p,
    )


class GateEvaluator:
    """按 §4.5 的优先级求值：终止类 > 人工类 > 回炉类 > 前进类 > stay。"""

    def __init__(self, rules: RuleSet) -> None:
        self.rules = rules

    # ------------------------------------------------------------ 单条
    def _run_checks(self, checks: list[dict[str, Any]], env: GateEnv) -> tuple[bool, list[dict[str, Any]]]:
        results: list[dict[str, Any]] = []
        all_true = True
        for check in checks:
            name = str(check.get("fn"))
            args = dict(check.get("args") or {})
            fn = get_fn(name)
            try:
                ok, detail = fn(env, args)
            except Exception as exc:  # 闸门函数自身异常 → 判 false 并留痕，不让引擎崩
                ok, detail = False, {"error": f"{type(exc).__name__}: {exc}"}
            results.append({"fn": name, "result": bool(ok), "detail": detail})
            all_true = all_true and bool(ok)
        return all_true, results

    # ------------------------------------------------------------ 求值
    def evaluate(
        self,
        state: RunState,
        artifacts: dict[str, Any],
        ledger: BudgetLedger,
        flow: Any = None,
        config: Any = None,
    ) -> dict[str, Any]:
        env = GateEnv(artifacts=artifacts, ledger=ledger, state=state, flow=flow, config=config)

        # 1) 终止类无条件先判
        for t in self.rules.terminations:
            ok, results = self._run_checks([t.check], env)
            if ok:
                return self._verdict(
                    gate_id=t.id,
                    from_phase=state.phase,
                    to_phase=str(t.action.get("phase") or "HUMAN_GATE"),
                    action=str(t.action.get("type") or "escalate"),
                    reason=str(t.action.get("reason") or t.id),
                    checks=results,
                    transition=t.action,
                )

        # 2) 当前阶段全部 gate，按 priority 降序，取第一条 checks 全为 true 的
        candidates = self.rules.gates_from(state.phase)
        partial: tuple[GateSpec, list[dict[str, Any]]] | None = None
        for gate in candidates:
            ok, results = self._run_checks(gate.checks, env)
            if ok:
                return self._verdict(
                    gate_id=gate.id,
                    from_phase=gate.from_phase,
                    to_phase=gate.to_phase,
                    action=str(gate.on_pass.get("type") or "advance"),
                    reason=str(gate.on_pass.get("reason") or "checks_passed"),
                    checks=results,
                    transition=gate.on_pass,
                )
            # 「部分命中」的口径：首条 check 是触发条件，后面都是守门条件。
            # 只有触发条件成立、守门条件没过的才算法部分命中，它的 on_fail 才是对症的去向。
            if partial is None and results and results[0]["result"]:
                partial = (gate, results)

        # 3) 都没命中：优先用「部分命中」那条的 on_fail（它能给出更有针对性的去向，
        #    例如 P6 覆盖有空行 → 退 P3 补需求，而不是原地 stay）
        if partial is not None:
            gate, results = partial
            return self._verdict(
                gate_id=gate.id,
                from_phase=gate.from_phase,
                to_phase=str(gate.on_fail.get("to") or gate.to_phase),
                action=str(gate.on_fail.get("type") or "stay"),
                reason=str(gate.on_fail.get("reason") or "checks_failed"),
                checks=results,
                transition=gate.on_fail,
            )

        fallback = next((g for g in candidates if g.on_fail), None)
        if fallback is not None:
            return self._verdict(
                gate_id=fallback.id,
                from_phase=fallback.from_phase,
                to_phase=str(fallback.on_fail.get("to") or fallback.to_phase),
                action=str(fallback.on_fail.get("type") or self.rules.defaults.get("on_no_match", "stay")),
                reason=str(fallback.on_fail.get("reason") or "no_gate_matched"),
                checks=[],
                transition=fallback.on_fail,
            )

        return self._verdict(
            gate_id="G_NO_MATCH",
            from_phase=state.phase,
            to_phase=state.phase,
            action="stay",
            reason="no_gate_matched",
            checks=[],
            transition={"type": "stay"},
        )

    @staticmethod
    def _verdict(
        gate_id: str,
        from_phase: str,
        to_phase: str,
        action: str,
        reason: str,
        checks: list[dict[str, Any]],
        transition: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "gate_id": gate_id,
            "from": from_phase,
            "to": to_phase,
            "action": action,
            "reason": reason,
            "checks": checks,
            "transition": dict(transition),
            "evaluated_at": now_iso(),
        }
