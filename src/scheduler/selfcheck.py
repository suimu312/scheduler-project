"""architecture §15 验收用例的可执行版本。

不联网、不调用真实模型：用 mocks.py 的离线桩把状态机整条链路跑起来，
逐条断言「闸门是否按文档判、状态是否停在该停的地方、预算与轮次是否生效」。

    python -m src.scheduler.cli selfcheck
"""

from __future__ import annotations

import json
import re
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


def case_task_dep_cycle(run_root: Path) -> dict[str, Any]:
    """A-14：dev_task 依赖成环 → G_P6_TASKS 命中，停在 P6 重新分解，不进入排期。"""
    rt, state, _ = _fresh("taskcycle", run_root, {"p6_cycle": True})
    rt.run(state, until="P6")
    verdict = rt.step(state)
    errors = ((verdict.get("checks") or [{}])[0].get("detail") or {}).get("errors") or []
    codes = sorted({str(e.get("code")) for e in errors})
    no_schedule = not (state.artifacts_index.get("schedule"))
    ok = (
        verdict["gate_id"] == "G_P6_TASKS"
        and verdict["action"] == "stay"
        and state.phase == "P6"
        and "E_DEP_CYCLE" in codes
        and no_schedule
    )
    return _result(
        "A-14 依赖成环 → 中止排期并原地重新分解",
        ok,
        {"gate": verdict["gate_id"], "action": verdict["action"], "phase": state.phase, "codes": codes, "schedule_absent": no_schedule},
    )


def case_wave_parallel(run_root: Path) -> dict[str, Any]:
    """A-15：六张卡互不依赖 → 一个波次并行 6 张，超上限记警告，排期照样算得出。"""
    rt, state, _ = _fresh("wave", run_root, {"p6_wide": True})
    res = rt.run(state, human_script=[{"action": "sign"}])
    sched = rt.store.load_artifact(state.run_id, "schedule", state.artifacts_index) or {}
    data = sched.get("data") or {}
    widths = [int(w.get("width") or 0) for w in (data.get("waves") or [])]
    codes = sorted({str(w.get("code")) for w in (data.get("warnings") or [])})
    ok = (
        res["status"] == "frozen"
        and widths[:1] == [6]
        and int(data.get("max_wave_width_actual") or 0) == 6
        and "E_WAVE_TOO_WIDE" in codes
    )
    return _result(
        "A-15 波次并行度 → 超上限被标记",
        ok,
        {"status": res["status"], "widths": widths, "warnings": codes, "limit": data.get("max_wave_width")},
    )


def case_critical_path(run_root: Path) -> dict[str, Any]:
    """A-16：关键路径 = 001→002→004→007→008，总工期 12 天，5 个波次。"""
    rt, state, _ = _fresh("cpath", run_root)
    res = rt.run(state, human_script=[{"action": "sign"}])
    sched = rt.store.load_artifact(state.run_id, "schedule", state.artifacts_index) or {}
    data = sched.get("data") or {}
    cp = data.get("critical_path") or {}
    widths = [int(w.get("width") or 0) for w in (data.get("waves") or [])]
    ok = (
        res["status"] == "frozen"
        and cp.get("task_ids") == ["DEV-001", "DEV-002", "DEV-004", "DEV-007", "DEV-008"]
        and abs(float(cp.get("total_days") or 0) - 12.0) < 1e-9
        and abs(float(data.get("total_days") or 0) - 12.0) < 1e-9
        and widths == [1, 3, 2, 1, 1]
    )
    return _result(
        "A-16 关键路径与总工期",
        ok,
        {"critical_path": cp.get("task_ids"), "total_days": data.get("total_days"), "widths": widths, "status": res["status"]},
    )


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


