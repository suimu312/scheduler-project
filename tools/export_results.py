"""把一条 mock 全链路 happy flow 固化进 examples/results（W1 验收口径）。

克隆仓库后不用跑任何命令，`examples/results/<run_id>/artifacts/` 下就能数出
15 个阶段产物 JSON；events.jsonl / spans.jsonl / state.json / config.json 作为
运行元数据一并入库，供复盘与探针核对。

用法（仓库根目录）：
    python tools/export_results.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.scheduler.context import RunConfig, now_iso  # noqa: E402
from src.scheduler.statemachine import Runtime  # noqa: E402

SCHEDULER_DIR = REPO_ROOT / "src" / "scheduler"
SPEC_PATH = REPO_ROOT / "examples" / "design_spec_wechat.json"
RUN_ID = "DEMO-WX"
STAGE_ROOT = REPO_ROOT / "runs" / "_export"
EXPORT_ROOT = REPO_ROOT / "examples" / "results"


def main() -> int:
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    cfg = RunConfig(
        run_id=RUN_ID,
        idea_text=f"[design_spec] {spec.get('product_name', '')} {spec.get('goal', '')}".strip(),
        author="demo",
        flow_ref=str(SCHEDULER_DIR / "flow.yaml"),
        gate_ref=str(SCHEDULER_DIR / "gate_rule.yaml"),
        agent_ref=str(REPO_ROOT / "src" / "agents" / "agent_config.yaml"),
        run_root=str(STAGE_ROOT),
        provider="mock",
    )
    cfg.design_spec = spec

    rt = Runtime.boot(cfg)
    state = rt.load_or_init()
    result = rt.run(state, human_script=[{"action": "sign"}])
    if result["status"] != "frozen":
        raise SystemExit(f"happy flow 未冻结：{result['status']} / {result.get('termination_reason')}")

    dst = EXPORT_ROOT / RUN_ID
    if dst.exists():
        shutil.rmtree(dst)
    (dst / "artifacts").mkdir(parents=True)

    src_run = STAGE_ROOT / RUN_ID
    for f in (src_run / "artifacts").glob("*.json"):
        shutil.copy2(f, dst / "artifacts" / f.name)
    for name in ("events.jsonl", "state.json", "config.json", "spans.jsonl"):
        p = src_run / name
        if p.exists():
            shutil.copy2(p, dst / name)

    artifacts = sorted(p.stem for p in (dst / "artifacts").glob("*.json"))

    # 快照不保留本机绝对路径：run_root 用通用口径，产物索引改成快照内相对路径
    cfg_path = dst / "config.json"
    cfg_obj = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg_obj["run_root"] = "runs"
    for ref_key, rel in (
        ("flow_ref", "src/scheduler/flow.yaml"),
        ("gate_ref", "src/scheduler/gate_rule.yaml"),
        ("agent_ref", "src/agents/agent_config.yaml"),
    ):
        cfg_obj[ref_key] = rel
    cfg_path.write_text(json.dumps(cfg_obj, ensure_ascii=False, indent=2), encoding="utf-8")

    state_path = dst / "state.json"
    state_obj = json.loads(state_path.read_text(encoding="utf-8"))
    state_obj["artifacts_index"] = {name: f"artifacts/{name}.json" for name in artifacts}
    state_path.write_text(json.dumps(state_obj, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = {
        "run_id": RUN_ID,
        "exported_at": now_iso(),
        "source": "mock happy flow（离线桩，未调真实模型）",
        "status": result["status"],
        "baseline_ref": result.get("baseline_ref"),
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
    (dst / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
