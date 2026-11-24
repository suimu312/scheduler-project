"""跨 run 对比页生成器（F）。

从 examples/results/<run_id>/ 的真实产物读取指标，输出一个自包含、
断网可看的静态 HTML。页面里的每个数字都来自产物文件，不手写、不估算。

用法：
    python -m src.baseline_mgr.compare --run-root examples/results \
        --out examples/results/compare.html
"""

from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
from typing import Any

METRICS: tuple[tuple[str, str, str | None], ...] = (
    ("运行状态", "status", None),
    ("阶段", "phase", None),
    ("模型来源", "provider", None),
    ("产物数", "artifact_count", "high"),
    ("事件数", "event_count", "high"),
    ("model.call 事件", "model_calls", "high"),
    ("规则判定事件", "rule_decisions", "high"),
    ("任务卡数", "task_count", "high"),
    ("波次数", "wave_count", "high"),
    ("总工期（天）", "total_days", "low"),
    ("关键路径长度", "critical_path_len", None),
    ("覆盖率", "coverage_rate", "high"),
    ("覆盖空缺", "gap_count", "low"),
    ("冲突数", "conflict_count", None),
    ("基线版本", "baseline_version", None),
    ("基线冻结", "baseline_frozen", None),
    ("签字人", "signed_by", None),
    ("签字时间", "signed_at", None),
)

EVIDENCE_ARTIFACTS: tuple[str, ...] = (
    "design_spec",
    "discussion_minutes",
    "conflict_register",
    "solution_comparison",
    "review_sheet",
    "dev_task_list",
    "coverage_matrix",
    "schedule",
    "confirmation_sheet",
    "opinion_handling",
    "baseline",
)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _data(artifacts: dict[str, Any], name: str) -> dict[str, Any]:
    payload = artifacts.get(name)
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _count(events: list[dict[str, Any]], event_type: str) -> int:
    return sum(
        1
        for event in events
        if str(event.get("event_type") or event.get("type") or "") == event_type
    )


def collect_run(run_dir: Path) -> dict[str, Any]:
    """读取单个 run 的指标；缺字段一律记 0/空，不臆造。"""
    state = _load_json(run_dir / "state.json")
    config = _load_json(run_dir / "config.json")
    artifacts: dict[str, Any] = {}
    for path in sorted((run_dir / "artifacts").glob("*.json")):
        artifacts[path.stem] = _load_json(path)

    events_path = run_dir / "events.jsonl"
    events: list[dict[str, Any]] = []
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict):
                    events.append(item)

    schedule = _data(artifacts, "schedule")
    baseline = _data(artifacts, "baseline")
    task_list = _data(artifacts, "dev_task_list")
    coverage = _data(artifacts, "coverage_matrix") or _data(artifacts, "task_coverage")
    conflict = _data(artifacts, "conflict_register")
    critical = schedule.get("critical_path") or baseline.get("critical_path") or []
    if isinstance(critical, dict):
        critical = critical.get("task_ids") or []
    conflicts = conflict.get("conflicts") or conflict.get("conflict_candidates") or []

    return {
        "run_id": run_dir.name,
        "status": state.get("status") or "—",
        "phase": state.get("phase") or "—",
        "provider": f"{config.get('provider', '—')} / {config.get('llm_provider', '—')}",
        "artifact_count": len(artifacts),
        "event_count": len(events),
        "model_calls": _count(events, "model.call"),
        "rule_decisions": _count(events, "gate.evaluated"),
        "task_count": len(task_list.get("tasks") or []),
        "wave_count": len(schedule.get("waves") or []),
        "total_days": schedule.get("total_days") or baseline.get("total_days") or 0,
        "critical_path_len": len(critical) if isinstance(critical, list) else 0,
        "coverage_rate": coverage.get("coverage_rate", 0),
        "gap_count": coverage.get("gap_count", 0),
        "conflict_count": len(conflicts) if isinstance(conflicts, list) else 0,
        "baseline_version": baseline.get("version") or "—",
        "baseline_frozen": "是" if baseline.get("frozen") else "否",
        "signed_by": baseline.get("signed_by") or "—",
        "signed_at": baseline.get("signed_at") or "—",
        "present_artifacts": [name for name in EVIDENCE_ARTIFACTS if name in artifacts],
        "run_dir": run_dir.name,
    }


def discover_runs(run_root: Path, run_ids: list[str] | None = None) -> list[dict[str, Any]]:
    if run_ids:
        dirs = [run_root / run_id for run_id in run_ids]
    else:
        dirs = sorted((p for p in run_root.iterdir() if p.is_dir()), key=lambda p: p.name)
    runs = [collect_run(d) for d in dirs if (d / "state.json").exists()]
    if not runs:
        raise ValueError(f"在 {run_root} 下没有找到含 state.json 的运行目录")
    return runs


