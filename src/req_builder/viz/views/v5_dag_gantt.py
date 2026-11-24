"""V5 任务依赖图与甘特同屏：左结构、右时间，共用同一份 schedule.json。

- 节点坐标按波次分层（列 = 波次，行 = 任务序）；
- 条的长度按工时，横轴为天；
- 关键路径用加粗描边标出，不允许为了"看起来均衡"挪动；
- 并行度超上限的波次在右上方标注"顺延 N 张卡"。

只读 schedule.json 与任务事件，不自己重算依赖 —— 否则图上的顺序会和引擎跑出来的不一样。
"""

from __future__ import annotations

import math
from typing import Any

from .. import svg
from ..theme import (
    ACCENT,
    ACCENT_SOFT,
    BORDER,
    CRITICAL,
    CRITICAL_SOFT,
    GRID,
    INK,
    INK_FAINT,
    INK_SUB,
    PRIORITY_COLOR,
)

TOP = 44
ROW_H = 30
NODE_W = 84
NODE_H = 22
WAVE_COL = 100
DAY_HEAD = 18


def _schedule_of(artifacts: dict[str, Any]) -> dict[str, Any]:
    raw = artifacts.get("schedule")
    if not raw:
        return {}
    return raw.get("data", raw) if isinstance(raw, dict) else {}


def _fallback_schedule(artifacts: dict[str, Any]) -> dict[str, Any]:
    """没读到 schedule.json 时给出兜底：按任务顺序排天，并在页面上说明这是兜底。"""
    tasks_raw = artifacts.get("dev_task_list")
    data = tasks_raw.get("data", tasks_raw) if isinstance(tasks_raw, dict) else {}
    tasks = data.get("tasks") or []
    day = 1
    rows = []
    for task in tasks:
        days = float(task.get("estimate_days") or 1)
        rows.append(
            {
                "task_id": task.get("task_id"),
                "title": task.get("title", ""),
                "priority": task.get("priority", "should"),
                "wave_no": int(day),
                "start_day": day,
                "end_day": day + days - 1,
                "critical": False,
                "on_path": False,
                "depends_on": task.get("depends_on", []),
            }
        )
        day += days
    return {"tasks": rows, "total_days": max(day - 1, 1), "fallback": True}


def _critical_ids(value: Any) -> list[str]:
    """兼容两种关键路径形状：A 的 {"task_ids": [...], "total_days": n} 与纯列表。"""
    if isinstance(value, dict):
        ids = value.get("task_ids") or value.get("tasks") or value.get("ids") or []
        return [str(item) for item in ids if item]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item]
    return []


def _total_days_of(schedule: dict[str, Any], rows: list[dict[str, Any]]) -> float:
    """总工期优先取 schedule.total_days；A 也可能把它塞在 critical_path.total_days 里。"""
    candidates: list[Any] = [schedule.get("total_days")]
    critical = schedule.get("critical_path")
    if isinstance(critical, dict):
        candidates.append(critical.get("total_days"))
    for value in candidates:
        try:
            if value is not None and float(value) > 0:
                return float(value)
        except (TypeError, ValueError):
            continue
    return float(max((float(r.get("end_day") or 1) for r in rows), default=1))


