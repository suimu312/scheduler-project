"""命令行入口（architecture §9.2）。

    python -m src.scheduler.cli start  --idea "..." [--run-id R0001]
    python -m src.scheduler.cli run    --run-id R0001 [--until P6]
    python -m src.scheduler.cli status --run-id R0001
    python -m src.scheduler.cli human  --run-id R0001 --action sign --reason "..."
    python -m src.scheduler.cli resume --run-id R0001
    python -m src.scheduler.cli replay --run-id R0001
    python -m src.scheduler.cli viz    --run-id R0001
    python -m src.scheduler.cli selfcheck

离线跑通用 --provider mock（默认）；接真实模块用 --provider real。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN_ROOT = REPO_ROOT / "runs"

from . import mocks  # noqa: E402
from .context import RunConfig, RunState, now_iso  # noqa: E402
from .events import replay as replay_events  # noqa: E402
from .human_gate import apply_human_decision, resolve_after_decision  # noqa: E402
from .statemachine import ConfigError, Runtime  # noqa: E402
from .store import RunStore  # noqa: E402

SCENARIOS: dict[str, dict[str, Any]] = {
    "happy": {},
    "p3_escalate": {"conflict_escalate": True},
    "p3_open": {"conflict_open_rounds": 2},
    "p4_mismatch": {"p4_mismatch": True},
    "p5_stddev": {"p5_mode": "stddev", "p5_category": "solution"},
    "p5_requirement": {"p5_mode": "low_total", "p5_category": "requirement"},
    "p5_veto": {"p5_mode": "veto"},
    "p6_gap": {"p6_gap": True},
    "p6_no_ac": {"p6_missing_acceptance": True},
    "budget": {"p3_burn": 30},
}


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _load_config(run_id: str, run_root: Path) -> RunConfig:
    path = RunStore(run_root).config_path(run_id)
    if not path.exists():
        raise SystemExit(f"找不到 {path}，请先执行 start 子命令")
    return RunConfig.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _new_config(args: argparse.Namespace, run_root: Path) -> RunConfig:
    cfg = RunConfig(
        run_id=args.run_id,
        idea_text=args.idea,
        author=args.author,
        flow_ref=str(REPO_ROOT / "src" / "scheduler" / "flow.yaml"),
        gate_ref=str(REPO_ROOT / "src" / "scheduler" / "gate_rule.yaml"),
        agent_ref=str(REPO_ROOT / "src" / "agents" / "agent_config.yaml"),
        run_root=str(run_root),
        provider=args.provider,
    )
    if args.budget:
        cfg.budget["global_llm_calls"] = int(args.budget)
    return cfg


# ---------------------------------------------------------------- 子命令

def cmd_start(args: argparse.Namespace) -> int:
    run_root = Path(args.run_root)
    cfg = _new_config(args, run_root)
    store = RunStore(run_root)
    store.ensure(cfg.run_id)
    store.save_config(cfg.run_id, cfg.to_dict())
    rt = Runtime.boot(cfg)
    state = rt.init_state()
    rt.store.save_state(state)
    print(_dump({"run_id": cfg.run_id, "phase": state.phase, "status": state.status, "config": cfg.to_dict()}))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    run_root = Path(args.run_root)
    cfg = _load_config(args.run_id, run_root)
    if args.scenario:
        if args.scenario not in SCENARIOS:
            raise SystemExit(f"未知场景 {args.scenario}，可选 {sorted(SCENARIOS)}")
        mocks.reset_scenario()
        mocks.set_scenario(args.scenario, **SCENARIOS[args.scenario])
    rt = Runtime.boot(cfg)
    state = rt.load_or_init()
    script = []
    if args.script:
        script = json.loads(Path(args.script).read_text(encoding="utf-8"))
    result = rt.run(state, until=args.until, max_steps=args.max_steps, human_script=script)
    print(_dump(result))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    run_root = Path(args.run_root)
    cfg = _load_config(args.run_id, run_root)
    rt = Runtime.boot(cfg)
    print(_dump(rt.status(args.run_id)))
    return 0


def cmd_human(args: argparse.Namespace) -> int:
    run_root = Path(args.run_root)
    cfg = _load_config(args.run_id, run_root)
    store = RunStore(run_root)
    state = store.load_state(args.run_id)
    if state is None:
        raise SystemExit(f"运行实例 {args.run_id} 不存在")
    decision = apply_human_decision(
        state,
        args.action,
        reason=args.reason or "",
        payload_ref=args.payload or "",
        by=args.by,
    )
    effect = resolve_after_decision(state, cfg)
    store.save_state(state)
    from .events import EventEmitter

    emitter = EventEmitter(store, args.run_id, state.ledger.global_used)
    emitter.emit("human.decision", stage=decision["at_phase"], actor="human", payload_ref=args.action)
    print(_dump({"decision": decision, "effect": effect, "hint": "执行 resume 或 run 继续"}))
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    args.scenario = getattr(args, "scenario", None)
    return cmd_run(args)


def cmd_replay(args: argparse.Namespace) -> int:
    events = replay_events(args.run_id, str(Path(args.run_root)))
    print(_dump({"run_id": args.run_id, "events": len(events), "tail": events[-8:] if events else []}))
    return 0


def cmd_viz(args: argparse.Namespace) -> int:
    """A 侧的文本兜底视图（正式渲染建议交给 E，Q1）。"""
    run_root = Path(args.run_root)
    events = replay_events(args.run_id, str(run_root))
    state = RunStore(run_root).load_state(args.run_id)
    lines = [f"run={args.run_id} status={state.status if state else '?'} phase={state.phase if state else '?'}"]
    if state:
        lines.append(f"budget={state.ledger.global_used}/{state.ledger.global_limit} rounds={state.ledger.rounds}")
    for e in events:
        lines.append(f"{e['ts']} {e['stage']:<5} {e['event_type']:<16} {e['actor']:<16} {e['payload_ref']}")
    print("\n".join(lines))
    return 0


def cmd_selfcheck(args: argparse.Namespace) -> int:
    from .selfcheck import run_all

    results = run_all(run_root=str(Path(args.run_root) / "selfcheck"))
    failures = [r for r in results if not r["pass"]]
    print(_dump({"total": len(results), "passed": len(results) - len(failures), "failed": len(failures), "cases": results}))
    return 1 if failures else 0


# ---------------------------------------------------------------- 解析

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scheduler", description="多智能体需求生成工作流 · 编排引擎 CLI")
    p.add_argument("--run-root", default=str(DEFAULT_RUN_ROOT), help="运行目录，默认 <repo>/runs")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start", help="创建一次运行实例")
    s.add_argument("--idea", required=True)
    s.add_argument("--run-id", default=f"R{now_iso()[:10].replace('-', '')}")
    s.add_argument("--author", default="user")
    s.add_argument("--provider", default="mock", choices=["mock", "real"])
    s.add_argument("--budget", type=int, default=None, help="全局 LLM 调用上限，默认 30")
    s.set_defaults(func=cmd_start)

    r = sub.add_parser("run", help="跑到阻塞点")
    r.add_argument("--run-id", required=True)
    r.add_argument("--until", default=None, choices=["P1", "P2", "P3", "P4", "P5", "P6", "P7"])
    r.add_argument("--max-steps", type=int, default=None)
    r.add_argument("--scenario", default=None, help=f"mock 场景：{sorted(SCENARIOS)}")
    r.add_argument("--script", default=None, help="人工动作脚本 JSON 路径")
    r.set_defaults(func=cmd_run)

    st = sub.add_parser("status", help="打印 phase / 预算 / 最近闸门")
    st.add_argument("--run-id", required=True)
    st.set_defaults(func=cmd_status)

    h = sub.add_parser("human", help="写入人工动作（不自动跑）")
    h.add_argument("--run-id", required=True)
    h.add_argument("--action", required=True, choices=["approve", "reject", "comment", "sign", "adjudicate", "abort"])
    h.add_argument("--reason", default="")
    h.add_argument("--payload", default="")
    h.add_argument("--by", default="user")
    h.set_defaults(func=cmd_human)

    rs = sub.add_parser("resume", help="人为动作后继续跑")
    rs.add_argument("--run-id", required=True)
    rs.add_argument("--until", default=None)
    rs.add_argument("--max-steps", type=int, default=None)
    rs.add_argument("--scenario", default=None)
    rs.add_argument("--script", default=None, help="人工动作脚本 JSON 路径（与 run 同口径）")
    rs.set_defaults(func=cmd_resume)

    rp = sub.add_parser("replay", help="从 events.jsonl 复盘，不产生新调用")
    rp.add_argument("--run-id", required=True)
    rp.set_defaults(func=cmd_replay)

    vz = sub.add_parser("viz", help="文本版执行可视化兜底")
    vz.add_argument("--run-id", required=True)
    vz.set_defaults(func=cmd_viz)

    sc = sub.add_parser("selfcheck", help="跑 architecture §15 的验收用例")
    sc.set_defaults(func=cmd_selfcheck)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(_dump({"error": "ConfigError", "detail": exc.errors}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
