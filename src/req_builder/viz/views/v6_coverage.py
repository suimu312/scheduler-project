"""V6 覆盖热图：行是设计规格的来源，列是非 Wont 的任务，空缺留白。

回答"设计规格的哪条要求没被任务覆盖"。
按 docx §6.4 第二条，空缺用留白 + 斜纹表达而不是涂满红色：
满屏红会被无视，而覆盖缺口恰恰是最该被看见的东西。
"""

from __future__ import annotations

from typing import Any

from .. import svg
from ..theme import (
    ACCENT,
    BORDER,
    CRITICAL,
    GRID,
    INK,
    INK_FAINT,
    INK_SUB,
    OK_SOFT,
)

LABEL_W = 236
SUMMARY_W = 150
HEAD_H = 92
ROW_H = 24
MAX_COLW = 26


def _coverage_of(artifacts: dict[str, Any]) -> dict[str, Any]:
    # E 的产物名是 task_coverage；A 早期版本落的是 coverage_matrix，
    # 两者 data 形状一致（rows / columns / gap_count / coverage_rate），直接兼容。
    raw = artifacts.get("task_coverage") or artifacts.get("coverage_matrix")
    if not raw:
        return {}
    return raw.get("data", raw) if isinstance(raw, dict) else {}


def render(window: ReplayWindow, artifacts: dict, width: float = 1180) -> tuple[str, float]:
    coverage = _coverage_of(artifacts)
    rows = coverage.get("rows") or []
    columns = coverage.get("columns") or []
    gap_count = int(coverage.get("gap_count") or 0)
    rate = float(coverage.get("coverage_rate") or 0)

    if not rows:
        return (
            "".join([svg.text(8, 28, "未读到覆盖矩阵（task_coverage）", size=11, fill=INK_FAINT)]),
            60,
        )

    height = HEAD_H + len(rows) * ROW_H + 40
    grid_x = LABEL_W
    available = width - LABEL_W - SUMMARY_W - 16
    col_w = max(8.0, min(MAX_COLW, available / max(1, len(columns))))

    parts: list[str] = []
    parts.append(svg.text(8, 16, "覆盖热图：行 = 规格来源，列 = 非 Wont 任务", size=11.5, fill=INK,
                          weight="bold"))
    parts.append(svg.hatch_pattern("gapHatch"))
    parts.append(
        svg.text(8, 32,
                 "实心 = 有任务承接；留白斜纹 = 空缺（gap_count 即 A 判 G_P6_P7 的输入）",
                 size=8.5, fill=INK_FAINT)
    )

    # 列表头（任务号，竖排：列宽只有 26px 左右，横排会互相压住）
    for index, task_id in enumerate(columns):
        x = grid_x + index * col_w
        parts.append(
            svg.text(x + col_w / 2 + 2, HEAD_H - 8, str(task_id), size=7.5, fill=INK_SUB,
                     rotate=-90, anchor="start")
        )

    # 行
    for row_index, row in enumerate(rows):
        y = HEAD_H + row_index * ROW_H
        is_gap = str(row.get("status")) == "gap"
        band = "#ffffff" if row_index % 2 == 0 else "#fafbfc"
        parts.append(
            svg.rect(8, y, width - 16, ROW_H, fill=band if not is_gap else "#ffffff", stroke=GRID)
        )
        # 空缺行：左侧加一道虚线与文字，不用大面积红色
        if is_gap:
            parts.append(
                svg.rect(8, y, width - 16, ROW_H, fill="url(#gapHatch)", extra=' opacity="0.25"')
            )
            parts.append(svg.line(8, y, 8, y + ROW_H, stroke=CRITICAL, sw=2.5, dash="4,3"))
        else:
            parts.append(svg.line(8, y, 8, y + ROW_H, stroke=OK_SOFT, sw=2.5))

        source_id = str(row.get("source_id") or "")
        title = str(row.get("title") or "")
        kind = {
            "feature": "功能点",
            "constraint": "约束",
            "conflict": "冲突",
            "challenge": "质疑",
        }.get(str(row.get("source_type")), "来源")
        parts.append(svg.text(16, y + 15, f"{source_id}", size=8.5, fill=INK, weight="bold"))
        parts.append(svg.text(16 + svg.text_width(source_id, 8.5) + 6, y + 15,
                              svg.clip(f"{kind} {title}", 7.5, LABEL_W - 100), size=7.5,
                              fill=INK_FAINT))
        if is_gap:
            parts.append(
                svg.text(LABEL_W - 34, y + 15, "空缺", size=7.5, fill=CRITICAL, weight="bold")
            )

        covered = set(row.get("covered_by") or [])
        for index, task_id in enumerate(columns):
            x = grid_x + index * col_w
            if task_id in covered:
                parts.append(
                    svg.rect(x + 2, y + 4, col_w - 4, ROW_H - 8, fill=ACCENT, rx=2,
                             extra=f' opacity="{0.9 if not is_gap else 0.75}"')
                )
            else:
                parts.append(
                    svg.line(x + col_w / 2, y + 4, x + col_w / 2, y + ROW_H - 4,
                             stroke="#eef1f5", sw=1)
                )

    # 右侧汇总
    sx = width - SUMMARY_W + 8
    parts.append(svg.rect(sx, HEAD_H, SUMMARY_W - 16, 64, fill="#f8f9fb", stroke=BORDER, rx=6))
    parts.append(svg.text(sx + 10, HEAD_H + 20, f"覆盖率 {rate * 100:.1f}%", size=11, fill=INK,
                          weight="bold"))
    parts.append(
        svg.text(sx + 10, HEAD_H + 38, f"空缺 {gap_count} 条", size=9,
                 fill=CRITICAL if gap_count else INK_SUB)
    )
    parts.append(
        svg.text(sx + 10, HEAD_H + 54, f"来源 {len(rows)} · 任务 {len(columns)}", size=8.5,
                 fill=INK_FAINT)
    )

    parts.append(
        svg.text(8, height - 6, "覆盖率 = 0.6 × 来源加权覆盖率 + 0.4 × 任务完备率；Wont 不进分母",
                 size=8.5, fill=INK_FAINT)
    )
    return "".join(parts), height