def case_llm_injection(run_root: Path) -> dict[str, Any]:
    """A-17 模型注入口：set_provider 即时生效；模块调 ctx.ask 自动计入本阶段账本。"""
    from . import llm_gateway as lg
    from . import modules as modules_mod

    # 1) 直接注入：换 provider 不动调用点
    gw = lg.LLMGateway()
    hits: list[tuple[str, str]] = []

    def fake(agent_id: str, prompt: str, stage: str) -> str:
        hits.append((agent_id, stage))
        return "OK"

    gw.set_provider(fake, name="test")
    out = gw.call("A2", "hi", "P3")
    seam = out == "OK" and gw.name == "test" and hits == [("A2", "P3")] and gw.stage_calls("P3") == 1

    # 2) 适配组员 B 的 HttpModel 形态（dict 进 dict 出）
    class FakeModel:
        def __call__(self, payload: dict[str, Any], timeout: int = 120) -> dict[str, str]:
            return {"text": f"echo:{payload['agent_id']}:{payload['step']}"}

    adapted = lg.adapt_model(FakeModel())("A4", "hi", "P4")

    # 3) 运行时：模块不自报，靠 Gateway 实测进账；阶段外的调用不串账
    rt, state, _ = _fresh("llm", run_root)
    rt.set_provider(lambda agent_id, prompt, stage: "X", name="test")
    rt.gateway.call("A2", "pre", "P2")

    original = modules_mod.MOCK_REGISTRY["solution_gen"]

    def counting_module(ctx):
        res = original(ctx)
        res.budget_used = 0
        ctx.ask("A4", "方案一")
        ctx.ask("A4", "方案二")
        return res

    modules_mod.MOCK_REGISTRY["solution_gen"] = counting_module
    try:
        rt.run(state, until="P5")
    finally:
        modules_mod.MOCK_REGISTRY["solution_gen"] = original

    charged = state.ledger.used_of("P4")
    ok = seam and adapted == "echo:A4:P4" and charged == 2
    return _result(
        "模型注入口可注入且自动计费",
        ok,
        {"seam": seam, "adapted": adapted, "p4_charged": charged, "gateway_calls": rt.gateway.calls},
    )


def case_resource_leveling(run_root: Path) -> dict[str, Any]:
    """W5：同负责人同波次两张卡 → 串行顺延，冲突条目写清顺延卡/原因/顺延天数。"""
    from . import taskgraph

    tasks = [
        {
            "task_id": "DEV-A",
            "title": "前端列表",
            "module": "web",
            "owner_role": "frontend",
            "estimate_days": 3,
            "depends_on": [],
            "acceptance": ["列表可渲染"],
            "feature_refs": ["F1"],
        },
        {
            "task_id": "DEV-B",
            "title": "前端详情",
            "module": "web",
            "owner_role": "frontend",
            "estimate_days": 3,
            "depends_on": [],
            "acceptance": ["详情可打开"],
            "feature_refs": ["F2"],
        },
    ]
    sched = taskgraph.build_schedule(tasks, ["F1", "F2"])
    conflicts = sched["resource_conflicts"]
    b = next((t for t in sched["tasks"] if t["task_id"] == "DEV-B"), {})
    ok = (
        len(conflicts) == 1
        and conflicts[0]["delayed_task"] == "DEV-B"
        and conflicts[0]["blocked_by"] == "DEV-A"
        and bool(conflicts[0]["reason"])
        and float(conflicts[0]["shift_days"]) == 3.0
        and float(b.get("start_day") or 0) == 4.0
        and float(b.get("end_day") or 0) == 6.0
        and abs(float(sched["total_days"]) - 6.0) < 1e-9
    )
    return _result(
        "W5 同负责人撞车 → 串行顺延并写明原因",
        ok,
        {"conflicts": conflicts, "B": b, "total_days": sched["total_days"]},
    )


