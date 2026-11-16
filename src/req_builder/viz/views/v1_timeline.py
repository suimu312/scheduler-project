"""V1 运行时间轴：横轴为事件序号，泳道为编排器与 A1–A6。

回答两个问题：现在跑到哪了、谁在说话。
发言块长度按字数，防趋同信号叠灰蒙层并标相似度（docx §6.4 第三条）。
"""

from __future__ import annotations

from .. import svg
from ..events import ReplayWindow, guard_signals, stage_spans
from ..theme import (
    AGENT_COLORS,
    BORDER,
    GRID,
    INK,
    INK_FAINT,
    INK_SUB,
    LANE_LABEL,
    LANES,
    STAGE_LABEL,
)

LABEL_W = 96
LANE_H = 32
TOP_PAD = 26
AXIS_H = 22


def _lane_y(index: int) -> float:
    return TOP_PAD + index * LANE_H + LANE_H / 2


def _lane_index(actor: str) -> int:
    actor = str(actor or "scheduler")
    return LANES.index(actor) if actor in LANES else 0


def render(window: ReplayWindow, artifacts: dict, width: float = 1180) -> tuple[str, float]:
    height = TOP_PAD + len(LANES) * LANE_H + AXIS_H + 10
    plot_x0 = LABEL_W + 8
    plot_x1 = width - 24
    plot_w = plot_x1 - plot_x0

    max_seq = max(window.until_seq, 1)
    step = max(6.0, min(30.0, plot_w / max(max_seq, 1)))

    def x_of(seq: int) -> float:
        return plot_x0 + (seq - 1) * step

    parts: list[str] = []

    # 泳道底纹与标签
    for index, lane in enumerate(LANES):
        y = TOP_PAD + index * LANE_H
        parts.append(svg.rect(LABEL_W, y, plot_x1 - LABEL_W, LANE_H, fill="#fbfcfd", stroke=GRID))
        parts.append(svg.rect(LABEL_W - 4, y + 2, 3, LANE_H - 4, fill=AGENT_COLORS.get(lane, INK_FAINT)))
        parts.append(
            svg.text(
                LABEL_W - 10,
                y + LANE_H / 2 + 4,
                LANE_LABEL.get(lane, lane),
                size=10.5,
                fill=INK_SUB,
                anchor="end",
            )
        )

    # 阶段分隔线与阶段标题（标签按实际字宽排，避免相邻阶段标题叠在一起）
    spans = stage_spans(window.events)
    label_end = -1e9
    for span in spans:
        from_seq = span["from_seq"]
        to_seq = span.get("to_seq") or max_seq
        x0 = x_of(from_seq)
        x1 = x_of(min(to_seq, max_seq))
        label = STAGE_LABEL.get(str(span["stage"]), str(span["stage"]))
        parts.append(
            svg.line(x0, TOP_PAD - 6, x0, TOP_PAD + len(LANES) * LANE_H, stroke="#c9d0da", dash="3,3")
        )
        if x0 + 4 > label_end + 10:
            parts.append(
                svg.text(x0 + 4, TOP_PAD - 10, label, size=9.5, fill=INK_SUB, weight="bold",
                         seq=from_seq)
            )
            label_end = x0 + 4 + svg.text_width(label, 9.5)
        else:
            parts.append(
                svg.text(x0 + 4, TOP_PAD - 1, label[:2], size=8.5, fill=INK_FAINT, seq=from_seq)
            )
        if x1 - x0 > 60:
            parts.append(
                svg.rect(x0, TOP_PAD, x1 - x0, len(LANES) * LANE_H, fill="#ffffff", stroke="none",
                         extra=' opacity="0.02"')
            )

    # 事件图元
    for event in window.events:
        lane = _lane_index(event.actor)
        y = _lane_y(lane)
        x = x_of(event.seq)

        if event.event_type in ("run.start", "run.end", "stage.enter", "stage.exit"):
            color = AGENT_COLORS["scheduler"]
            parts.append(svg.circle(x, y, 3.2, fill=color, seq=event.seq))
            continue

        if event.event_type == "agent.statement":
            words = int(event.get("word_count") or len(str(event.get("text") or "")))
            bar_w = max(8.0, min(68.0, 6.0 + words * 3.0))
            stance = str(
                event.get("stance") or event.get("position") or event.get("topic") or event.get("text") or ""
            )
            color = AGENT_COLORS.get(str(event.actor), INK_SUB)
            parts.append(
                svg.rect(x, y - 7, bar_w, 14, fill=color, rx=3, seq=event.seq,
                         extra=f' opacity="0.85"')
            )
            if bar_w >= 24 and stance:
                label = svg.clip(stance, 8.5, bar_w - 4)
                parts.append(
                    svg.text(x + 3, y + 3, label, size=8.5, fill="#ffffff", seq=event.seq)
                )
            continue

        if event.event_type == "agent.query":
            target = _lane_index(str(event.get("to") or event.get("to_agent") or "A1"))
            y2 = _lane_y(target)
            quote = str(event.get("quote") or event.get("citation") or event.get("text") or "")
            parts.append(svg.line(x, y, x, y2, stroke=AGENT_COLORS.get("A4", INK_SUB), sw=1.4,
                                  dash="4,2", seq=event.seq))
            if quote:
                parts.append(
                    svg.text(x + 4, (y + y2) / 2, svg.clip(quote, 8, 120), size=8, fill=INK_FAINT,
                             seq=event.seq)
                )
            continue

        if event.event_type == "agent.vote":
            vote = str(event.get("vote") or event.get("stance") or event.get("ballot") or "?")
            parts.append(
                svg.rect(x - 6, y - 7, 12, 14, fill="#ffffff", stroke=AGENT_COLORS.get("A2", INK_SUB),
                         rx=3, seq=event.seq)
            )
            parts.append(svg.text(x, y + 3.5, vote[:1].upper(), size=9, fill=INK, anchor="middle",
                                  seq=event.seq))
            continue

        if event.event_type == "guard.signal":
            signals = guard_signals([event])
            item = signals[0] if signals else {"value": 0.0, "signal": ""}
            value = float(item.get("value") or 0)
            overlay_h = 14
            parts.append(
                svg.rect(x - 8, y - overlay_h / 2, 16, overlay_h, fill="#9aa3b2", rx=2, seq=event.seq,
                         extra=' opacity="0.55"')
            )
            parts.append(
                svg.text(x, y - overlay_h / 2 - 3, f"相似度 {value:.2f}", size=7.5, fill=INK_SUB,
                         anchor="middle", seq=event.seq)
            )
            continue

        if event.event_type == "human.decision":
            action = str(event.get("action") or "")
            parts.append(
                svg.rect(x - 5, y - 9, 10, 18, fill="#fff3bf", stroke="#b8860b", rx=3, seq=event.seq)
            )
            parts.append(svg.text(x, y + 3.5, "H", size=8.5, fill="#8a6d00", anchor="middle",
                                  seq=event.seq))
            if action:
                parts.append(svg.text(x + 7, y - 1, action, size=7.5, fill=INK_FAINT, seq=event.seq))
            continue

        # 其余事件（闸门、预算、冲突、任务）用小方块标在编排器泳道上
        color = {
            "gate.evaluated": AGENT_COLORS["scheduler"],
            "budget.charged": "#adb5bd",
            "conflict.detected": AGENT_COLORS["A4"],
            "conflict.resolved": "#2b8a3e",
            "task.dispatched": AGENT_COLORS["A5"],
            "task.completed": "#2b8a3e",
        }.get(event.event_type, INK_FAINT)
        parts.append(svg.rect(x - 2.5, y - 2.5, 5, 5, fill=color, rx=1, seq=event.seq))

    # 时间轴刻度
    axis_y = TOP_PAD + len(LANES) * LANE_H + 4
    parts.append(svg.line(plot_x0, axis_y, plot_x1, axis_y, stroke=BORDER))
    tick_every = max(1, int(round(40 / step)))
    for seq in range(1, max_seq + 1, tick_every):
        parts.append(svg.line(x_of(seq), axis_y, x_of(seq), axis_y + 4, stroke=BORDER))
        parts.append(
            svg.text(x_of(seq), axis_y + 15, str(seq), size=8, fill=INK_FAINT, anchor="middle")
        )
    parts.append(
        svg.text(plot_x1, axis_y + 15, "事件序号 →", size=8.5, fill=INK_FAINT, anchor="end")
    )

    return "".join(parts), height
