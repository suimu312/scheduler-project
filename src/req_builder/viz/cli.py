"""viz 子命令：跑完一次运行即可导出可视化页面。

用法（与 A 的 `cli viz --run-id` 对齐，也支持直接给目录）：

    python -m req_builder.viz --run-dir runs/R0001
    python -m req_builder.viz --run-dir runs/R0001 --until 42
    python -m req_builder.viz --events runs/R0001/events.jsonl --artifacts runs/R0001/artifacts

输出默认落在 <run-dir>/viz/index.html；页面自包含，断网可看，可截图进答辩材料。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .events import load_events
from .render import render_to_file

ARTIFACT_NAMES = (
    "schedule",
    "coverage_matrix",   # 引擎实际产出的名字（E 原写 task_coverage，一直在读空）
    "dev_task_list",
    "solution_comparison",  # 功能点标题
    "idea_card",          # 设计规格原文（P1 的产物，design_spec 在它里面）
    "conflict_register",
    "review_sheet",
)


def load_artifacts(artifact_dir: str | Path) -> dict[str, Any]:
    """一次把渲染要用的产物读进内存：{artifact_name: 信封对象}。

    兼容旧命名：task_coverage / design_spec 这两个名字引擎从来没产出过，
    读到对应新名字时顺手补一份别名，免得老视图取不到。
    """
    directory = Path(artifact_dir)
    bundle: dict[str, Any] = {}
    if not directory.exists():
        return bundle
    for name in ARTIFACT_NAMES:
        path = directory / f"{name}.json"
        if path.exists():
            try:
                bundle[name] = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path} 不是合法 JSON：{exc}") from exc
    if "coverage_matrix" in bundle:
        bundle.setdefault("task_coverage", bundle["coverage_matrix"])
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="req_builder.viz", description="把事件流渲染成七个视图")
    parser.add_argument("--run-dir", help="运行目录，例如 runs/R0001")
    parser.add_argument("--run-id", help="与 --run-dir 二选一，等价于 runs/<run_id>")
    parser.add_argument("--events", help="直接指定 events.jsonl")
    parser.add_argument("--artifacts", help="产物目录，默认 <run-dir>/artifacts")
    parser.add_argument("--until", type=int, default=None, help="截止序号；缺省为最新（实时）")
    parser.add_argument("--out", default=None, help="输出 HTML 路径，默认 <run-dir>/viz/index.html")
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir) if args.run_dir else (Path("runs") / args.run_id if args.run_id else None)
    if not run_dir and not args.events:
        parser.error("需要 --run-dir / --run-id / --events 之一")

    events_path = Path(args.events) if args.events else run_dir / "events.jsonl"
    artifact_dir = Path(args.artifacts) if args.artifacts else (run_dir / "artifacts" if run_dir else Path("runs/artifacts"))
    out_path = Path(args.out) if args.out else (run_dir / "viz" / "index.html" if run_dir else Path("runs/viz/index.html"))

    events = load_events(events_path)
    artifacts = load_artifacts(artifact_dir)
    target = render_to_file(events, out_path, until_seq=args.until, artifacts=artifacts)

    total = max((e.seq for e in events), default=0)
    until = total if args.until is None else min(args.until, total)
    print(f"[viz] 事件 {len(events)} 条，截止序号 {until}/{total}，产物 {len(artifacts)} 份")
    print(f"[viz] 已输出：{target.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
