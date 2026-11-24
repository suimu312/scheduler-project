"""O2 执行观测台：健康条 / 单卡瀑布 / 告警列表 + 暂停 / 批注 / 单卡重试三动作。

页面仍然是自包含单文件（无外部 src/href），有两副面孔：
- 双击打开（file://）：纯回看。三个动作按钮给出等效 CLI 命令并可一键复制，
  保证「离线页面不假装能改后端」。
- `--serve` 起本地服务：按钮直接下发命令，服务端调 scheduler.commands 真正落
  command.issued + command.applied，retry_task 会 fork 出 <run_id>.fork.<n>，
  页面刷新后命令流水与父子对比同步更新。

三个视图的每个数字都带 events 证据号，双击钻到原始事件 JSON。
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import obs
from .theme import ACCENT, BG, BORDER, CARD, CRITICAL, FONT_STACK, GRID, INK, INK_FAINT, INK_SUB

PAGE_W = 1180

CSS = """
  * { box-sizing: border-box; }
  body { margin: 0; padding: 22px 18px 40px; background: @@BG@@; color: @@INK@@;
    font-family: @@FONT@@; font-size: 13px; }
  .wrap { max-width: @@W@@px; margin: 0 auto; }
  .page-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px;
    padding: 4px 2px 14px; border-bottom: 1px solid @@BORDER@@; margin-bottom: 14px; }
  .title { font-size: 19px; font-weight: 700; letter-spacing: .3px; }
  .sub { margin-top: 6px; color: @@SUB@@; font-size: 11.5px; }
  .sub b { color: @@INK@@; }
  .counters { margin-top: 8px; display: flex; gap: 14px; align-items: baseline; font-size: 11px; color: @@SUB@@; }
  .counters .sep { padding-left: 14px; border-left: 1px solid @@BORDER@@; }
  .counters b { font-size: 15px; color: @@ACCENT@@; font-variant-numeric: tabular-nums; margin-right: 3px; }
  .head-right { text-align: right; color: @@FAINT@@; font-size: 10.5px; line-height: 1.6; }
  .card { background: @@CARD@@; border: 1px solid @@BORDER@@; border-radius: 10px;
    padding: 12px 14px 14px; margin-bottom: 12px; }
  .card-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 10px; }
  .chip { background: #eef2f9; color: @@ACCENT@@; font-weight: 700; font-size: 10.5px;
    border-radius: 4px; padding: 2px 6px; }
  .card-title { font-weight: 700; font-size: 13.5px; }
  .card-q { color: @@FAINT@@; font-size: 11px; }
  .strip { display: flex; gap: 8px; flex-wrap: wrap; }
  .stage { flex: 1 1 120px; border: 1px solid @@BORDER@@; border-radius: 8px; padding: 7px 9px 8px;
    background: #fff; cursor: pointer; position: relative; }
  .stage:hover { border-color: @@ACCENT@@; }
  .stage .lamp { width: 8px; height: 8px; border-radius: 50%; position: absolute; right: 9px; top: 10px; }
  .lamp.ok { background: #2b8a3e; }
  .lamp.warn { background: #e8590c; }
  .lamp.bad { background: #c92a2a; }
  .sname { font-size: 11px; font-weight: 700; color: @@ACCENT@@; }
  .sval { font-size: 15px; font-weight: 700; font-variant-numeric: tabular-nums; margin: 2px 0 1px; }
  .sval small { font-size: 10px; font-weight: 400; color: @@FAINT@@; }
  .sgate { font-size: 10px; color: @@SUB@@; }
  .sgate .fail { color: #c92a2a; }
  .salert { font-size: 10px; color: @@FAINT@@; margin-top: 2px; }
  .sum { display: flex; gap: 18px; margin-top: 10px; font-size: 11px; color: @@SUB@@; align-items: baseline; }
  .sum b { font-size: 13px; color: @@INK@@; font-variant-numeric: tabular-nums; }
  .wf-head { display: flex; gap: 12px; align-items: center; margin-bottom: 10px; flex-wrap: wrap; }
  select, input[type=text] { border: 1px solid @@BORDER@@; border-radius: 6px; padding: 4px 8px;
    font-family: inherit; font-size: 11.5px; color: @@INK@@; background: #fff; }
  select:focus, input[type=text]:focus { outline: none; border-color: @@ACCENT@@; }
  .badge { font-size: 10px; border-radius: 4px; padding: 1px 6px; background: #eef2f9; color: @@ACCENT@@; }
  .badge.plan { background: #fff4e6; color: #d9480f; }
  .badge.measured { background: #e7f6ec; color: #2b8a3e; }
  .wf-total { margin-left: auto; font-size: 11.5px; color: @@SUB@@; }
  .wf-total b { font-size: 16px; color: @@ACCENT@@; font-variant-numeric: tabular-nums; }
  .wf-bar { display: flex; height: 34px; border-radius: 6px; overflow: hidden; border: 1px solid @@BORDER@@; }
  .wf-bar div { display: flex; align-items: center; justify-content: center; color: #fff;
    font-size: 10.5px; font-variant-numeric: tabular-nums; min-width: 2px; }
  .wf-bar div.wait { color: #343a40; }
  .wf-legend { display: flex; gap: 16px; margin-top: 8px; font-size: 10.5px; color: @@SUB@@; flex-wrap: wrap; }
  .wf-legend i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; margin-right: 4px; }
  .wf-meta { margin-top: 10px; display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
  .mcell { border: 1px solid @@BORDER@@; border-radius: 6px; padding: 6px 8px; }
  .mcell .k { font-size: 10px; color: @@FAINT@@; }
  .mcell .v { font-size: 12px; font-weight: 700; margin-top: 2px; }
  .wf-dep { margin-top: 8px; font-size: 10.5px; color: @@SUB@@; }
  table { width: 100%; border-collapse: collapse; font-size: 11.5px; }
  th { text-align: left; color: @@FAINT@@; font-weight: 500; font-size: 10.5px; padding: 0 8px 6px 0;
    border-bottom: 1px solid @@BORDER@@; }
  td { padding: 7px 8px 7px 0; border-bottom: 1px solid #f0f2f6; vertical-align: top; }
  .tag { display: inline-block; border-radius: 4px; padding: 0 6px; font-size: 10px; }
  .tag.high { background: #ffe3e3; color: #c92a2a; }
  .tag.mid { background: #fff4e6; color: #d9480f; }
  .tag.low { background: #eef2f9; color: #364fc7; }
  .tag.ok { background: #e7f6ec; color: #2b8a3e; }
  .adetail { color: @@SUB@@; font-size: 10.5px; margin-top: 2px; }
  .ev { color: @@ACCENT@@; cursor: pointer; font-variant-numeric: tabular-nums; font-size: 10.5px;
    border-bottom: 1px dotted @@ACCENT@@; margin-right: 6px; }
  .ev:hover { background: #eef2f9; }
  .dash { color: @@FAINT@@; }
  .acts { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
  .act { border: 1px solid @@BORDER@@; border-radius: 8px; padding: 9px 11px 11px; background: #fff; }
  .act h4 { margin: 0 0 8px; font-size: 12px; }
  .act .row { display: flex; gap: 6px; margin-bottom: 6px; }
  .act .row input, .act .row select { flex: 1; min-width: 0; }
  .act p { margin: 6px 0 0; font-size: 10px; color: @@FAINT@@; line-height: 1.5; }
  button { border: 1px solid @@BORDER@@; background: #fff; color: @@INK@@; border-radius: 6px;
    padding: 4px 10px; font-size: 11.5px; cursor: pointer; font-family: inherit; white-space: nowrap; }
  button:hover { border-color: @@ACCENT@@; color: @@ACCENT@@; }
  button.primary { background: @@ACCENT@@; border-color: @@ACCENT@@; color: #fff; }
  button.primary:hover { opacity: .9; color: #fff; }
  .log { margin-top: 12px; }
  .offline-hint { margin-top: 9px; padding: 8px 10px; background: #fff9db; border: 1px solid #ffe066;
    border-radius: 6px; font-size: 10.5px; color: #a07800; line-height: 1.6; }
  .cmp { margin-top: 12px; }
  .cmp th, .cmp td { text-align: left; }
  .cmp td.num { font-variant-numeric: tabular-nums; }
  .cmp .delta { color: @@FAINT@@; font-size: 10px; }
  pre.drill-body { margin: 0; max-height: 300px; overflow: auto; background: #f8f9fb;
    border-top: 1px solid @@BORDER@@; padding: 10px 12px; font-family: ui-monospace, Consolas, monospace;
    font-size: 10.5px; line-height: 1.55; }
  .drill { position: fixed; left: 0; right: 0; bottom: 0; background: rgba(255,255,255,.98);
    border-top: 2px solid @@ACCENT@@; box-shadow: 0 -6px 18px rgba(30,40,70,.08); display: none; z-index: 20; }
  .drill.on { display: block; }
  .drill-head { max-width: @@W@@px; margin: 0 auto; display: flex; align-items: center; gap: 10px;
    padding: 8px 18px 6px; font-size: 11.5px; }
  .drill-head b { color: @@ACCENT@@; }
  .drill-head .spacer { flex: 1; }
  .footer { margin-top: 16px; color: @@FAINT@@; font-size: 10.5px; text-align: center; line-height: 1.7; }
"""


def _fmt_ms(value: Any) -> str:
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "—"


def _ev_chips(ids: list[str] | None, limit: int = 6) -> str:
    ids = [i for i in (ids or []) if i]
    if not ids:
        return '<span class="dash">无证据事件</span>'
    chips = "".join(f'<span class="ev" data-ev="{i}">{i}</span>' for i in ids[:limit])
    more = f'<span class="dash">+{len(ids) - limit}</span>' if len(ids) > limit else ""
    return chips + more


def _header(data: dict[str, Any]) -> str:
    health = data["health"]
    state = data["state"]
    log = data["command_log"]
    paired = f"{len(log['commands'])}/{len(log['commands'])}" if log["commands"] else "0/0"
    return (
        '<header class="page-head"><div>'
        '<div class="title">执行观测台 · 健康条 / 单卡瀑布 / 告警列表</div>'
        f'<div class="sub">run_id <b>{data["run_id"]}</b> · 阶段 <b>{state["phase"] or "—"}</b>'
        f' · 状态 <b>{state["status"] or "—"}</b> · span <b>{state["spans"]}</b> 条'
        f' · 事件 <b>{len(data["raw"]["events"])}</b> 条'
        + (f' · 子运行 <b>{state["fork"]}</b>' if state.get("fork") else "")
        + "</div>"
        '<div class="counters">'
        f'<span><b>{health["score"]}</b>健康分</span>'
        f'<span class="sep"><b>{health["alert_total"]}</b>条告警</span>'
        f'<span class="sep"><b>{paired}</b>命令成对</span>'
        f'<span class="sep"><b>{health["budget_used"]}/{health["budget_limit"]}</b>预算</span>'
        "</div></div>"
        '<div class="head-right">双击阶段方块或事件号 → 钻到原始事件<br>'
        "观测数据来自 spans.jsonl + events.jsonl + state.json</div></header>"
    )


def _health_section(data: dict[str, Any]) -> str:
    health = data["health"]
    if not health["rows"]:
        return '<section class="card"><div class="card-head"><span class="chip">V8</span><span class="card-title">健康条</span></div>没有阶段 span，无法计算健康度</section>'

    cells = []
    for row in health["rows"]:
        lamp = row["status"] if row["status"] in ("ok", "warn", "bad") else "warn"
        gate = row["gate_action"] or "未判"
        gate_cls = "" if row["gate_pass"] else ' class="fail"'
        gate_note = "（推断）" if row["gate_inferred"] else ""
        cells.append(
            f'<div class="stage" data-stage="{row["stage"]}">'
            f'<span class="lamp {lamp}"></span>'
            f'<div class="sname">{row["stage"]}</div>'
            f'<div class="sval"><span class="num" data-ev="{",".join(row["evidence"])}">{_fmt_ms(row["duration_ms"])}</span>'
            '<small> ms</small></div>'
            f'<div class="sgate">闸门 <span{gate_cls}>{gate}</span>{gate_note}</div>'
            f'<div class="salert">{row["events"]} 事件 · {row["alerts"]} 告警</div>'
            "</div>"
        )
    return (
        '<section class="card" id="v8"><div class="card-head">'
        '<span class="chip">V8</span><span class="card-title">健康条</span>'
        '<span class="card-q">每个阶段一盏灯：耗时 / 闸门结论 / 告警数，双击钻到证据</span></div>'
        f'<div class="strip">{"".join(cells)}</div>'
        '<div class="sum">'
        f'<span>总耗时 <b>{_fmt_ms(health["total_ms"])}</b> ms（阶段 span 之和）</span>'
        f'<span>健康分 <b>{health["score"]}</b> = 100 − 异常阶段×12 − 告警×3</span>'
        f'<span>预算 <b>{health["budget_used"]}/{health["budget_limit"]}</b>'
        f'<span class="ev" data-ev="{health["budget_evidence"]}">（证据）</span></span>'
        "</div></section>"
    )


def _waterfall_section(data: dict[str, Any]) -> str:
    wf = data["waterfall"]
    if wf.get("empty"):
        return '<section class="card"><div class="card-head"><span class="chip">V9</span><span class="card-title">单卡瀑布</span></div>没有排期产物，无法分解单卡耗时</section>'
    cards = wf.get("cards") or []
    options = "".join(
        f'<option value="{c["task_id"]}"{" selected" if c["task_id"] == wf["task_id"] else ""}>'
        f'{c["task_id"]} {c["title"]}{"（重试中）" if c.get("retried") else ""}'
        f'{"（关键路径）" if c.get("critical") else ""}</option>'
        for c in cards
    )
    return (
        '<section class="card" id="v9"><div class="card-head">'
        '<span class="chip">V9</span><span class="card-title">单卡瀑布</span>'
        '<span class="card-q">单张任务卡的耗时分解：等多久 / 干多久 / 被顺延多少</span></div>'
        f'<div class="wf-head"><label>任务卡 </label><select id="wf-task">{options}</select>'
        '<span class="badge plan" id="wf-basis">排期口径</span>'
        f'<span class="wf-total">总计 <b id="wf-total">{wf["total_days"]:.1f}</b> 天</span></div>'
        '<div class="wf-bar" id="wf-bar"></div>'
        '<div class="wf-legend" id="wf-legend"></div>'
        '<div class="wf-meta" id="wf-meta"></div>'
        '<div class="wf-dep" id="wf-dep"></div>'
        f'<div class="wf-dep" style="color:#a07800">{wf.get("note") or ""}</div></section>'
    )


def _alerts_section(data: dict[str, Any]) -> str:
    alerts = data["alerts"]
    if not alerts:
        body = '<div style="color:#2b8a3e;font-size:12px">没有判据命中：闸门全放行、span 无报错、预算与覆盖均在阈值内。</div>'
    else:
        rows = []
        for a in alerts:
            rows.append(
                "<tr>"
                f'<td><span class="tag {a["level"]}">{a["level_label"]}</span></td>'
                f'<td><b>{a["title"]}</b><div class="adetail">{a["detail"]}</div></td>'
                f'<td>{a["stage"] or "—"}</td>'
                f'<td><span class="dash">{a["code"]}</span></td>'
                f'<td>{_ev_chips(a["evidence"])}</td>'
                "</tr>"
            )
        body = (
            "<table><thead><tr><th>级别</th><th>告警</th><th>阶段</th><th>判据码</th><th>证据事件</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
    return (
        '<section class="card" id="v10"><div class="card-head">'
        '<span class="chip">V10</span><span class="card-title">告警列表</span>'
        f'<span class="card-q">判据命中即告警，共 {len(alerts)} 条；每条都能钻到具体事件</span></div>'
        f"{body}</section>"
    )


def _commands_section(data: dict[str, Any]) -> str:
    log = data["command_log"]
    task_options = "".join(
        f'<option value="{c["task_id"]}">{c["task_id"]}</option>'
        for c in (data["waterfall"].get("cards") or [])
    )
    rows = []
    for cmd in log["commands"]:
        fork = f'<span class="ev" data-fork="{cmd["effective_fork"]}">{cmd["effective_fork"]}</span>' if cmd.get("effective_fork") else '<span class="dash">—</span>'
        rows.append(
            "<tr>"
            f'<td><b>{cmd["command_id"]}</b></td>'
            f'<td>{cmd.get("action")}</td>'
            f'<td>{cmd.get("target") or "—"}</td>'
            f'<td>{(cmd.get("note") or "—")[:26]}</td>'
            f'<td>{_ev_chips([cmd.get("issued_event", "")])}</td>'
            f'<td>{_ev_chips([cmd.get("applied_event", "")])}</td>'
            f"<td>{fork}</td>"
            "</tr>"
        )
    log_body = (
        "<table><thead><tr><th>命令</th><th>动作</th><th>目标</th><th>备注</th><th>command.issued</th>"
        "<th>command.applied</th><th>fork</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        if rows
        else '<div class="dash">还没有命令：点上面三个按钮，或在仓库根跑 <code>python -m scheduler.cli command --run-id ...</code></div>'
    )

    compare = data.get("compare")
    if compare:
        cmp_rows = []
        for row in compare["rows"]:
            p, f = row["parent"], row["fork"]
            delta = ""
            if isinstance(p, (int, float)) and isinstance(f, (int, float)):
                d = f - p
                delta = f'<span class="delta"> {"+" if d else ""}{d:g}</span>'
            cmp_rows.append(
                f'<tr><td>{row["label"]}</td><td class="num">{p}{delta}</td><td class="num">{f}</td></tr>'
            )
        cmp_body = (
            '<div class="cmp"><div class="card-head" style="margin-bottom:6px">'
            f'<span class="chip">对比</span><span class="card-title">父运行 vs 子运行</span>'
            f'<span class="card-q">{compare["retry_task"] or "—"} 重试后 fork 出 {compare["fork"]["run_id"]}'
            f'（{compare["reason"]}）</span></div>'
            "<table><thead><tr><th>指标</th>"
            f'<th>{compare["parent"]["run_id"]}（父）</th><th>{compare["fork"]["run_id"]}（子）</th>'
            f"</tr></thead><tbody>{''.join(cmp_rows)}</tbody></table>"
            '<div class="wf-dep">父子各自独立：父运行状态不回写，子运行从 P3 重跑被重试的卡，预算沿用父运行已花部分。</div></div>'
        )
    else:
        cmp_body = '<div class="wf-dep">还没有 fork：对某张卡做一次「单卡重试」即生成 &lt;run_id&gt;.fork.1。</div>'

    return (
        '<section class="card" id="k1"><div class="card-head">'
        '<span class="chip">K1</span><span class="card-title">交互控件 · 三个动作</span>'
        '<span class="card-q">暂停 / 批注 / 单卡重试，一律落 command.issued + command.applied</span></div>'
        '<div class="acts">'
        '<div class="act"><h4>① 暂停</h4>'
        '<div class="row"><input type="text" id="note-pause" placeholder="暂停理由（可空）"></div>'
        '<div class="row"><button class="primary" data-act="pause">下发暂停</button></div>'
        '<p>落 HUMAN_GATE，状态机等人工继续；不改产物。</p></div>'
        '<div class="act"><h4>② 批注</h4>'
        '<div class="row"><input type="text" id="target-comment" placeholder="批注对象，如 review_sheet"></div>'
        '<div class="row"><input type="text" id="note-comment" placeholder="批注内容"></div>'
        '<div class="row"><button class="primary" data-act="comment">写进人工决策</button></div>'
        '<p>内容同时进 constraints 与 human_decisions，P5/P6 会读到。</p></div>'
        '<div class="act"><h4>③ 单卡重试</h4>'
        f'<div class="row"><select id="target-retry">{task_options}</select></div>'
        '<div class="row"><input type="text" id="note-retry" placeholder="重试理由（可空）"></div>'
        '<div class="row"><button class="primary" data-act="retry_task">重试该卡</button></div>'
        '<p>不改原运行：复制出 &lt;run_id&gt;.fork.n，从 P3 重跑该卡，父子并排对比。</p></div>'
        "</div>"
        '<div class="offline-hint" id="offline-hint">当前是 file:// 静态页：按钮不会改后端，点「下发」会复制等效 CLI 命令。'
        "需要真下发就改用 <code>python -m req_builder.viz.o2 --run-dir &lt;运行目录&gt; --serve</code>。</div>"
        f'<div class="log"><div class="card-head" style="margin-bottom:6px"><span class="card-title">命令流水</span>'
        f'<span class="card-q">成对率 {"全部成对" if data["command_log"]["paired"] else "存在未成对命令"}</span></div>{log_body}</div>'
        f"{cmp_body}</section>"
    )


JS = """
(function () {
  var RUN_DIR = @@RUN_DIR@@;
  var RAW = @@RAW@@;
  var WATERFALLS = @@WF@@;
  var PICKED = @@PICKED@@;

  function evList(ids) {
    if (!ids) return [];
    return String(ids).split(',').filter(function (x) { return x && RAW[x]; });
  }
  function drill(ids, title) {
    var list = evList(ids);
    var panel = document.getElementById('drill');
    var body = document.getElementById('drill-body');
    document.getElementById('drill-title').innerHTML = title || ('证据事件 ' + list.join(' / '));
    if (!list.length) {
      body.textContent = '这条指标没有对应的单条事件（由 span 或产物汇总得出）。';
    } else {
      body.textContent = list.map(function (id) { return JSON.stringify(RAW[id], null, 2); }).join('\\n\\n');
    }
    panel.classList.add('on');
    window.scrollBy(0, 200);
  }
  document.addEventListener('click', function (e) {
    var ev = e.target.closest ? e.target.closest('.ev') : null;
    if (ev) { drill(ev.getAttribute('data-ev'), '原始事件'); return; }
    var num = e.target.closest ? e.target.closest('.num') : null;
    if (num) { drill(num.getAttribute('data-ev'), '指标证据'); return; }
    var st = e.target.closest ? e.target.closest('.stage') : null;
    if (st) {
      var cells = st.querySelectorAll('[data-ev]');
      var ids = [];
      for (var i = 0; i < cells.length; i++) {
        ids = ids.concat(evList(cells[i].getAttribute('data-ev')));
      }
      drill(ids.join(','), st.getAttribute('data-stage') + ' 阶段的证据事件');
    }
  });
  document.getElementById('drill-close').addEventListener('click', function () {
    document.getElementById('drill').classList.remove('on');
  });

  function fmt(x, d) { return (isFinite(x) ? x.toFixed(d) : '—'); }

  function paint(taskId) {
    var w = WATERFALLS[taskId];
    if (!w || !w.segments) return;
    var total = 0;
    w.segments.forEach(function (s) { total += Math.max(s.days, 0); });
    if (total <= 0) total = 1;
    var bar = document.getElementById('wf-bar');
    bar.innerHTML = '';
    w.segments.forEach(function (s) {
      var d = document.createElement('div');
      d.style.width = (Math.max(s.days, 0) / total * 100) + '%';
      d.style.background = s.color;
      if (s.key === 'wait') d.className = 'wait';
      d.title = s.label + '：' + fmt(s.days, 2) + ' 天';
      d.textContent = s.days > 0.4 ? fmt(s.days, 1) : '';
      bar.appendChild(d);
    });
    var legend = document.getElementById('wf-legend');
    legend.innerHTML = w.segments.map(function (s) {
      return '<span><i style="background:' + s.color + '"></i>' + s.label + ' ' + fmt(s.days, 2) + ' 天</span>';
    }).join('');
    document.getElementById('wf-total').textContent = fmt(w.total_days, 1);
    var basis = document.getElementById('wf-basis');
    basis.textContent = w.basis === 'measured' ? '实测口径' : '排期口径';
    basis.className = 'badge ' + (w.basis === 'measured' ? 'measured' : 'plan');

    var meta = [
      ['任务卡', w.task_id], ['负责人', w.owner || '—'],
      ['模块', w.module || '—'], ['依赖', (w.depends_on || []).join(' → ') || '无'],
      ['可开始日', '第 ' + fmt(w.ready_day, 1) + ' 天'], ['计划起止', '第 ' + fmt(w.start_day, 1) + ' → ' + fmt(w.end_day, 1) + ' 天'],
      ['排期估算', fmt(w.estimate, 1) + ' 天'], ['验收条数', (w.acceptance || []).length + ' 条']
    ];
    document.getElementById('wf-meta').innerHTML = meta.map(function (m) {
      return '<div class="mcell"><div class="k">' + m[0] + '</div><div class="v">' + m[1] + '</div></div>';
    }).join('');
    document.getElementById('wf-dep').innerHTML = (w.acceptance || []).length
      ? '验收：' + w.acceptance.map(function (a) { return '「' + a + '」'; }).join(' ')
      : '';
  }
  var sel = document.getElementById('wf-task');
  if (sel) {
    sel.value = PICKED;
    sel.addEventListener('change', function () { paint(sel.value); });
    paint(sel.value);
  }

  function copyCmd(action) {
    var target = '';
    var note = '';
    if (action === 'pause') { note = document.getElementById('note-pause').value; }
    if (action === 'comment') {
      target = document.getElementById('target-comment').value;
      note = document.getElementById('note-comment').value;
    }
    if (action === 'retry_task') {
      target = document.getElementById('target-retry').value;
      note = document.getElementById('note-retry').value;
    }
    var cmd = 'python -m req_builder.viz.o2 --run-dir "' + RUN_DIR + '" --command ' + action
      + (target ? ' --target ' + target : '')
      + (note ? ' --note "' + note + '"' : '');
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(cmd).then(function () {
        document.getElementById('offline-hint').textContent = '已复制：' + cmd;
      }, function () { window.prompt('复制这条命令执行：', cmd); });
    } else { window.prompt('复制这条命令执行：', cmd); }
  }

  function send(action) {
    var target = '';
    var note = '';
    if (action === 'pause') { note = document.getElementById('note-pause').value; }
    if (action === 'comment') {
      target = document.getElementById('target-comment').value;
      note = document.getElementById('note-comment').value;
    }
    if (action === 'retry_task') {
      target = document.getElementById('target-retry').value;
      note = document.getElementById('note-retry').value;
    }
    if (location.protocol !== 'http:' && location.protocol !== 'https:') { copyCmd(action); return; }
    fetch('/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: action, target: target, note: note })
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (j.ok) { location.reload(); }
      else { window.alert('下发失败：' + (j.error || '未知错误')); }
    }).catch(function (err) { window.alert('请求失败，确认本地服务还在跑：' + err); });
  }
  Array.prototype.forEach.call(document.querySelectorAll('button[data-act]'), function (btn) {
    btn.addEventListener('click', function () { send(btn.getAttribute('data-act')); });
  });
  if (location.protocol.indexOf('http') === 0) {
    document.getElementById('offline-hint').textContent = '已连接本地服务：按钮会真正下发命令，命令流水与父子对比会自动刷新。';
  }
})();
"""


def _js(data: dict[str, Any]) -> str:
    raw = {e["event_id"]: e for e in data["raw"]["events"]}
    return (
        JS.replace("@@RUN_DIR@@", json.dumps(data["run_dir"]))
        .replace("@@RAW@@", json.dumps(raw, ensure_ascii=False))
        .replace("@@WF@@", json.dumps(data["waterfalls"], ensure_ascii=False))
        .replace("@@PICKED@@", json.dumps(data["picked"]))
    )


def _css() -> str:
    palette = {
        "@@BG@@": BG,
        "@@INK@@": INK,
        "@@SUB@@": INK_SUB,
        "@@FAINT@@": INK_FAINT,
        "@@BORDER@@": BORDER,
        "@@CARD@@": CARD,
        "@@ACCENT@@": ACCENT,
        "@@GRID@@": GRID,
        "@@FONT@@": FONT_STACK,
        "@@W@@": str(PAGE_W),
    }
    css = CSS
    for key, value in palette.items():
        css = css.replace(key, value)
    return css


def render_o2(data: dict[str, Any]) -> str:
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{data['run_id']} · 执行观测台（O2）</title>\n<style>"
        + _css()
        + "</style>\n</head>\n<body>\n<div class=\"wrap\">\n"
        + _header(data)
        + _health_section(data)
        + _waterfall_section(data)
        + _alerts_section(data)
        + _commands_section(data)
        + '<div class="footer">由 req_builder 生成 · 观测源：spans.jsonl / events.jsonl / state.json / artifacts<br>'
        "三视图的每个数字都带证据事件号，双击钻取；三动作一律 command.issued + command.applied，重试走 fork</div>\n"
        "</div>\n"
        '<div class="drill" id="drill"><div class="drill-head"><b>钻取</b><span id="drill-title"></span>'
        '<span class="spacer"></span><button id="drill-close">关闭</button></div>'
        '<pre class="drill-body" id="drill-body"></pre></div>\n'
        "<script>\n" + _js(data) + "\n</script>\n</body>\n</html>\n"
    )


def render_to_file(run_dir: str | Path, out_path: str | Path, task_id: str | None = None) -> Path:
    data = obs.overview(run_dir, task_id=task_id)
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_o2(data), encoding="utf-8")
    print(f"[o2] {data['run_id']}：健康 {data['health']['score']} 分、告警 {len(data['alerts'])} 条、"
          f"命令 {len(data['command_log']['commands'])} 条、"
          f"fork {'有' if data.get('compare') else '无'}")
    print(f"[o2] 已输出：{target.resolve()}")
    return target


# ---------------------------------------------------------------- 真下发命令（服务端）

def execute_command(
    run_dir: str | Path,
    action: str,
    target: str = "",
    note: str = "",
    by: str = "user",
) -> dict[str, Any]:
    """调用 A 的命令通道落 issued + applied；retry_task 会 fork 新运行。

    与 `python -m scheduler.cli command` 同一套函数，保证两条入口产出同样的事件。
    """
    from scheduler.commands import apply_command, issue_command
    from scheduler.context import RunConfig
    from scheduler.events import EventEmitter
    from scheduler.store import RunStore

    directory = Path(run_dir)
    run_id = directory.name
    store = RunStore(directory.parent)
    state = store.load_state(run_id)
    if state is None:
        raise RuntimeError(f"运行实例 {run_id} 不存在")
    cfg = RunConfig.from_dict(json.loads(store.config_path(run_id).read_text(encoding="utf-8")))

    cmd = issue_command(state, action, target=target, note=note, by=by)
    emitter = EventEmitter(store, run_id, state.ledger.global_used)
    emitter.emit(
        "command.issued",
        stage=state.phase,
        actor=by,
        payload_ref=action,
        detail={"command_id": cmd["command_id"], "target": cmd["target"], "note": cmd["note"]},
    )
    effect = apply_command(store, cfg, state, cmd)
    store.save_state(state)
    emitter.emit(
        "command.applied",
        stage=state.phase,
        actor="scheduler",
        payload_ref=action,
        detail={"command_id": cmd["command_id"], "effect": effect},
    )
    return {"command": cmd, "effect": effect}


class _Handler(BaseHTTPRequestHandler):
    run_dir: Path
    out_path: Path

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?")[0] not in ("/", "/index.html"):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        render_to_file(self.run_dir, self.out_path)
        self._send(200, self.out_path.read_bytes(), "text/html; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?")[0] != "/command":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            result = execute_command(
                self.run_dir,
                str(payload.get("action") or ""),
                target=str(payload.get("target") or ""),
                note=str(payload.get("note") or ""),
            )
            render_to_file(self.run_dir, self.out_path)
            body = json.dumps({"ok": True, "result": result["effect"]}, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
        except Exception as exc:  # 页面上直接看到失败原因
            body = json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False).encode("utf-8")
            self._send(500, body, "application/json; charset=utf-8")

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[o2] {fmt % args}")


def serve(run_dir: str | Path, out_path: str | Path, port: int = 8777) -> None:
    run_dir = Path(run_dir).resolve()
    out_path = Path(out_path).resolve()
    render_to_file(run_dir, out_path)
    _Handler.run_dir = run_dir
    _Handler.out_path = out_path
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    print(f"[o2] 服务已起：http://127.0.0.1:{port}/  （Ctrl+C 退出）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[o2] 已退出")
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="req_builder.viz.o2", description="O2 执行观测台：三视图 + 三动作")
    parser.add_argument("--run-dir", required=True, help="运行目录，例如 runs/O2RUN")
    parser.add_argument("--run-id", help="与 --run-dir 二选一，等价于 runs/<run_id>")
    parser.add_argument("--task", default=None, help="单卡瀑布默认选中的任务卡号")
    parser.add_argument("--out", default=None, help="输出 HTML，默认 <run-dir>/viz/o2.html")
    parser.add_argument("--command", dest="action", choices=["pause", "comment", "retry_task"], default=None,
                        help="下发一条命令（等价 scheduler.cli command）")
    parser.add_argument("--target", default="", help="命令目标：批注对象或要重试的任务卡号")
    parser.add_argument("--note", default="", help="命令备注")
    parser.add_argument("--serve", action="store_true", help="起本地服务，页面按钮可真正下发命令")
    parser.add_argument("--port", type=int, default=8777)
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir) if args.run_dir else (Path("runs") / args.run_id if args.run_id else None)
    if not run_dir:
        parser.error("需要 --run-dir 或 --run-id")
    out_path = Path(args.out) if args.out else run_dir / "viz" / "o2.html"

    if args.action:
        result = execute_command(run_dir, args.action, target=args.target, note=args.note)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.serve:
        serve(run_dir, out_path, port=args.port)
        return 0
    render_to_file(run_dir, out_path, task_id=args.task)
    return 0


if __name__ == "__main__":
    sys.exit(main())
