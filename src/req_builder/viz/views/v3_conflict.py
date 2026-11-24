"""V3 冲突矩阵：行是议题，列是角色，格是立场与证据标记。

回答"哪些议题未裁决、谁与谁不一致"。
关键细节（docx §6.4 第一条）：有证据的格子右上角点一个小圆点，
否则人看不出"为什么这么判"。

列宽按传入宽度自适应，因此既能独占一行，也能在双栏布局里显示。
"""

from __future__ import annotations

from typing import Any

from .. import svg
from ..events import ReplayWindow
from ..theme import (
    ACCENT,
    AGENT_COLORS,
    BORDER,
    CRITICAL,
    GRID,
    INK,
    INK_FAINT,
    INK_SUB,
    OK,
    OK_SOFT,
    WARN,
    WARN_SOFT,
)

ROLES = ("A1", "A2", "A3", "A4")
ROW_H = 46
HEAD_H = 52


def _collect(window: ReplayWindow) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """把 conflict.detected 折成议题行，conflict.resolved 补裁决列。"""
    rows: list[dict[str, Any]] = []
    index: dict[str, int] = {}
    for event in window.events:
        if event.event_type != "conflict.detected":
            continue
        cid = str(event.get("conflict_id") or f"CFL-{len(rows) + 1:03d}")
        stances = event.get("stances")
        if not isinstance(stances, list):
            stances = []
            for role in ROLES:
                position = event.get(f"{role}_position") or event.get(role)
                if position:
                    stances.append({"agent_id": role, "position": position})
        if cid in index:
            row = rows[index[cid]]
        else:
            row = {
                "conflict_id": cid,
                "topic": str(event.get("topic_id") or event.get("topic") or ""),
                "level": str(event.get("level") or ""),
                "seq": event.seq,
                "stances": [],
                "resolution": None,
            }
            rows.append(row)
            index[cid] = len(rows) - 1
        existing = {str(s.get("agent_id") or "") for s in row["stances"]}
        for stance in stances:
            if isinstance(stance, dict) and str(stance.get("agent_id") or "") not in existing:
                row["stances"].append(stance)

    for event in window.events:
        if event.event_type != "conflict.resolved":
            continue
        cid = str(event.get("conflict_id") or "")
        if cid not in index:
            continue
        resolution = event.get("resolution")
        if not isinstance(resolution, dict):
            resolution = {
                "type": str(event.get("resolution_type") or event.get("type") or ""),
                "winner": str(event.get("winner") or ""),
                "reason": str(event.get("reason") or ""),
            }
        rows[index[cid]]["resolution"] = {**resolution, "seq": event.seq}
    return rows, index


