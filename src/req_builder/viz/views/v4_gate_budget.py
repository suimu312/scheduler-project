"""V4 闸门灯与预算条：常驻顶部，回答"能不能过、还剩几次调用"。"""

from __future__ import annotations

from .. import svg
from ..events import ReplayWindow, budget_series, gate_states
from ..theme import (
    ACCENT,
    ACCENT_SOFT,
    BORDER,
    CRITICAL,
    CRITICAL_SOFT,
    INK,
    INK_FAINT,
    INK_SUB,
    OK,
    OK_SOFT,
)

GATE_ORDER = (
    ("G1", "G_P1_P2"),
    ("G2", "G_P2_P3"),
    ("G3", "G_P3_P4"),
    ("G4", "G_P4_P5"),
    ("G5", "G_P5_P6"),
    ("G6", "G_P6_P7"),
    ("G7", "G_P7_BASELINE"),
)

HEIGHT = 86


def render(window: ReplayWindow, artifacts: dict, width: float = 1180) -> tuple[str, float]:
    gates = gate_states(window.events)
    series = budget_series(window.events)
    used, limit = (series[-1]["used"], series[-1]["limit"]) if series else (0, 30)
    limit = max(limit, 1)
    used_ratio = min(1.0, used / limit)

    parts: list[str] = []
    parts.append(svg.text(20, 26, "闸门判定", size=13, fill=INK_SUB, weight="bold"))

    x = 100
    for short, gate_id in GATE_ORDER:
        state = gates.get(gate_id)
        if state is None:
            fill, stroke, dot_fill, label_fill = "#f1f3f6", BORDER, INK_FAINT, INK_SUB
            action = "未判"
        elif state["pass"]:
            fill, stroke, dot_fill, label_fill = OK_SOFT, OK, OK, OK
            action = "通过"
        else:
            fill, stroke, dot_fill, label_fill = CRITICAL_SOFT, CRITICAL, CRITICAL, CRITICAL
            action = state["action"] or "未过"
        seq = state["seq"] if state else None
        parts.append(svg.rect(x, 12, 96, 44, fill=fill, stroke=stroke, sw=1, rx=8, seq=seq))
        parts.append(svg.circle(x + 14, 34, 5, fill=dot_fill))
        parts.append(svg.text(x + 26, 38, short, size=13, fill=label_fill, weight="bold", seq=seq))
        parts.append(
            svg.text(x + 14, 52, svg.clip(action, 9, 76), size=9.5, fill=INK_SUB, seq=seq)
        )
        x += 106

    # 预算条
    bar_x = x + 30
    bar_w = max(160, width - bar_x - 92)
    parts.append(svg.text(bar_x, 26, "模型调用预算", size=13, fill=INK_SUB, weight="bold"))
    parts.append(svg.rect(bar_x, 34, bar_w, 16, fill="#eef1f5", stroke=BORDER, rx=8))
    if used_ratio > 0:
        parts.append(
            svg.rect(
                bar_x,
                34,
                bar_w * used_ratio,
                16,
                fill=ACCENT if used_ratio < 0.85 else CRITICAL,
                rx=8,
                seq=series[-1]["seq"] if series else None,
            )
        )
    parts.append(
        svg.text(
            bar_x + bar_w + 8,
            47,
            f"{used}/{limit}",
            size=12,
            fill=INK,
            weight="bold",
        )
    )
    parts.append(
        svg.text(
            bar_x,
            70,
            "闸门为纯函数判定，不计费；只有模型调用计入预算",
            size=9.5,
            fill=INK_FAINT,
        )
    )

    body = "".join(parts)
    return body, HEIGHT
