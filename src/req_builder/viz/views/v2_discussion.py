"""V2 讨论看板：三栏并列，回答"三段各自进行到哪"。

三栏固定为并行陈述 / 定向质询 / 投票，不按轮次猜，只按事件类型分栏：
第一段互不可见，第二段一问一答，第三段票型 + 理由。
"""

from __future__ import annotations

from .. import svg
from ..events import ReplayWindow, discussion_phases
from ..theme import AGENT_COLORS, BORDER, INK, INK_FAINT, INK_SUB

HEAD_H = 30
CARD_H = 40
GAP = 6
PAD = 6


def _column(
    x: float,
    width: float,
    title: str,
    hint: str,
    cards: list[str],
    count: int,
    empty_text: str,
) -> tuple[list[str], float]:
    """cards 中的每个片段以 (PAD, 0) 为左上角绘制，这里统一加纵向偏移。"""
    parts: list[str] = []
    parts.append(svg.text(x, HEAD_H - 8, title, size=12, fill=INK, weight="bold"))
    parts.append(svg.text(x + svg.text_width(title, 12) + 8, HEAD_H - 8, hint, size=9, fill=INK_FAINT))
    parts.append(svg.rect(x, HEAD_H + 2, width, 24, fill="#f3f5f8", stroke=BORDER))
    parts.append(svg.text(x + 8, HEAD_H + 18, f"{count} 条", size=9.5, fill=INK_SUB, weight="bold"))

    y = HEAD_H + 32
    if not cards:
        parts.append(svg.rect(x, y, width, CARD_H, fill="#ffffff", stroke=BORDER, rx=4))
        parts.append(svg.text(x + 10, y + CARD_H / 2 + 4, empty_text, size=9.5, fill=INK_FAINT))
        return parts, y + CARD_H + GAP

    for index, card in enumerate(cards):
        offset = y + index * (CARD_H + GAP)
        parts.append(
            svg.group([card], extra=f' transform="translate({x:.1f},{offset:.1f})"')
        )
    return parts, y + len(cards) * (CARD_H + GAP)


def render(window: ReplayWindow, artifacts: dict, width: float = 1180) -> tuple[str, float]:
    # 三段式只发生在 P3，因此只看 P3 的事件；P5 的五维打分看 V1/V3
    phases = discussion_phases([e for e in window.events if e.stage == "P3"])
    col_gap = 14
    col_w = (width - 2 * PAD - 2 * col_gap) / 3

    # 第一栏：并行陈述（互不可见）
    statement_cards: list[str] = []
    for event in phases["statements"]:
        agent = str(event.actor)
        color = AGENT_COLORS.get(agent, INK_SUB)
        text = str(event.get("stance") or event.get("position") or event.get("text") or "")
        words = int(event.get("word_count") or len(text))
        statement_cards.append(
            "".join(
                [
                    svg.rect(PAD, 0, 3, CARD_H, fill=color),
                    svg.text(PAD + 10, 16, f"{agent} · {event.get('stage') or ''}", size=9.5,
                             fill=color, weight="bold"),
                    svg.text(PAD + col_w - 8, 16, f"{words} 字", size=8.5, fill=INK_FAINT,
                             anchor="end"),
                    svg.text(PAD + 10, 31, svg.clip(text, 9.5, col_w - 30), size=9.5, fill=INK),
                ]
            )
        )

    # 第二栏：定向质询（一问一答，须引原话）
    query_cards: list[str] = []
    for event in phases["queries"]:
        src = str(event.actor)
        dst = str(event.get("to") or event.get("to_agent") or "?")
        quote = str(event.get("quote") or event.get("citation") or "")
        question = str(event.get("question") or event.get("text") or "")
        query_cards.append(
            "".join(
                [
                    svg.line(PAD + 3, 4, PAD + 3, CARD_H - 4,
                             stroke=AGENT_COLORS.get(dst, INK_FAINT), sw=2),
                    svg.text(PAD + 10, 15, f"{src} → {dst}", size=9.5,
                             fill=AGENT_COLORS.get(src, INK_SUB), weight="bold"),
                    svg.text(PAD + 10, 27, svg.clip(question or quote, 9, col_w - 12), size=9,
                             fill=INK),
                    svg.text(PAD + 10, 38, "已引原话" if quote else "未引原话", size=8,
                             fill=INK_FAINT),
                ]
            )
        )

    # 第三栏：逐条投票（票型固定、理由强制）
    vote_cards: list[str] = []
    for event in phases["votes"]:
        agent = str(event.actor)
        vote = str(event.get("vote") or event.get("ballot") or "?").upper()
        reason = str(event.get("reason") or "")
        vote_cards.append(
            "".join(
                [
                    svg.rect(PAD + 4, 8, 26, 18, fill="#ffffff",
                             stroke=AGENT_COLORS.get(agent, BORDER), rx=4),
                    svg.text(PAD + 17, 21, vote[:1], size=11, fill=INK, anchor="middle",
                             weight="bold"),
                    svg.text(PAD + 38, 16, agent, size=9.5, fill=INK_SUB, weight="bold"),
                    svg.text(PAD + 38, 30, svg.clip(reason, 8.5, col_w - 50), size=8.5,
                             fill=INK_FAINT),
                ]
            )
        )

    x1 = PAD
    x2 = PAD + col_w + col_gap
    x3 = PAD + 2 * (col_w + col_gap)

    body1, y1 = _column(x1, col_w, "① 并行陈述", "互不可见", statement_cards,
                        len(statement_cards), "本阶段无陈述")
    body2, y2 = _column(x2, col_w, "② 定向质询", "一问一答，须引原话", query_cards,
                        len(query_cards), "本阶段无质询")
    body3, y3 = _column(x3, col_w, "③ 逐条投票", "票型固定、理由强制", vote_cards,
                        len(vote_cards), "本阶段无投票")

    parts = body1 + body2 + body3
    return "".join(parts), max(y1, y2, y3) + 8
