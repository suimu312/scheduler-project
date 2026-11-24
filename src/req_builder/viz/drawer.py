"""阶段产物抽屉（W7）：每个阶段节点可点开「输入快照 / 输出 JSON 可下载 / 判定证据」。

仍然是投影：数据全部来自 events.jsonl 与 artifacts，不在页面里存任何业务状态。
抽屉是 HTML + 一小段内联脚本，不引外部依赖，断网可看，导出仍是单文件。
"""

from __future__ import annotations

import json
from typing import Any

from .events import ReplayWindow

STAGE_LABELS = {
    "P1": "规格输入",
    "P2": "模块划分",
    "P3": "讨论裁决",
    "P4": "三套方案",
    "P5": "评审裁决",
    "P6": "WBS 定稿",
    "P7": "排期冻结",
}
STAGE_ORDER = ("P1", "P2", "P3", "P4", "P5", "P6", "P7")


def _payload(event: Any) -> dict[str, Any]:
    payload = getattr(event, "payload", None)
    return dict(payload) if isinstance(payload, dict) else {}


def _artifact_stage(artifact: Any) -> str:
    if isinstance(artifact, dict):
        meta = artifact.get("meta")
        if isinstance(meta, dict) and meta.get("stage"):
            return str(meta["stage"])
    return ""


def _artifact_producer(artifact: Any) -> str:
    if isinstance(artifact, dict):
        meta = artifact.get("meta")
        if isinstance(meta, dict) and meta.get("produced_by"):
            return str(meta["produced_by"])
    return ""


def collect_stage_data(window: ReplayWindow, artifacts: dict[str, Any] | None) -> dict[str, Any]:
    """把「每个阶段有什么输入 / 产出 / 判定」算成一个纯数据字典，供页面脚本消费。"""
    bundle = dict(artifacts or {})
    events = list(window.events)

    by_stage: dict[str, list[Any]] = {}
    for event in events:
        stage = str(getattr(event, "stage", "") or "")
        if stage:
            by_stage.setdefault(stage, []).append(event)

    outputs_by_stage: dict[str, list[dict[str, Any]]] = {}
    for name, artifact in bundle.items():
        stage = _artifact_stage(artifact)
        if not stage:
            continue
        outputs_by_stage.setdefault(stage, []).append(
            {
                "name": name,
                "producer": _artifact_producer(artifact),
                "json": artifact,
            }
        )

    stages: list[dict[str, Any]] = []
    for index, stage_id in enumerate(STAGE_ORDER):
        stage_events = by_stage.get(stage_id)
        if not stage_events:
            continue

        enter = next((e for e in stage_events if e.event_type == "stage.enter"), None)
        from_stage = str(_payload(enter).get("from_stage") or "") if enter else ""
        prev_stage = STAGE_ORDER[index - 1] if index > 0 else ""
        input_stage = prev_stage if prev_stage else ""

        gates = [
            {
                # E 侧写 payload.gate_id；A 侧把闸门号放在事件级 payload_ref 上，两种都认
                "gate_id": str(
                    _payload(e).get("gate_id")
                    or getattr(e, "payload_ref", None)
                    or "gate"
                ),
                "action": str(_payload(e).get("action") or "evaluated"),
                "reason": str(_payload(e).get("reason") or ""),
                "seq": e.seq,
                "event_id": getattr(e, "event_id", ""),
            }
            for e in stage_events
            if e.event_type == "gate.evaluated"
        ]
        guards = [
            {
                "signal": str(_payload(e).get("signal") or ""),
                "reason": str(_payload(e).get("reason") or ""),
                "value": _payload(e).get("value"),
                "seq": e.seq,
            }
            for e in stage_events
            if e.event_type == "guard.signal"
        ]
        human = [
            {
                "action": str(_payload(e).get("action") or ""),
                "text": str(_payload(e).get("text") or ""),
                "seq": e.seq,
            }
            for e in stage_events
            if e.event_type == "human.decision"
        ]

        stages.append(
            {
                "id": stage_id,
                "label": STAGE_LABELS.get(stage_id, stage_id),
                "from_stage": from_stage,
                "seq_from": min(e.seq for e in stage_events),
                "seq_to": max(e.seq for e in stage_events),
                "event_count": len(stage_events),
                "inputs": outputs_by_stage.get(input_stage, []),
                "input_stage": input_stage,
                "outputs": outputs_by_stage.get(stage_id, []),
                "gates": gates,
                "guards": guards,
                "human": human,
            }
        )

    return {
        "stages": stages,
        "default_stage": str(window.last("stage.enter").get("stage")) if window.last("stage.enter") else (stages[-1]["id"] if stages else ""),
    }


def render_drawer(window: ReplayWindow, artifacts: dict[str, Any] | None) -> str:
    data = collect_stage_data(window, artifacts)
    stages = data["stages"]
    if not stages:
        return ""

    chips = "".join(
        f'<button class="stage-chip" data-stage="{item["id"]}" type="button">'
        f'<b>{item["id"]}</b><span>{item["label"]}</span>'
        f'<i>{len(item["outputs"])} 产出</i></button>'
        for item in stages
    )
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

    return f"""
<section class="card" id="stage-drawer">
  <div class="card-head">
    <span class="chip">D</span>
    <span class="card-title">阶段产物抽屉</span>
    <span class="card-q">每个阶段节点：输入快照 / 输出 JSON 可下载 / 判定证据</span>
  </div>
  <div class="stage-strip">{chips}</div>
  <div class="drawer-panel" id="drawer-panel"></div>
</section>
<script>var STAGE_DATA = {blob};</script>"""


