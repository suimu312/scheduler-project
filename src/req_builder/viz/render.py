"""渲染器：render_views(events, until_seq, artifacts) -> 自包含 index.html。

一条原则（docx §6.1）：界面是事件的投影。入参只有事件数组与截止序号，
输出界面，渲染函数本身是纯函数；实时与回放共用同一套代码。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from ..contracts import RunEvent
from .events import ReplayWindow
from .svg import svg_open
from .theme import (
    ACCENT,
    BG,
    BORDER,
    CARD,
    CRITICAL,
    FONT_STACK,
    GRID,
    INK,
    INK_FAINT,
    INK_SUB,
)
from .views import VIEWS

PAGE_W = 1180


def _artifact_bundle(artifacts: dict[str, Any] | None) -> dict[str, Any]:
    return dict(artifacts or {})


def _header(window: ReplayWindow, run_id: str, until_seq: int) -> str:
    stages = sorted({str(e.stage) for e in window.events if e.stage}, key=lambda s: s)
    last_stage = str(window.last("stage.enter").get("stage")) if window.last("stage.enter") else "—"
    return f"""
<header class="page-head">
  <div class="head-left">
    <div class="title">多智能体任务分解与编排系统 · 执行可视化</div>
    <div class="sub">run_id <b>{run_id}</b> · 截止序号 <b>EVT-{until_seq:05d}</b> · 当前阶段 <b>{last_stage}</b> · 事件 {len(window.events)} 条</div>
  </div>
  <div class="head-right">
    <div class="legend"><span class="dot must"></span>Must <span class="dot critical"></span>关键路径
      <span class="swatch"></span>空缺 <span class="swatch gray"></span>发言趋同</div>
    <div class="hint">页面为静态 SVG 生成，无外部依赖，断网可看</div>
  </div>
</header>"""


def _section(view: dict[str, Any], body: str, height: float, width: float = PAGE_W, extra_class: str = "") -> str:
    return f"""
<section class="card {extra_class}" id="{view['id'].lower()}">
  <div class="card-head">
    <span class="chip">{view['id']}</span>
    <span class="card-title">{view['title']}</span>
    <span class="card-q">{view['question']}</span>
  </div>
  <div class="card-body">
    {svg_open(width, height)}{body}</svg>
  </div>
</section>"""


def render_views(
    events: Iterable[RunEvent],
    until_seq: int | None = None,
    artifacts: dict[str, Any] | None = None,
) -> str:
    """纯函数：同样的入参永远得到同一份 HTML（时间戳除外，由调用方注入）。"""
    event_list = list(events)
    window = ReplayWindow.of(event_list, until_seq)
    bundle = _artifact_bundle(artifacts)
    run_id = next((e.run_id for e in event_list if e.run_id), "R0001")
    total = window.until_seq
    max_total = max((e.seq for e in event_list), default=0)

    bodies: dict[str, tuple[str, float]] = {}
    # 双栏里的两个视图按半宽绘制，避免 viewBox 被整体缩放成看不清的小字
    half_w = (PAGE_W - 14) / 2
    for view in VIEWS:
        width = half_w if view["id"] in ("V2", "V3") else PAGE_W
        bodies[view["id"]] = view["module"].render(window, bundle, width)
        bodies[view["id"]] = (bodies[view["id"]][0], bodies[view["id"]][1], width)

    grid = f"""
<section class="grid-2">
  {_section(VIEWS[1], *bodies["V2"])}
  {_section(VIEWS[2], *bodies["V3"])}
