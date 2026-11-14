"""事件流写入（architecture §10）。

A 只负责「按顺序、幂等、字段完整」地把事件写进 events.jsonl；渲染归 E。
"""

from __future__ import annotations

from typing import Any

from .context import RunEvent, next_event_id, now_iso
from .store import RunStore


class EventEmitter:
    """单调递增 EVT 序号；同一 event_id 重复 emit 会被忽略（保证重放不重复写）。"""

    def __init__(self, store: RunStore, run_id: str, last_budget: int = 0) -> None:
        self.store = store
        self.run_id = run_id
        self._last_id: str | None = None
        self._seen: set[str] = set()
        self._budget_hint = last_budget
        self._bootstrap()

    def _bootstrap(self) -> None:
        events = self.store.read_events(self.run_id)
        if events:
            self._last_id = str(events[-1].get("event_id") or "") or None
            self._seen = {str(e.get("event_id")) for e in events if e.get("event_id")}
            self._budget_hint = int(events[-1].get("budget_used") or self._budget_hint)

    def emit(
        self,
        event_type: str,
        stage: str,
        actor: str,
        payload_ref: str = "",
        budget_used: int | None = None,
        event_id: str | None = None,
    ) -> RunEvent:
        eid = event_id or next_event_id(self._last_id)
        if eid in self._seen:
            eid = next_event_id(self._last_id)
        event = RunEvent(
            event_id=eid,
            run_id=self.run_id,
            ts=now_iso(),
            stage=stage,
            event_type=event_type,
            actor=actor,
            payload_ref=payload_ref,
            budget_used=self._budget_hint if budget_used is None else int(budget_used),
        )
        self.store.append_event(event)
        self._last_id = eid
        self._seen.add(eid)
        return event

    def set_budget_hint(self, used: int) -> None:
        self._budget_hint = int(used)


def replay(run_id: str, root: str = "runs") -> list[dict[str, Any]]:
    """从 events.jsonl 复盘，不产生新的调用（§9.2 replay）。"""
    return RunStore(root).read_events(run_id)
