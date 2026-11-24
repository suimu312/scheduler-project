"""payload_ref 解引用：让 A 的事件在七视图里也有内容可画。

E 自己的事件把内容内联在 `payload` 里；A 的编排器只在事件上记一个引用
（`payload_ref`，形如 `discussion_minutes:ST-002-01` 或 `conflict_register:CFL-001`）。
七视图只读 payload，所以这里在渲染前把被引用的产物片段解引用进 payload——
**只补内容，不改语义**：解引用后的 payload 与 E 内联 payload 等价，各视图不用分支。

闸门这一路另作处理：A 的 `gate.evaluated` 只带闸门号，不带判定结论。
「下一阶段是否真的进入」是可观测的推进证据，据此把结论补成 advance；
这是从事件顺序读出来的事实，不是猜字段。
"""

from __future__ import annotations

import copy
from typing import Any

ID_KEYS = (
    "statement_id",
    "conflict_id",
    "task_id",
    "phase_id",
    "source_id",
    "gate_id",
    "id",
)


def _find_record(data: Any, ident: str) -> dict[str, Any] | None:
    """在产物 data 里按 id 字段找一条记录：先按字典键，再递归找列表项。"""
    if not ident:
        return data if isinstance(data, dict) else None
    if isinstance(data, dict):
        if ident in data and isinstance(data[ident], dict):
            return data[ident]
        for value in data.values():
            found = _find_record(value, ident)
            if found:
                return found
        return None
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            for key in ID_KEYS:
                if str(item.get(key) or "") == ident:
                    return item
        return None
    return None


def hydrate_events(events: list[Any], artifacts: dict[str, Any] | None) -> list[Any]:
    """把 `payload_ref` 指向的产物片段填进 payload（原地修改，返回同一列表）。"""
    bundle = artifacts or {}
    for event in events:
        if getattr(event, "payload", None) or not getattr(event, "payload_ref", ""):
            continue
        name, _, ident = str(event.payload_ref).partition(":")
        artifact = bundle.get(name)
        if not isinstance(artifact, dict):
            continue
        data = artifact.get("data", artifact)
        record = _find_record(data, ident)
        if record:
            event.payload = copy.deepcopy(record)
    return events


def infer_gate_verdicts(events: list[Any]) -> list[Any]:
    """补闸门结论：闸门号后一阶段确实进入过，就记为 advance（并写明依据）。"""
    entered = [str(getattr(e, "stage", "") or "") for e in events if e.event_type == "stage.enter"]
    for event in events:
        if event.event_type != "gate.evaluated":
            continue
        payload = getattr(event, "payload", None) or {}
        if payload.get("action"):
            continue
        gate_id = str(payload.get("gate_id") or getattr(event, "payload_ref", "") or "")
        if not gate_id.startswith("G_"):
            continue
        parts = gate_id.split("_")
        if len(parts) < 3:
            continue
        target_stage = parts[2]
        if target_stage in entered:
            event.payload = dict(payload, gate_id=gate_id, action="advance", reason="后续阶段已进入")
    return events