def _from_artifact(artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    """事件里只留了 conflict_register:CFL-001 这种引用，议题内容要从产物里取。

    没有这一步，页面上只会出现一列「未命名议题 · 未分级」。
    """
    raw = artifacts.get("conflict_register")
    data = raw.get("data", raw) if isinstance(raw, dict) else {}
    rows: list[dict[str, Any]] = []
    for conflict in data.get("conflicts") or []:
        if not isinstance(conflict, dict):
            continue
        rows.append(
            {
                "conflict_id": str(conflict.get("conflict_id") or ""),
                "topic": str(conflict.get("topic_id") or conflict.get("topic") or ""),
                "level": str(conflict.get("level") or ""),
                "seq": 0,
                "stances": [s for s in (conflict.get("stances") or []) if isinstance(s, dict)],
                "resolution": conflict.get("resolution"),
                "status": str(conflict.get("status") or ""),
            }
        )
    return rows


def render(window: ReplayWindow, artifacts: dict, width: float = 1180) -> tuple[str, float]:
    rows, _index = _collect(window)
    if not rows or not any(row.get("stances") for row in rows):
        from_art = _from_artifact(artifacts)
        if from_art:
            rows = from_art
    height = HEAD_H + max(1, len(rows)) * ROW_H + 20

    margin = 8
    topic_w = max(120, width * 0.26)
    verdict_w = max(120, width * 0.24)
    role_w = max(50, (width - 2 * margin - topic_w - verdict_w) / len(ROLES))

    x_topic = margin
    x_roles = x_topic + topic_w
    x_verdict = x_roles + len(ROLES) * role_w

    parts: list[str] = []
    parts.append(
        svg.rect(x_topic, 6, x_verdict + verdict_w - x_topic, 34, fill="#f3f5f8", stroke=BORDER)
    )
    parts.append(
        svg.text(x_topic + 8, 27, "议题（行）/ 角色（列）", size=9.5, fill=INK_SUB, weight="bold")
    )
    for idx, role in enumerate(ROLES):
        x = x_roles + idx * role_w
        parts.append(svg.line(x, 6, x, 6 + 34 + len(rows) * ROW_H, stroke=GRID))
        parts.append(
            svg.text(x + role_w / 2, 27, role, size=10.5, fill=AGENT_COLORS.get(role, INK),
                     anchor="middle", weight="bold")
        )
    parts.append(svg.line(x_verdict, 6, x_verdict, 6 + 34 + len(rows) * ROW_H, stroke=GRID))
    parts.append(
        svg.text(x_verdict + verdict_w / 2, 27, "裁决", size=10.5, fill=INK_SUB,
                 anchor="middle", weight="bold")
    )

    if not rows:
        parts.append(
            svg.rect(x_topic, 46, x_verdict + verdict_w - x_topic, 60, fill="#ffffff", stroke=BORDER)
        )
        parts.append(
            svg.text(x_topic + 12, 80, "本次运行未登记冲突（冲突扫描无输出）", size=10,
                     fill=INK_FAINT)
        )
        return "".join(parts), height

    for row_idx, row in enumerate(rows):
        y = 40 + row_idx * ROW_H
        band = "#ffffff" if row_idx % 2 == 0 else "#fafbfc"
        parts.append(
            svg.rect(x_topic, y, x_verdict + verdict_w - x_topic, ROW_H, fill=band, stroke=GRID)
        )
        topic = svg.clip(row["topic"] or "未命名议题", 9, topic_w - 18)
        parts.append(svg.text(x_topic + 8, y + 18, topic, size=9, fill=INK, weight="bold"))
        parts.append(
            svg.text(x_topic + 8, y + 34, f"{row['conflict_id']} · {row['level'] or '未分级'}",
                     size=8, fill=INK_FAINT)
        )

        stance_map = {str(s.get("agent_id") or ""): s for s in row["stances"] if isinstance(s, dict)}
        for idx, role in enumerate(ROLES):
            x = x_roles + idx * role_w
            stance = stance_map.get(role)
            if not stance:
                parts.append(
                    svg.text(x + role_w / 2, y + ROW_H / 2 + 4, "·", size=12, fill=INK_FAINT,
                             anchor="middle")
                )
                continue
            position = str(stance.get("position") or stance.get("quote") or "")
            evidence = stance.get("evidence_refs") or stance.get("evidence") or []
            parts.append(
                svg.rect(x + 3, y + 6, role_w - 6, ROW_H - 12, fill="#ffffff", stroke=BORDER, rx=3)
            )
            parts.append(
                svg.text(x + 7, y + 21, svg.clip(position, 8, role_w - 18), size=8, fill=INK)
            )
            if evidence:
                # 有证据必须可见：右上角小圆点
                parts.append(svg.circle(x + role_w - 9, y + 12, 3, fill=ACCENT))
                count = len(evidence) if isinstance(evidence, list) else 1
                parts.append(svg.text(x + 7, y + 33, f"证据 {count} 条", size=7.2, fill=ACCENT))
            else:
                parts.append(svg.text(x + 7, y + 33, "仅主张", size=7.2, fill=INK_FAINT))

        resolution = row["resolution"]
        if resolution:
            winner = str(resolution.get("winner") or "")
            kind = str(resolution.get("type") or "")
            fill = OK_SOFT if winner else WARN_SOFT
            stroke = OK if winner else WARN
            parts.append(
                svg.rect(x_verdict + 4, y + 6, verdict_w - 8, ROW_H - 12, fill=fill, stroke=stroke,
                         rx=3)
            )
            parts.append(
                svg.text(x_verdict + 9, y + 21, f"{kind or 'resolved'} · {winner or '无胜方'}",
                         size=8.5, fill=stroke, weight="bold")
            )
            reason = str(resolution.get("reason") or "")
            if reason:
                parts.append(
                    svg.text(x_verdict + 9, y + 34, svg.clip(reason, 7.5, verdict_w - 20), size=7.5,
                             fill=INK_SUB)
                )
        else:
            parts.append(
                svg.rect(x_verdict + 4, y + 6, verdict_w - 8, ROW_H - 12, fill="#ffffff",
                         stroke=CRITICAL, rx=3, extra=' stroke-dasharray="4,2"')
            )
            parts.append(
                svg.text(x_verdict + 9, y + 25, "未裁决", size=9, fill=CRITICAL, weight="bold")
            )

    parts.append(
        svg.text(margin, height - 4, "● 蓝点为有证据支撑的立场；虚线框为未裁决议题", size=8,
                 fill=INK_FAINT)
    )
    return "".join(parts), height