def case_spans_recorded(run_root: Path) -> dict[str, Any]:
    """O1：一次运行写出 spans.jsonl，trace_id 对齐 run_id，stage/gate/model 三类 span 齐全。"""
    from . import modules as modules_mod

    rt, state, _ = _fresh("spans", run_root)
    original = modules_mod.MOCK_REGISTRY["solution_gen"]

    def counting_module(ctx):
        res = original(ctx)
        res.budget_used = 0
        ctx.ask("A4", "方案一")
        return res

    modules_mod.MOCK_REGISTRY["solution_gen"] = counting_module
    try:
        rt.run(state, human_script=[{"action": "sign"}])
    finally:
        modules_mod.MOCK_REGISTRY["solution_gen"] = original

    spans = rt.store.read_spans(state.run_id)
    kinds = {str(sp.get("kind")) for sp in spans}
    trace_ok = all(str(sp.get("trace_id")) == state.run_id for sp in spans)
    ok = bool(spans) and trace_ok and {"stage", "gate", "model"} <= kinds
    return _result(
        "O1 spans.jsonl 追踪链完整",
        ok,
        {"spans": len(spans), "kinds": sorted(kinds), "trace_ok": trace_ok},
    )


def case_command_fork(run_root: Path) -> dict[str, Any]:
    """O2：命令 issued/applied 成对；retry_task fork 出 .fork.1 且血缘指向父运行。"""
    from . import commands as cmd_mod
    from .events import EventEmitter

    rt, state, cfg = _fresh("cmd", run_root)
    store = rt.store
    emitter = EventEmitter(store, state.run_id, state.ledger.global_used)

    pause = cmd_mod.issue_command(state, "pause", note="演示暂停", by="tester")
    emitter.emit("command.issued", stage=state.phase, actor="tester", payload_ref="pause",
                 detail={"command_id": pause["command_id"], "target": "", "note": "演示暂停"})
    pause_effect = cmd_mod.apply_command(store, cfg, state, pause)
    emitter.emit("command.applied", stage=state.phase, actor="scheduler", payload_ref="pause",
                 detail={"command_id": pause["command_id"], "effect": pause_effect})

    retry = cmd_mod.issue_command(state, "retry_task", target="DEV-001", note="单卡重试", by="tester")
    emitter.emit("command.issued", stage=state.phase, actor="tester", payload_ref="retry_task",
                 detail={"command_id": retry["command_id"], "target": "DEV-001", "note": "单卡重试"})
    retry_effect = cmd_mod.apply_command(store, cfg, state, retry)
    emitter.emit("command.applied", stage=state.phase, actor="scheduler", payload_ref="retry_task",
                 detail={"command_id": retry["command_id"], "effect": retry_effect})
    store.save_state(state)

    events = store.read_events(state.run_id)
    issued = {str(e["detail"]["command_id"]) for e in events if e["event_type"] == "command.issued"}
    applied = {str(e["detail"]["command_id"]) for e in events if e["event_type"] == "command.applied"}
    fork_id = str(retry_effect.get("fork_run_id") or "")
    fork_state = store.load_state(fork_id) if fork_id else None
    ok = (
        issued == applied
        and len(issued) == 2
        and bool(re.fullmatch(r".+\.fork\.\d+", fork_id))
        and fork_state is not None
        and fork_state.parent_run_id == state.run_id
        and state.status == "waiting_human"
    )
    return _result(
        "O2 命令成对落事件且 retry_task fork 血缘正确",
        ok,
        {"issued": sorted(issued), "applied": sorted(applied), "fork_id": fork_id, "pause_effect": pause_effect},
    )


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
    ("task_cycle", case_task_dep_cycle),
    ("wave_width", case_wave_parallel),
    ("critical_path", case_critical_path),
    ("p7_rework", case_p7_rework_limit),
    ("budget", case_budget),
    ("resume", case_resume),
    ("schema", case_schema_invalid),
    ("llm_injection", case_llm_injection),
    ("resource_leveling", case_resource_leveling),
    ("spans", case_spans_recorded),
    ("command_fork", case_command_fork),
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