def _rows(schedule: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = [dict(t) for t in schedule.get("tasks") or [] if isinstance(t, dict)]
    if not tasks:
        return []
    tasks.sort(key=lambda t: (int(t.get("wave_no") or 1), str(t.get("task_id") or "")))
    return tasks


def render(window: ReplayWindow, artifacts: dict, width: float = 1180) -> tuple[str, float]:
    schedule = _schedule_of(artifacts) or _fallback_schedule(artifacts)
    rows = _rows(schedule)
    waves = sorted({int(r.get("wave_no") or 1) for r in rows}) or [1]
    total_days = max(1, int(math.ceil(float(schedule.get("total_days") or _total_days_of(schedule, rows)))))
    limit = int(schedule.get("max_wave_width") or schedule.get("parallel_limit") or 4)
    height = TOP + max(1, len(rows)) * ROW_H + 46

    if not rows:
        parts = [svg.text(8, 28, "未读到任务与排期数据", size=11, fill=INK_FAINT)]
        return "".join(parts), 60

    gx0 = 8 + len(waves) * WAVE_COL + 30
    day_w = max(10.0, (width - gx0 - 34) / total_days)
    gy0 = TOP + DAY_HEAD

    parts: list[str] = []
    parts.append(svg.text(8, 16, "任务依赖图（列 = 波次）", size=11.5, fill=INK, weight="bold"))
    parts.append(svg.text(gx0, 16, "甘特（横轴 = 天，条长 = 工时）", size=11.5, fill=INK, weight="bold"))
    if schedule.get("fallback"):
        parts.append(
            svg.text(gx0, 32, "未读到 schedule.json，当前为按任务顺序的兜底示意", size=8.5,
                     fill=INK_FAINT)
        )

    # 波次列标题
    for index, wave_no in enumerate(waves, start=0):
        x = 8 + index * WAVE_COL
        parts.append(
            svg.text(x + NODE_W / 2, TOP - 6, f"W{wave_no}", size=9.5, fill=INK_SUB,
                     anchor="middle", weight="bold")
        )

    # 天数网格与轴标
    for day in range(1, total_days + 1):
        x = gx0 + (day - 1) * day_w
        parts.append(svg.line(x, gy0 - 4, x, gy0 + len(rows) * ROW_H, stroke=GRID))
        parts.append(svg.text(x, gy0 - 8, f"D{day}", size=8, fill=INK_FAINT))

    nodes: dict[str, tuple[float, float]] = {}
    wave_of: dict[str, int] = {}
    for index, row in enumerate(rows):
        y = TOP + index * ROW_H
        wave_no = int(row.get("wave_no") or 1)
        wave_index = waves.index(wave_no) if wave_no in waves else 0
        node_x = 8 + wave_index * WAVE_COL
        node_y = y + (ROW_H - NODE_H) / 2
        task_id = str(row.get("task_id") or "")
        nodes[task_id] = (node_x, node_y)
        wave_of[task_id] = wave_no

        critical = bool(row.get("critical") or row.get("on_path"))
        fill = CRITICAL_SOFT if critical else ACCENT_SOFT
        stroke = CRITICAL if critical else ACCENT
        parts.append(
            svg.rect(node_x, node_y, NODE_W, NODE_H, fill=fill, stroke=stroke, sw=2 if critical else 1,
                     rx=5)
        )
        parts.append(
            svg.text(node_x + 6, node_y + 14, task_id, size=8.5, fill=INK, weight="bold")
        )
        priority = str(row.get("priority") or "should").lower()
        parts.append(
            svg.circle(node_x + NODE_W - 8, node_y + 8, 3, fill=PRIORITY_COLOR.get(priority, INK_FAINT))
        )

    # 依赖边
    for row in rows:
        child = str(row.get("task_id") or "")
        for dep in row.get("depends_on") or []:
            parent = str(dep)
            if parent not in nodes or child not in nodes:
                continue
            px, py = nodes[parent]
            cx, cy = nodes[child]
            x1 = px + NODE_W
            y1 = py + NODE_H / 2
            x2 = cx
            y2 = cy + NODE_H / 2
            if x2 <= x1:
                x2 = x1 + 12
            mid = (x1 + x2) / 2
            d = f"M {x1:.1f} {y1:.1f} C {mid:.1f} {y1:.1f} {mid:.1f} {y2:.1f} {x2:.1f} {y2:.1f}"
            parts.append(svg.path(d, stroke="#9aa3b2", sw=1.2))

    # 甘特条
    for index, row in enumerate(rows):
        y = gy0 + index * ROW_H
        start = float(row.get("start_day") or 1)
        days = float(row.get("estimate_days") or (float(row.get("end_day") or 1) - start + 1))
        bar_x = gx0 + (start - 1) * day_w
        bar_w = max(4.0, days * day_w - 2)
        critical = bool(row.get("critical") or row.get("on_path"))
        fill = CRITICAL_SOFT if critical else "#eff4ff"
        stroke = CRITICAL if critical else ACCENT
        parts.append(
            svg.rect(bar_x, y + 4, bar_w, ROW_H - 12, fill=fill, stroke=stroke,
                     sw=2.2 if critical else 1, rx=4)
        )
        label = f"{row.get('task_id')} {days:g}d"
        if bar_w > svg.text_width(label, 8.5) + 6:
            parts.append(svg.text(bar_x + 4, y + ROW_H - 9, label, size=8.5, fill=INK,
                                  weight="bold" if critical else "normal"))
        # 任务标题：紧跟条尾，超宽截断。只写任务号的话，换一份规格页面看起来是一样的。
        title = str(row.get("title") or "")
        if title:
            title_x = bar_x + bar_w + 6
            room = width - title_x - 8
            if room > 30:
                parts.append(
                    svg.text(title_x, y + ROW_H - 9, svg.clip(title, 8, room), size=8,
                             fill=INK_SUB if not critical else CRITICAL)
                )

    # 关键路径与并行度标注
    # A 的 schedule.json 里 critical_path 是对象 {"task_ids": [...], "total_days": 12.0}，
    # 而 E 自己的 demo 写的是任务号列表；两种形状都要认，否则会把字典的键当成任务号画出来。
    critical_path = _critical_ids(schedule.get("critical_path")) or [
        r.get("task_id") for r in rows if r.get("critical") or r.get("on_path")
    ]
    if critical_path:
        parts.append(
            svg.text(gx0, TOP + DAY_HEAD + len(rows) * ROW_H + 18,
                     f"关键路径：{' → '.join(str(t) for t in critical_path)}（总工期 {total_days} 天）",
                     size=9, fill=CRITICAL)
        )

    deferred: list[dict[str, Any]] = [d for d in schedule.get("deferred") or [] if isinstance(d, dict)]
    for wave in schedule.get("waves") or []:
        if not isinstance(wave, dict):
            continue
        count = int(wave.get("deferred_count") or 0)
        if not count:
            wave_no = int(wave.get("wave_no") or 1)
            count = sum(1 for d in deferred if int(d.get("wave_no") or wave_no) == wave_no)
        if count <= 0:
            continue
        wave_no = int(wave.get("wave_no") or 1)
        start = float(wave.get("start_day") or 1)
        x = gx0 + (start - 1) * day_w
        parts.append(svg.rect(x, TOP + 2, 96, 16, fill="#fdf3dd", stroke="#b8860b", rx=3))
        parts.append(
            svg.text(x + 6, TOP + 13, f"W{wave_no} 顺延 {count} 张卡", size=8.5, fill="#8a6d00")
        )

    parts.append(
        svg.text(8, height - 6,
                 f"并行度上限 {limit}；同一波内超出上限的任务按 Must→Should→Could 顺延并记录原因",
                 size=8.5, fill=INK_FAINT)
    )
    return "".join(parts), height