def drawer_script() -> str:
    """抽屉的交互逻辑：点阶段 → 渲染三块内容；产物走 Blob 下载，不联网。"""
    return """
(function () {
  var data = STAGE_DATA || { stages: [], default_stage: '' };
  var panel = document.getElementById('drawer-panel');
  if (!panel) { return; }
  var chips = document.querySelectorAll('.stage-chip');

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function pretty(o) { try { return JSON.stringify(o, null, 2); } catch (e) { return String(o); } }

  function download(name, obj) {
    var text = pretty(obj);
    var blob = new Blob([text], { type: 'application/json;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name + '.json';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }

  function artifactsHtml(list, kind, empty) {
    if (!list || !list.length) { return '<div class="drawer-empty">' + esc(empty) + '</div>'; }
    return '<div class="drawer-files">' + list.map(function (f, i) {
      return '<div class="drawer-file"><div class="fname">' + esc(f.name) + '.json' +
        (f.producer ? '<span class="fby">' + esc(f.producer) + '</span>' : '') +
        '</div><div class="fops"><button class="mini" data-dl="' + i + '" data-kind="' + kind +
        '" type="button">下载 JSON</button>' +
        '<button class="mini ghost" data-peek="' + i + '" data-kind="' + kind +
        '" type="button">看内容</button></div></div>';
    }).join('') + '</div>';
  }

  function show(id) {
    var s = null;
    for (var i = 0; i < data.stages.length; i++) { if (data.stages[i].id === id) { s = data.stages[i]; } }
    if (!s) { panel.innerHTML = ''; return; }
    for (var j = 0; j < chips.length; j++) {
      chips[j].classList.toggle('active', chips[j].getAttribute('data-stage') === id);
    }
    var gates = (s.gates || []).map(function (g) {
      return '<div class="drawer-row"><span class="tag ' + esc(g.action) + '">' + esc(g.action) +
        '</span><b>' + esc(g.gate_id) + '</b><span class="dim">' + esc(g.reason) +
        '</span><span class="seq">EVT-' + ('00000' + g.seq).slice(-5) + '</span></div>';
    }).join('') || '<div class="drawer-empty">该阶段没有闸门判定</div>';
    var guards = (s.guards || []).map(function (g) {
      return '<div class="drawer-row"><span class="tag guard">' + esc(g.signal) + '</span><span class="dim">' +
        esc(g.reason) + '</span><span class="seq">EVT-' + ('00000' + g.seq).slice(-5) + '</span></div>';
    }).join('');
    var human = (s.human || []).map(function (h) {
      return '<div class="drawer-row"><span class="tag human">' + esc(h.action) + '</span><span class="dim">' +
        esc(h.text) + '</span><span class="seq">EVT-' + ('00000' + h.seq).slice(-5) + '</span></div>';
    }).join('');

    panel.innerHTML =
      '<div class="drawer-head"><b>' + esc(s.id) + ' ' + esc(s.label) + '</b>' +
      '<span class="dim">EVT-' + ('00000' + s.seq_from).slice(-5) + ' → EVT-' + ('00000' + s.seq_to).slice(-5) +
      ' · ' + s.event_count + ' 条事件' +
      (s.from_stage ? ' · 来自 ' + esc(s.from_stage) : '') + '</span></div>' +
      '<div class="drawer-cols">' +
      '<div class="drawer-col"><h4>① 输入快照' +
      (s.input_stage ? '<span class="dim">（' + esc(s.input_stage) + ' 的产物）</span>' : '<span class="dim">（本阶段无上游产物）</span>') +
      '</h4>' + artifactsHtml(s.inputs, 'in', '本阶段没有上游产物') + '</div>' +
      '<div class="drawer-col"><h4>② 输出 JSON<span class="dim">（点击即下载）</span></h4>' +
      artifactsHtml(s.outputs, 'out', '该阶段没有落盘产物') + '</div>' +
      '<div class="drawer-col"><h4>③ 判定证据</h4>' + gates + guards + human + '</div>' +
      '</div>';

    panel._stage = s;
    var btns = panel.querySelectorAll('[data-dl]');
    for (var k = 0; k < btns.length; k++) {
      btns[k].addEventListener('click', function () {
        var idx = parseInt(this.getAttribute('data-dl'), 10);
        var list = this.getAttribute('data-kind') === 'in' ? panel._stage.inputs : panel._stage.outputs;
        if (list && list[idx]) { download(list[idx].name, list[idx].json); }
      });
    }
    var peeks = panel.querySelectorAll('[data-peek]');
    for (var m = 0; m < peeks.length; m++) {
      peeks[m].addEventListener('click', function () {
        var idx = parseInt(this.getAttribute('data-peek'), 10);
        var list = this.getAttribute('data-kind') === 'in' ? panel._stage.inputs : panel._stage.outputs;
        if (!list || !list[idx]) { return; }
        var pre = panel.querySelector('.peek');
        if (!pre) { pre = document.createElement('pre'); pre.className = 'peek'; panel.appendChild(pre); }
        pre.textContent = pretty(list[idx].json);
        pre.scrollTop = 0;
      });
    }
  }

  for (var q = 0; q < chips.length; q++) {
    chips[q].addEventListener('click', function () { show(this.getAttribute('data-stage')); });
  }
  show(data.default_stage || (data.stages[0] || {}).id);
})();
"""