def _display(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return escape(str(value))


def _best(values: list[Any], direction: str | None) -> Any | None:
    if not direction:
        return None
    numbers = [v for v in values if isinstance(v, (int, float))]
    if not numbers:
        return None
    return max(numbers) if direction == "high" else min(numbers)


def render_html(runs: list[dict[str, Any]], title: str, generated_at: str) -> str:
    parts: list[str] = []
    parts.append(
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{escape(title)}</title>\n<style>\n"
        ":root{--bg:#f4f6fa;--card:#fff;--border:#dfe4ed;--ink:#1d2433;--sub:#5b6478;"
        "--accent:#315efb;--good:#e7f5ea;--bad:#fff0eb;--font:'PingFang SC','Microsoft YaHei',sans-serif}"
        "*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--font);font-size:14px}"
        "header{background:linear-gradient(120deg,#1f3a8a,#315efb);color:#fff;padding:24px 32px}"
        "header h1{margin:0;font-size:22px}header p{margin:8px 0 0;opacity:.85;font-size:12px}"
        "main{max-width:1180px;margin:24px auto;padding:0 20px 48px}"
        ".card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px;margin-bottom:18px;"
        "box-shadow:0 2px 10px rgba(30,45,80,.05)}h2{font-size:16px;margin:0 0 14px}"
        "table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:9px 12px;border-bottom:1px solid var(--border);text-align:left}"
        "th{background:#f7f9fd;color:var(--sub);font-weight:600;white-space:nowrap}td.metric{font-weight:600;color:var(--sub);width:180px}"
        "td.best{background:var(--good);font-weight:700}td.warn{background:var(--bad);font-weight:700}"
        ".chips{display:flex;flex-wrap:wrap;gap:8px}.chip{padding:6px 10px;border:1px solid var(--border);border-radius:999px;"
        "background:#f8faff;font-size:12px}.chip a{color:var(--accent);text-decoration:none}"
        "footer{color:var(--sub);font-size:12px;line-height:1.8}"
        "code{background:#f1f3f8;padding:2px 6px;border-radius:4px;font-size:12px}"
        "</style>\n</head>\n<body>\n"
        f"<header><h1>{escape(title)}</h1><p>数据来源：examples/results/&lt;run_id&gt;/artifacts/ · "
        f"生成时间 {escape(generated_at)} · 无外部资源，断网可看</p></header>\n<main>\n"
    )

    parts.append('<section class="card"><h2>一、总览</h2><div class="chips">')
    for run in runs:
        parts.append(
            f'<span class="chip"><b>{escape(run["run_id"])}</b> · {escape(str(run["status"]))} · '
            f'{escape(str(run["artifact_count"]))} 份产物 · {escape(str(run["event_count"]))} 条事件</span>'
        )
    parts.append("</div></section>\n")

    parts.append('<section class="card"><h2>二、指标对照（数字均直接读自产物）</h2><table><thead><tr><th>指标</th>')
    for run in runs:
        parts.append(f'<th>{escape(run["run_id"])}</th>')
    parts.append("</tr></thead><tbody>\n")
    for label, key, direction in METRICS:
        values = [run.get(key, "—") for run in runs]
        best = _best(values, direction)
        parts.append(f'<tr><td class="metric">{escape(label)}</td>')
        for value in values:
            css = ""
            if best is not None and value == best:
                css = ' class="best"'
            elif key == "model_calls" and isinstance(value, int) and value == 0:
                css = ' class="warn"'
            parts.append(f"<td{css}>{_display(value)}</td>")
        parts.append("</tr>\n")
    parts.append("</tbody></table></section>\n")

    parts.append('<section class="card"><h2>三、产物追溯（点击直达 JSON）</h2><table><thead><tr><th>产物</th>')
    for run in runs:
        parts.append(f'<th>{escape(run["run_id"])}</th>')
    parts.append("</tr></thead><tbody>\n")
    for name in EVIDENCE_ARTIFACTS:
        parts.append(f'<tr><td class="metric"><code>{escape(name)}.json</code></td>')
        for run in runs:
            if name in run["present_artifacts"]:
                href = f'{escape(run["run_dir"])}/artifacts/{escape(name)}.json'
                parts.append(f'<td><a href="{href}">查看产物</a></td>')
            else:
                parts.append("<td>—</td>")
        parts.append("</tr>\n")
    parts.append("</tbody></table></section>\n")

    parts.append(
        '<section class="card"><footer>'
        "生成逻辑：<code>src/baseline_mgr/compare.py</code>；每组运行的状态取自 "
        "<code>state.json</code>，指标取自 <code>artifacts/*.json</code>，"
        "事件计数取自 <code>events.jsonl</code>。model.call 为 0 表示当前运行没有真实模型调用事件，"
        "对应作业 2 的 W6 尚未达标；本页不将其包装为已完成。<br>"
        "多运行演示页（七视图切换）由 E 的 <code>python -m req_builder.viz --run-root examples/results --all-runs</code> 生成，"
        "本页与其互补：本页比数字与产物，那页看流程与回放。"
        "</footer></section>\n</main>\n</body>\n</html>\n"
    )
    return "".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成跨 run 对比页（F）")
    parser.add_argument("--run-root", default="examples/results", help="运行根目录")
    parser.add_argument("--out", default="examples/results/compare.html", help="输出 HTML 路径")
    parser.add_argument("--run-ids", default="", help="逗号分隔的 run_id，默认扫描全部")
    parser.add_argument("--title", default="第 10 组 · 跨运行对比页", help="页面标题")
    args = parser.parse_args()

    from datetime import datetime
    from datetime import timedelta
    from datetime import timezone

    now = datetime.now(timezone(timedelta(hours=8))).replace(microsecond=0).isoformat()
    run_ids = [item.strip() for item in args.run_ids.split(",") if item.strip()]
    runs = discover_runs(Path(args.run_root), run_ids or None)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_html(runs, args.title, now), encoding="utf-8")
    print(f"[compare] 共 {len(runs)} 组运行：{', '.join(r['run_id'] for r in runs)}")
    print(f"[compare] 已输出：{out_path}")


if __name__ == "__main__":
    main()
