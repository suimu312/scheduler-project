"""事件流读取与回放窗口。

渲染函数的输入只有"事件数组 + 截止序号"，输出界面，本身是纯函数：
- 实时：截止序号 = 最新；
- 回放：截止序号由 --until / 进度条给；
同一套代码同时拿到监控与复盘，界面自己不留任何业务状态。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..contracts import RunEvent

ANOMALY_TYPES = ("guard.signal", "conflict.detected", "human.decision")
GATE_FAIL_ACTIONS = ("rework", "wait_human", "stay", "escalate", "block", "fail")


def load_events(path: str | Path) -> list[RunEvent]:
    """读 events.jsonl，空行跳过，seq 缺失时按行号补。"""
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"事件文件不存在：{file_path}")
    events: list[RunEvent] = []
    with file_path.open("r", encoding="utf-8") as handle:
        for index, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{file_path.name} 第 {index} 行不是合法 JSON：{exc}") from exc
            events.append(RunEvent.from_dict(payload, seq=index))
    events.sort(key=lambda e: e.seq)
    return events


def events_to_jsonl(events: Iterable[RunEvent]) -> str:
    return "\n".join(json.dumps(event.to_dict(), ensure_ascii=False) for event in events) + "\n"


@dataclass
class ReplayWindow:
    """截止序号窗口：视图只画这个范围以内的事件。"""

    events: list[RunEvent]
    until_seq: int

    @classmethod
    def of(cls, events: list[RunEvent], until_seq: int | None = None) -> "ReplayWindow":
        if not events:
            return cls(events=[], until_seq=0)
        max_seq = max(event.seq for event in events)
        until = max_seq if until_seq is None else max(0, min(int(until_seq), max_seq))
        visible = [event for event in events if event.seq <= until]
        return cls(events=visible, until_seq=until)

    @property
    def max_seq(self) -> int:
        return max((event.seq for event in self.events), default=0)

    def of_type(self, *types: str) -> list[RunEvent]:
        wanted = set(types)
        return [event for event in self.events if event.event_type in wanted]

    def last(self, event_type: str) -> RunEvent | None:
        for event in reversed(self.events):
            if event.event_type == event_type:
                return event
        return None

    def anomalies(self) -> list[RunEvent]:
        """V7 的"跳到下一异常"：防趋同信号、被判失败的闸门、人工介入。"""
        out: list[RunEvent] = []
        for event in self.events:
            if event.event_type == "guard.signal":
                out.append(event)
            elif event.event_type == "gate.evaluated" and str(
                event.get("action", "")
            ).lower() in GATE_FAIL_ACTIONS:
                out.append(event)
            elif event.event_type == "human.decision" and str(event.get("action", "")).lower() in (
                "reject",
                "adjudicate",
                "abort",
            ):
                out.append(event)
        return out


def stage_spans(events: list[RunEvent]) -> list[dict[str, Any]]:
    """把 stage.enter 折成阶段区间，给 V1 画泳道分隔与阶段标题。"""
    spans: list[dict[str, Any]] = []
    open_span: dict[str, Any] | None = None
    for event in events:
        if event.event_type == "stage.enter":
            if open_span:
                open_span["to_seq"] = event.seq
                spans.append(open_span)
            open_span = {
                "stage": event.get("stage") or event.stage,
                "from_seq": event.seq,
                "to_seq": None,
            }
        elif event.event_type == "stage.exit" and open_span:
            open_span["to_seq"] = event.seq
            spans.append(open_span)
            open_span = None
    if open_span:
        spans.append(open_span)
    return spans


def discussion_phases(events: list[RunEvent]) -> dict[str, list[RunEvent]]:
    """P3 三段式：并行陈述 / 定向质询 / 投票。按事件类型分桶，不猜轮次。"""
    return {
        "statements": [e for e in events if e.event_type == "agent.statement"],
        "queries": [e for e in events if e.event_type == "agent.query"],
        "votes": [e for e in events if e.event_type == "agent.vote"],
    }


def guard_signals(events: list[RunEvent]) -> list[dict[str, Any]]:
    """发言趋同信号：相似度 + 被判无效的块（docx §6.4 第三条）。"""
    out: list[dict[str, Any]] = []
    for event in events:
        if event.event_type != "guard.signal":
            continue
        out.append(
            {
                "seq": event.seq,
                "agent_id": str(event.get("agent_id") or ""),
                "signal": str(event.get("signal") or "similarity_invalid"),
                "value": float(event.get("value") or event.get("similarity") or 0.0),
                "reason": str(event.get("reason") or ""),
                "invalid_block": str(event.get("invalid_block") or event.get("statement_id") or ""),
            }
        )
    return out


def budget_series(events: list[RunEvent]) -> list[dict[str, Any]]:
    """预算曲线：每次 budget.charged 记一个点。"""
    points: list[dict[str, Any]] = []
    for event in events:
        if event.event_type != "budget.charged":
            continue
        used = int(event.get("global_used") or event.get("used") or event.budget_used or 0)
        limit = int(event.get("limit") or event.get("global_limit") or 30)
        points.append({"seq": event.seq, "used": used, "limit": max(limit, 1)})
    return points


GATE_TARGETS = {
    "G_P1_P2": "P2",
    "G_P2_P3": "P3",
    "G_P3_P4": "P4",
    "G_P4_P5": "P5",
    "G_P5_P6": "P6",
    "G_P6_P7": "P7",
    "G_P7_BASELINE": "BASELINE_FROZEN",
}

_EXIT_LABELS = {
    "stay": "原地返工",
    "rework": "退回返工",
    "escalate": "交人裁决",
    "abort": "中止",
    "wait_human": "待人签",
    "freeze": "已冻结",
}


def gate_states(events: list[RunEvent]) -> dict[str, dict[str, Any]]:
    """闸门灯：同一闸门多次判定取最后一次（后判为准）。

    引擎的 gate.evaluated 只记闸门号（payload_ref），没带判定结果，所以这里按事件流
    自己推：目标阶段真的进去了 = 通过；没进去就看这条闸门之后的阶段退出动作。
    事件是唯一事实来源，推出来的结论和 state.json 的重放结果一致。
    """
    gates: dict[str, dict[str, Any]] = {}
    entered = {event.stage for event in events if event.stage}

    for index, event in enumerate(events):
        if event.event_type != "gate.evaluated":
            continue
        gate_id = str(event.get("gate_id") or event.get("gate") or event.payload_ref or "")
        if not gate_id:
            continue
        action = str(event.get("action") or event.get("verdict") or "").lower()
        passed = bool(event.get("pass")) or action in ("advance", "freeze", "pass", "pass_next")
        if not passed:
            target = GATE_TARGETS.get(gate_id)
            if target and target in entered:
                passed, action = True, "advance"
        if not passed and not action:
            # 没通过：紧跟其后的阶段退出 / 终止动作就是原因
            for nxt in events[index + 1:]:
                if nxt.event_type == "gate.evaluated":
                    break
                if nxt.event_type not in ("stage.exit", "run.terminated", "human.decision"):
                    continue
                ref = str(nxt.payload_ref or "")
                if nxt.event_type == "human.decision" or "human" in ref:
                    action = "待人签"
                    break
                if ref == "advance":
                    passed = True
                    action = "advance"
                    break
                action = _EXIT_LABELS.get(ref, ref)
                break
        gates[gate_id] = {
            "seq": event.seq,
            "action": action,
            "pass": passed,
            "reason": str(event.get("reason") or ""),
        }
    return gates


def artifact_index(artifacts: dict[str, Any] | None) -> dict[str, Any]:
    return artifacts or {}
