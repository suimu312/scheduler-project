"""V7 回放控制：底部常驻，回答"那一刻到底发生了什么"。

回放的唯一依据是 seq 单调递增：进度条给截止序号，页面里带 data-seq 的元素
超过截止序号就隐藏。真正的"重算"由命令行 --until 重新渲染完成，
界面不持有业务状态，也不回写运行状态（docx §6.1）。
"""

from __future__ import annotations

from typing import Any

from .. import svg
from ..events import ReplayWindow
from ..theme import ACCENT, BORDER, CRITICAL, INK, INK_FAINT, INK_SUB, WARN

HEIGHT = 72
TRACK_W = 720


def render(window: ReplayWindow, artifacts: dict, width: float = 1180) -> tuple[str, float]:
    total = max(window.until_seq, 1)
    current = window.until_seq
    anomalies = window.anomalies()
    ratio = current / total if total else 0

    x0 = 120
    track_w = min(TRACK_W, width - 420)
    y = 34

    parts: list[str] = []
    parts.append(svg.text(12, 22, "单一事实来源：events.jsonl（只追加、按 seq 重放）", size=8.5,
                          fill=INK_FAINT))

    parts.append(svg.rect(x0, y, track_w, 8, fill="#eef1f5", stroke=BORDER, rx=4))
    parts.append(svg.rect(x0, y, track_w * ratio, 8, fill=ACCENT, rx=4))

    for event in anomalies:
        px = x0 + track_w * (event.seq / total)
        color = CRITICAL if event.event_type == "guard.signal" else WARN
        parts.append(svg.circle(px, y + 4, 3.2, fill=color))
    if anomalies:
        parts.append(
            svg.text(x0, y + 26,
                     f"异常 {len(anomalies)} 处（灰蒙层=发言趋同，黄=人工介入/闸门未过）",
                     size=8, fill=INK_FAINT)
        )

    parts.append(
        svg.text(x0 + track_w + 12, y + 8,
                 f"EVT-{current:05d} / EVT-{total:05d}", size=9, fill=INK_SUB, weight="bold")
    )
    parts.append(
        svg.text(12, y + 44, "拖动进度条 = 指定截止序号；「跳到下一异常」按 seq 前进", size=8,
                 fill=INK_FAINT)
    )
    return "".join(parts), HEIGHT
