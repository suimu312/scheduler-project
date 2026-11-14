"""architecture §15 验收用例的可执行版本。

不联网、不调用真实模型：用 mocks.py 的离线桩把状态机整条链路跑起来，
逐条断言「闸门是否按文档判、状态是否停在该停的地方、预算与轮次是否生效」。

    python -m src.scheduler.cli selfcheck
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable

from . import mocks
from .context import RunConfig, RunState, validate_artifact
from .statemachine import Runtime

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEDULER_DIR = REPO_ROOT / "src" / "scheduler"
IDEA = "想做一个给小团队用的内部协作工具，能把讨论和文件都收在一处"


def _config(run_id: str, run_root: Path, budget: int = 30, per_stage: dict[str, int] | None = None) -> RunConfig:
    cfg = RunConfig(
        run_id=run_id,
        idea_text=IDEA,
        flow_ref=str(SCHEDULER_DIR / "flow.yaml"),
        gate_ref=str(SCHEDULER_DIR / "gate_rule.yaml"),
        agent_ref=str(REPO_ROOT / "src" / "agents" / "agent_config.yaml"),
        run_root=str(run_root),
        provider="mock",
    )
    cfg.budget["global_llm_calls"] = budget
    if per_stage:
        cfg.budget["per_stage"] = {**cfg.budget["per_stage"], **per_stage}
    return cfg


def _fresh(
    case: str,
    run_root: Path,
    scenario: dict[str, Any] | None = None,
    budget: int = 30,
    per_stage: dict[str, int] | None = None,
):
    run_id = f"SC-{case}"
    target = run_root / run_id
    if target.exists():
        shutil.rmtree(target)
    mocks.reset_scenario()
    if scenario:
        mocks.set_scenario("", **scenario)
    cfg = _config(run_id, run_root, budget, per_stage)
    rt = Runtime.boot(cfg)
    state = rt.load_or_init()
    return rt, state, cfg


def _result(name: str, ok: bool, detail: Any) -> dict[str, Any]:
    return {"case": name, "pass": bool(ok), "detail": detail}


# ---------------------------------------------------------------- 用例

def case_normal_flow(run_root: Path) -> dict[str, Any]:
    """正常全流程：P1→P7→BASELINE_FROZEN，termination=baseline_signed，基线 v1.0。"""
    rt, state, _ = _fresh("normal", run_root)
    res = rt.run(state, human_script=[{"action": "sign"}])
    ok = (
        res["status"] == "frozen"
        and res["final_phase"] == "BASELINE_FROZEN"
        and res["termination_reason"] == "baseline_signed"
        and res["baseline_ref"] == "baseline:v1.0"
    )
    return _result("正常全流程 → 基线冻结", ok, res)


def case_p3_escalate(run_root: Path) -> dict[str, Any]:
    """P3 一比一冲突（L3）：进 HUMAN_GATE，不自动裁决。"""
    rt, state, _ = _fresh("p3escalate", run_root, {"conflict_escalate": True})
    res = rt.run(state)
    gate = (res.get("gate") or {}).get("gate_id")
    ok = res["status"] == "waiting_human" and res["final_phase"] == "HUMAN_GATE" and gate == "G_P3_HUMAN"
    return _result("P3 一比一冲突 → 挂起交人", ok, res)


def case_p3_max_rounds(run_root: Path) -> dict[str, Any]:
    """P3 满三轮仍有未决冲突：G_P3_P4 仍放行，轮次记到 2（第 3 轮开始前）。"""
    rt, state, _ = _fresh("p3rounds", run_root, {"conflict_open_rounds": 2})
    res = rt.run(state, until="P4")
    ok = res.get("note") == "stopped_at_P4" and res["rounds"].get("P3") == 2
    return _result("P3 满三轮先有未决 → 按原文进 P4", ok, {"note": res.get("note"), "rounds": res["rounds"]})


def case_p4_mismatch(run_root: Path) -> dict[str, Any]:
    """P4 三套方案功能清单不一致：G_P4_P5 false，stay P4。"""
    rt, state, _ = _fresh("p4mismatch", run_root, {"p4_mismatch": True})
    rt.run(state, until="P4")
    verdict = rt.step(state)
    ok = verdict["gate_id"] == "G_P4_P5" and verdict["action"] == "stay" and state.phase == "P4"
    return _result("P4 功能清单不一致 → 原地重生成", ok, {"gate": verdict, "phase": state.phase})


def case_p5_stddev(run_root: Path) -> dict[str, Any]:
    """P5 总分过线但标准差超标：G_P5_P6 false，按 category=solution 退 P4。"""
    rt, state, _ = _fresh("p5stddev", run_root, {"p5_mode": "stddev", "p5_category": "solution"})
    rt.run(state, until="P5")
    verdict = rt.step(state)
    ok = verdict["gate_id"] == "G_P5_REWORK_SOL" and state.phase == "P4" and state.ledger.p5_revision_count == 1
    return _result("P5 标准差超标 → 退 P4 改方案", ok, {"gate": verdict["gate_id"], "phase": state.phase, "revisions": state.ledger.p5_revision_count})


def case_p5_requirement_rewind(run_root: Path) -> dict[str, Any]:
    """P5 需求层矛盾：G_P5_REWORK_REQ 退 P3 只开定点质询。"""
    rt, state, _ = _fresh("p5req", run_root, {"p5_mode": "low_total", "p5_category": "requirement"})
    rt.run(state, until="P5")
    verdict = rt.step(state)
    ok = verdict["gate_id"] == "G_P5_REWORK_REQ" and state.phase == "P3"
    return _result("P5 需求层矛盾 → 退 P3 定点质询", ok, {"gate": verdict["gate_id"], "phase": state.phase})


def case_p5_revision_limit(run_root: Path) -> dict[str, Any]:
    """P5 修改达 3 次：G_P5_HUMAN 优先于 G_P5_P6，交人。"""
    rt, state, _ = _fresh("p5limit", run_root, {"p5_mode": "stddev"})
    rt.run(state, until="P5")
    state.ledger.p5_revision_count = 3
    verdict = rt.step(state)
    ok = verdict["gate_id"] == "G_P5_HUMAN" and state.status == "waiting_human"
    return _result("P5 修改达 3 次 → 交人", ok, {"gate": verdict["gate_id"], "status": state.status})


def case_p6_gap(run_root: Path) -> dict[str, Any]:
    """P6 覆盖矩阵有空行：G_P6_P7 false，退回补需求。"""
    rt, state, _ = _fresh("p6gap", run_root, {"p6_gap": True})
    rt.run(state, until="P6")
    verdict = rt.step(state)
    ok = verdict["gate_id"] == "G_P6_P7" and state.phase == "P3"
    return _result("P6 覆盖有空行 → 不进 P7", ok, {"gate": verdict["gate_id"], "action": verdict["action"], "phase": state.phase})


def case_p6_missing_acceptance(run_root: Path) -> dict[str, Any]:
    """P6 有需求缺验收标准：G_P6_P7 false（A 自己扫一遍，不信 E 的自检）。"""
    rt, state, _ = _fresh("p6ac", run_root, {"p6_missing_acceptance": True})
    rt.run(state, until="P6")
    verdict = rt.step(state)
    detail = next((c["detail"] for c in verdict["checks"] if c["fn"] == "all_have_acceptance"), {})
    ok = verdict["gate_id"] == "G_P6_P7" and detail.get("missing") == ["REQ-001"]
    return _result("P6 缺验收标准 → 不进 P7", ok, {"gate": verdict["gate_id"], "missing": detail.get("missing")})


def case_p7_rework_limit(run_root: Path) -> dict[str, Any]:
    """P7 打回：第 1 次回 P3 且带原因，第 3 次 → escalated，不再回 P3。"""
    # 打回会重跑 P3→P6，为的是验闸门而不是验预算，所以给这一条放大预算
    rt, state, _ = _fresh(
        "p7rework",
        run_root,
        budget=80,
        per_stage={"P3": 24, "P4": 12, "P5": 16, "P6": 8, "P7": 6},
    )
    script = [
        {"action": "reject", "reason": "站内消息不需要已读回执"},
        {"action": "reject", "reason": "还是不需要已读回执"},
        {"action": "reject", "reason": "第三次仍然不接受"},
    ]
    res = rt.run(state, human_script=script)
    ok = (
        res["status"] == "escalated"
        and res["termination_reason"] == "rework_limit"
        and state.ledger.rework_count >= 2
        and any("已读回执" in c for c in state.constraints)
    )
    return _result("P7 打回至上限 → escalated", ok, {"status": res["status"], "reason": res["termination_reason"], "rework_count": state.ledger.rework_count, "constraints": state.constraints})


def case_budget(run_root: Path) -> dict[str, Any]:
    """预算耗尽：T_BUDGET 先于所有前进类闸门。"""
    rt, state, _ = _fresh("budget", run_root, budget=6)
    res = rt.run(state)
    ok = res["status"] == "escalated" and res["termination_reason"] == "budget_exhausted" and res["budget"]["global_used"] >= 6
    return _result("预算耗尽 → 交人", ok, {"status": res["status"], "reason": res["termination_reason"], "budget": res["budget"]})


def case_resume(run_root: Path) -> dict[str, Any]:
    """进程中断恢复：跑到 P6 停，重建 Runtime 后从 state.json 接着跑完，不重跑已完成阶段。"""
    rt, state, cfg = _fresh("resume", run_root)
    first = rt.run(state, until="P6")
    reloaded = Runtime.boot(cfg)
    st2 = reloaded.load_or_init()
    same_phase = st2.phase == "P6"
    res = reloaded.run(st2, human_script=[{"action": "sign"}])
    ok = same_phase and res["status"] == "frozen"
    return _result("中断后从 state.json 恢复", ok, {"resumed_phase": st2.phase, "status": res["status"]})


def case_schema_invalid(run_root: Path) -> dict[str, Any]:
    """缺 meta 的产物：A 的校验器直接拒收（E_SCHEMA_INVALID）。"""
    errors_bad = validate_artifact({"data": {}})
    errors_missing_key = validate_artifact({"meta": {"run_id": "R1"}, "data": {}})
    errors_ok = validate_artifact(
        {
            "meta": {
                "schema_version": "1.0",
                "run_id": "R1",
                "stage": "P3",
                "produced_by": "agents",
                "created_at": "2026-11-10T14:02:31+08:00",
            },
            "data": {},
            "extensions": {},
        }
    )
    ok = bool(errors_bad) and bool(errors_missing_key) and not errors_ok
    return _result("缺 meta 产物被拒收", ok, {"bad": errors_bad, "ok": errors_ok})


CASES: list[tuple[str, Callable[[Path], dict[str, Any]]]] = [
    ("normal", case_normal_flow),
    ("p3_escalate", case_p3_escalate),
    ("p3_max_rounds", case_p3_max_rounds),
    ("p4_mismatch", case_p4_mismatch),
    ("p5_stddev", case_p5_stddev),
    ("p5_requirement", case_p5_requirement_rewind),
    ("p5_revision_limit", case_p5_revision_limit),
    ("p6_gap", case_p6_gap),
    ("p6_acceptance", case_p6_missing_acceptance),
    ("p7_rework", case_p7_rework_limit),
    ("budget", case_budget),
    ("resume", case_resume),
    ("schema", case_schema_invalid),
]


def run_all(run_root: str | Path | None = None) -> list[dict[str, Any]]:
    root = Path(run_root or (REPO_ROOT / "runs" / "selfcheck"))
    root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for _name, fn in CASES:
        try:
            results.append(fn(root))
        except Exception as exc:  # 单条用例炸了不影响其它用例
            results.append(_result(_name, False, f"{type(exc).__name__}: {exc}"))
    mocks.reset_scenario()
    return results


if __name__ == "__main__":
    out = run_all()
    print(json.dumps(out, ensure_ascii=False, indent=2))