</section>"""

    anomalies = [event.seq for event in window.anomalies()]

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{run_id} · 执行可视化</title>
<style>
  :root {{
    --bg: {BG}; --card: {CARD}; --border: {BORDER}; --grid: {GRID};
    --ink: {INK}; --sub: {INK_SUB}; --faint: {INK_FAINT};
    --accent: {ACCENT}; --critical: {CRITICAL};
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 22px 18px 96px; background: var(--bg); color: var(--ink);
    font-family: {FONT_STACK}; font-size: 13px;
  }}
  .wrap {{ max-width: {PAGE_W}px; margin: 0 auto; }}
  .page-head {{ display: flex; justify-content: space-between; align-items: flex-end; gap: 16px;
    padding: 4px 2px 14px; border-bottom: 1px solid var(--border); margin-bottom: 14px; }}
  .title {{ font-size: 19px; font-weight: 700; letter-spacing: .3px; }}
  .sub {{ margin-top: 6px; color: var(--sub); font-size: 11.5px; }}
  .sub b {{ color: var(--ink); }}
  .head-right {{ text-align: right; color: var(--faint); font-size: 10.5px; }}
  .legend {{ display: flex; gap: 10px; align-items: center; justify-content: flex-end; margin-bottom: 6px; }}
  .dot {{ width: 9px; height: 9px; border-radius: 50%; display: inline-block; }}
  .dot.must {{ background: #c2255c; }}
  .dot.critical {{ background: var(--critical); }}
  .swatch {{ width: 12px; height: 10px; display: inline-block; border: 1px dashed #9aa3b2;
    background: repeating-linear-gradient(45deg, #dfe4ec 0 3px, #ffffff 3px 6px); }}
  .swatch.gray {{ background: rgba(154,163,178,.55); border: none; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 12px 14px 14px; margin-bottom: 12px; }}
  .card-head {{ display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }}
  .chip {{ background: #eef2f9; color: var(--accent); font-weight: 700; font-size: 10.5px;
    border-radius: 4px; padding: 2px 6px; }}
  .card-title {{ font-weight: 700; font-size: 13.5px; }}
  .card-q {{ color: var(--faint); font-size: 11px; }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
  .card-body svg {{ display: block; }}
  .replay {{ position: fixed; left: 0; right: 0; bottom: 0; background: rgba(255,255,255,.97);
    border-top: 1px solid var(--border); backdrop-filter: blur(6px); }}
  .replay-inner {{ max-width: {PAGE_W}px; margin: 0 auto; display: flex; align-items: center; gap: 10px;
    padding: 8px 18px 10px; flex-wrap: wrap; }}
  .replay-label {{ font-size: 11.5px; color: var(--sub); font-variant-numeric: tabular-nums; }}
  input[type=range] {{ flex: 1 1 320px; accent-color: var(--accent); }}
  button {{ border: 1px solid var(--border); background: #fff; color: var(--ink); border-radius: 6px;
    padding: 4px 10px; font-size: 11.5px; cursor: pointer; font-family: inherit; }}
  button:hover {{ border-color: var(--accent); color: var(--accent); }}
  .footer {{ margin-top: 18px; color: var(--faint); font-size: 10.5px; text-align: center; }}
  @media (max-width: 900px) {{ .grid-2 {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="wrap">
{_header(window, run_id, total)}
{_section(VIEWS[3], *bodies["V4"], extra_class="topbar")}
{_section(VIEWS[0], *bodies["V1"])}
{grid}
{_section(VIEWS[4], *bodies["V5"])}
{_section(VIEWS[5], *bodies["V6"])}
{_section(VIEWS[6], *bodies["V7"])}
  <div class="footer">
    由 req_builder 生成 · 输入 events.jsonl + schedule.json + task_coverage.json · 界面是事件的投影，不持有业务状态
  </div>
</div>
<div class="replay">
  <div class="replay-inner">
    <button id="btn-first" title="回到开头">◀◀</button>
    <button id="btn-prev" title="上一异常">◀</button>
    <input id="replay-range" type="range" min="0" max="{max_total}" value="{total}" step="1">
    <button id="btn-next" title="下一异常">▶</button>
    <button id="btn-last" title="跳到最新">▶▶</button>
    <span class="replay-label" id="replay-label">EVT-{total:05d} / EVT-{max_total:05d}</span>
  </div>
</div>
<script>
(function () {{
  var total = {max_total};
  var anomalies = {json.dumps(anomalies)};
  var slider = document.getElementById('replay-range');
  var label = document.getElementById('replay-label');
  function pad(n) {{ return ('00000' + n).slice(-5); }}
  function apply(v) {{
    v = Math.max(0, Math.min(total, v));
    slider.value = v;
    label.textContent = 'EVT-' + pad(v) + ' / EVT-' + pad(total);
    var nodes = document.querySelectorAll('[data-seq]');
    for (var i = 0; i < nodes.length; i++) {{
      var s = parseInt(nodes[i].getAttribute('data-seq'), 10);
      nodes[i].style.display = (s <= v) ? '' : 'none';
    }}
  }}
  function next() {{
    var v = parseInt(slider.value, 10);
    for (var i = 0; i < anomalies.length; i++) {{
      if (anomalies[i] > v) {{ apply(anomalies[i]); return; }}
    }}
    apply(total);
  }}
  function prev() {{
    var v = parseInt(slider.value, 10);
    for (var i = anomalies.length - 1; i >= 0; i--) {{
      if (anomalies[i] < v) {{ apply(anomalies[i]); return; }}
    }}
    apply(0);
  }}
  slider.addEventListener('input', function () {{ apply(parseInt(slider.value, 10)); }});
  document.getElementById('btn-first').addEventListener('click', function () {{ apply(0); }});
  document.getElementById('btn-last').addEventListener('click', function () {{ apply(total); }});
  document.getElementById('btn-next').addEventListener('click', next);
  document.getElementById('btn-prev').addEventListener('click', prev);
  apply({total});
}})();
</script>
</body>
</html>
"""
    return html


def render_to_file(
    events: Iterable[RunEvent],
    out_path: str | Path,
    until_seq: int | None = None,
    artifacts: dict[str, Any] | None = None,
) -> Path:
    html = render_views(events, until_seq=until_seq, artifacts=artifacts)
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    return target
