"""观测埋点出口：spans.jsonl（architecture §10 追踪视图）。

events.jsonl 回答「发生了什么」（事件流）；spans.jsonl 回答「一次运行里每个
动作花了多久、父子关系如何」（追踪视图）。两者都由编排层统一出口写入，
模块不直接落盘：
- trace_id = run_id，整轮一条追踪链；
- 每个 span 带 span_id / parent_id / kind / stage / 起止时间 / 耗时 / 状态；
- 指标视图（计数、均值、分阶段分布）由观测脚本从 events + spans 聚合，
  这里只负责如实记录。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .context import now_iso
from .store import RunStore

SPAN_KINDS = ("stage", "gate", "model", "artifact", "command", "system")


class SpanRecorder:
    """单次运行的 span 记录器；开一个 span、结束一个 span，逐行写 spans.jsonl。"""

    def __init__(self, store: RunStore, run_id: str) -> None:
        self.store = store
        self.run_id = run_id
        self._open: dict[str, dict[str, Any]] = {}
        self._seq = self._bootstrap_seq()

    def _bootstrap_seq(self) -> int:
        spans = self.store.read_spans(self.run_id)
        max_n = 0
        for sp in spans:
            sid = str(sp.get("span_id") or "")
            if sid.startswith("SPAN-"):
                try:
                    max_n = max(max_n, int(sid.split("-")[1]))
                except (ValueError, IndexError):
                    pass
        return max_n

    def _next_id(self) -> str:
        self._seq += 1
        return f"SPAN-{self._seq:05d}"

    def start(
        self,
        kind: str,
        name: str,
        stage: str,
        parent_id: str | None = None,
        refs: dict[str, Any] | None = None,
    ) -> str:
        if kind not in SPAN_KINDS:
            raise ValueError(f"非法 span kind：{kind}")
        span_id = self._next_id()
        record = {
            "span_id": span_id,
            "parent_id": parent_id,
            "trace_id": self.run_id,
            "run_id": self.run_id,
            "kind": kind,
            "name": name,
            "stage": stage,
            "start_ts": now_iso(),
            "end_ts": None,
            "duration_ms": None,
            "status": "running",
            "refs": dict(refs or {}),
            "detail": {},
        }
        self._open[span_id] = record
        self._open[span_id]["_perf"] = time.perf_counter()
        return span_id

    def end(self, span_id: str, status: str = "ok", detail: dict[str, Any] | None = None) -> dict[str, Any] | None:
        record = self._open.pop(span_id, None)
        if record is None:
            return None
        perf = record.pop("_perf", None)
        record["end_ts"] = now_iso()
        if perf is not None:
            record["duration_ms"] = round((time.perf_counter() - perf) * 1000, 2)
        record["status"] = status
        if detail:
            record["detail"] = dict(detail)
        self.store.append_span(record)
        return record

    def span(
        self,
        kind: str,
        name: str,
        stage: str,
        parent_id: str | None = None,
        refs: dict[str, Any] | None = None,
    ) -> "_SpanContext":
        return _SpanContext(self, kind, name, stage, parent_id, refs)

    def flush_pending(self) -> None:
        """进程退出前把没显式结束的 span 标记为 interrupted，避免追踪链断尾。"""
        for span_id, record in list(self._open.items()):
            self.end(span_id, status="interrupted")


class _SpanContext:
    def __init__(
        self,
        recorder: SpanRecorder,
        kind: str,
        name: str,
        stage: str,
        parent_id: str | None,
        refs: dict[str, Any] | None,
    ) -> None:
        self.recorder = recorder
        self.kind = kind
        self.name = name
        self.stage = stage
        self.parent_id = parent_id
        self.refs = refs
        self.span_id: str | None = None

    def __enter__(self) -> str:
        self.span_id = self.recorder.start(self.kind, self.name, self.stage, self.parent_id, self.refs)
        return self.span_id

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        status = "error" if exc_type is not None else "ok"
        detail: dict[str, Any] = {}
        if exc is not None:
            detail["error"] = f"{type(exc).__name__}: {exc}"
        if self.span_id:
            self.recorder.end(self.span_id, status=status, detail=detail)


def load_spans(run_id: str, root: str | Path) -> list[dict[str, Any]]:
    """复盘用：从 spans.jsonl 读全部 span，不产生新记录。"""
    return RunStore(root).read_spans(run_id)
