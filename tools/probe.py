"""编排侧验收探针（A 分工交付物）。

用法（仓库根目录）：
    python tools/probe.py <run_id> [--run-root runs]

逐条核对作业验收口径，输出 JSON 检查清单；全部通过退出码 0，否则 1：
  W6  越过 P2；events.jsonl 有成功的 model.call，且对应阶段产物落盘存在；
  O1  spans.jsonl 存在；trace_id 与 run_id 一致；stage/gate/model 三类 span 齐全；
  O2  command.issued 与 command.applied 按 command_id 成对；retry_task 的 fork
      目录存在且 state.json 里 parent_run_id 指向父运行。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.scheduler.context import STAGE_OUTPUTS, STAGES  # noqa: E402
from src.scheduler.store import RunStore  # noqa: E402


def _check(name: str, ok: bool, detail: object) -> dict[str, object]:
    return {"check": name, "pass": bool(ok), "detail": detail}


def probe(run_id: str, run_root: Path) -> dict[str, object]:
    store = RunStore(run_root)
    checks: list[dict[str, object]] = []

    state_path = store.state_path(run_id)
    if not state_path.exists():
        checks.append(_check("run_exists", False, f"找不到 {state_path}"))
        return {"run_id": run_id, "pass": False, "checks": checks}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    checks.append(_check("run_exists", True, {"phase": state.get("phase"), "status": state.get("status")}))

    # ---- W6：越过 P2 ----
    phase = str(state.get("phase") or "")
    phase_idx = STAGES.index(phase) if phase in STAGES else len(STAGES)
    artifacts_dir = store.artifacts_dir(run_id)
    artifact_names = {p.stem for p in artifacts_dir.glob("*.json")} if artifacts_dir.exists() else set()
    later_outputs = {name for stage in STAGES[2:] for name in STAGE_OUTPUTS[stage]}
    crossed_p2 = phase_idx >= 2 or bool(artifact_names & later_outputs)
    checks.append(_check("crossed_p2", crossed_p2, {"phase": phase, "artifacts": sorted(artifact_names)}))

    events = store.read_events(run_id)
    model_calls = [e for e in events if e.get("event_type") == "model.call"]
    ok_calls = [e for e in model_calls if (e.get("detail") or {}).get("ok")]
    checks.append(
        _check(
            "model_call_recorded",
            bool(ok_calls),
            {"total": len(model_calls), "ok": len(ok_calls), "sample": ok_calls[:2]},
        )
    )

    # 成功调用对应阶段至少有一个产物落盘
    artifact_ok = True
    artifact_detail: dict[str, object] = {}
    for call in ok_calls:
        stage = str(call.get("stage") or "")
        outputs = STAGE_OUTPUTS.get(stage, [])
        if outputs and not (set(outputs) & artifact_names):
            artifact_ok = False
            artifact_detail[stage] = {"outputs": outputs, "present": sorted(artifact_names & set(outputs))}
    checks.append(_check("model_call_artifact_exists", artifact_ok, artifact_detail or "model.call 阶段产物均已落盘"))

    # ---- O1：spans.jsonl ----
    spans = store.read_spans(run_id)
    checks.append(_check("spans_exist", bool(spans), {"spans": len(spans)}))
    trace_ok = all(str(sp.get("trace_id")) == run_id for sp in spans) if spans else False
    checks.append(_check("span_trace_id", trace_ok, {"trace_id": run_id}))
    kinds = {str(sp.get("kind")) for sp in spans}
    need_kinds = {"stage", "gate", "model"}
    checks.append(
        _check("span_kinds_present", need_kinds <= kinds, {"kinds": sorted(kinds), "need": sorted(need_kinds)})
    )

    # ---- O2：命令成对 + fork 血缘 ----
    issued = {str(e.get("detail", {}).get("command_id")): e for e in events if e.get("event_type") == "command.issued"}
    applied = {str(e.get("detail", {}).get("command_id")): e for e in events if e.get("event_type") == "command.applied"}
    # 没有命令时该项为空对空，视为通过；有命令则必须成对
    paired = set(issued) == set(applied)
    checks.append(_check("command_paired", paired, {"issued": sorted(issued), "applied": sorted(applied)}))

    fork_ok = True
    fork_detail: dict[str, object] = {}
    for cmd_id, e in issued.items():
        action = str((e.get("detail") or {}).get("target") or e.get("payload_ref") or "")
        if action != "retry_task":
            continue
        applied_e = applied.get(cmd_id) or {}
        fork_id = str((applied_e.get("detail") or {}).get("effect", {}).get("fork_run_id") or "")
        if not fork_id:
            fork_ok = False
            fork_detail[cmd_id] = "applied 事件缺 fork_run_id"
            continue
        fork_state_path = store.state_path(fork_id)
        if not fork_state_path.exists():
            fork_ok = False
            fork_detail[fork_id] = "fork 目录不存在"
            continue
        fork_state = json.loads(fork_state_path.read_text(encoding="utf-8"))
        lineage = fork_state.get("parent_run_id") == run_id
        fork_ok = fork_ok and lineage
        fork_detail[fork_id] = {"parent_run_id": fork_state.get("parent_run_id"), "lineage_ok": lineage}
    if issued:
        checks.append(_check("fork_lineage", fork_ok, fork_detail))

    return {"run_id": run_id, "pass": all(c["pass"] for c in checks), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="编排侧验收探针")
    parser.add_argument("run_id")
    parser.add_argument("--run-root", default=str(REPO_ROOT / "runs"))
    args = parser.parse_args(argv)
    result = probe(args.run_id, Path(args.run_root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
