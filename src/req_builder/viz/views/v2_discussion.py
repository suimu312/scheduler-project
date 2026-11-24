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


def _artifact_data(artifacts: dict, name: str) -> dict[str, Any]:
    raw = artifacts.get(name)
    if not isinstance(raw, dict):
        return {}
    payload = raw.get("data", raw)
    return payload if isinstance(payload, dict) else {}


def _statement_cards(rows: list[dict[str, str]], col_w: float) -> list[str]:
    cards: list[str] = []
    for row in rows:
        agent = row["agent"]
        color = AGENT_COLORS.get(agent, INK_SUB)
        cards.append(
            "".join(
                [
                    svg.rect(PAD, 0, 3, CARD_H, fill=color),
                    svg.text(PAD + 10, 16, f"{agent} · {row['tag']}", size=9.5,
                             fill=color, weight="bold"),
                    svg.text(PAD + col_w - 8, 16, f"{row['words']} 字", size=8.5, fill=INK_FAINT,
                             anchor="end"),
                    svg.text(PAD + 10, 31, svg.clip(row["text"], 9.5, col_w - 30), size=9.5, fill=INK),
                ]
            )
        )
    return cards


def _query_cards(rows: list[dict[str, str]], col_w: float) -> list[str]:
    cards: list[str] = []
    for row in rows:
        src, dst = row["agent"], row["to"]
        cards.append(
            "".join(
                [
                    svg.line(PAD + 3, 4, PAD + 3, CARD_H - 4,
                             stroke=AGENT_COLORS.get(dst, INK_FAINT), sw=2),
                    svg.text(PAD + 10, 15, f"{src} → {dst}", size=9.5,
                             fill=AGENT_COLORS.get(src, INK_SUB), weight="bold"),
                    svg.text(PAD + 10, 27, svg.clip(row["text"], 9, col_w - 12), size=9,
                             fill=INK),
                    svg.text(PAD + 10, 38, "已引原话" if row["cited"] == "1" else "未引原话", size=8,
                             fill=INK_FAINT),
                ]
            )
        )
    return cards


def _vote_cards(rows: list[dict[str, str]], col_w: float) -> list[str]:
    cards: list[str] = []
    for row in rows:
        agent = row["agent"]
        cards.append(
            "".join(
                [
                    svg.rect(PAD + 4, 8, 26, 18, fill="#ffffff",
                             stroke=AGENT_COLORS.get(agent, BORDER), rx=4),
                    svg.text(PAD + 17, 21, row["vote"][:1], size=11, fill=INK, anchor="middle",
                             weight="bold"),
                    svg.text(PAD + 38, 16, agent, size=9.5, fill=INK_SUB, weight="bold"),
                    svg.text(PAD + 38, 30, svg.clip(row["reason"], 8.5, col_w - 50), size=8.5,
                             fill=INK_FAINT),
                ]
            )
        )
    return cards


def render(window: ReplayWindow, artifacts: dict, width: float = 1180) -> tuple[str, float]:
    # 三段式只发生在 P3，因此只看 P3 的事件；P5 的五维打分看 V1/V3
    phases = discussion_phases([e for e in window.events if e.stage == "P3"])
    minutes = _artifact_data(artifacts, "discussion_minutes")
    col_gap = 14
    col_w = (width - 2 * PAD - 2 * col_gap) / 3
    fell_back = False

    # 第一栏：并行陈述（互不可见）
    statement_rows = [
        {
            "agent": str(event.actor),
            "text": str(event.get("stance") or event.get("position") or event.get("text") or ""),
            "words": str(int(event.get("word_count") or len(str(event.get("text") or "")))),
            "tag": str(event.get("stage") or ""),
        }
        for event in phases["statements"]
    ]
    artifact_statements = [
        {
            "agent": str(item.get("agent_id") or item.get("agent") or "?"),
            "text": str(item.get("quote") or item.get("text") or ""),
            "words": str(len(str(item.get("quote") or item.get("text") or ""))),
            "tag": str(item.get("topic_id") or "P3"),
        }
        for item in (minutes.get("statements") or [])
        if isinstance(item, dict)
    ]
    # A 的运行只在 P3 落 1 条 agent.statement（无 agent.query / agent.vote），
    # 讨论内容在 discussion_minutes 产物里是全的，事件比产物少时以产物为准。
    if len(artifact_statements) > len(statement_rows):
        statement_rows = artifact_statements
        fell_back = True

    # 第二栏：定向质询（一问一答，须引原话）
    query_rows = [
        {
            "agent": str(event.actor),
            "to": str(event.get("to") or event.get("to_agent") or "?"),
            "text": str(event.get("question") or event.get("text") or ""),
            "cited": "1" if (event.get("quote") or event.get("citation")) else "0",
        }
        for event in phases["queries"]
    ]
    artifact_queries = [
        {
            "agent": str(pair.get("asker") or "?"),
            "to": str(pair.get("responder") or "?"),
            "text": str(pair.get("answer") or pair.get("question") or ""),
            "cited": "1" if (pair.get("quote_ref") or pair.get("quote")) else "0",
        }
        for pair in (minutes.get("query_pairs") or [])
        if isinstance(pair, dict)
    ]
    if len(artifact_queries) > len(query_rows):
        query_rows = artifact_queries
        fell_back = True

    # 第三栏：逐条投票（票型固定、理由强制）
    vote_rows = [
        {
            "agent": str(event.actor),
            "vote": str(event.get("vote") or event.get("ballot") or "?").upper(),
            "reason": str(event.get("reason") or ""),
        }
        for event in phases["votes"]
    ]
    artifact_votes: list[dict[str, str]] = []
    for group in minutes.get("votes") or []:
        if not isinstance(group, dict):
            continue
        ballots = [v for v in (group.get("votes") or []) if isinstance(v, dict)]
        accept = sum(1 for v in ballots if str(v.get("value") or "").lower().startswith("accept"))
        artifact_votes.append(
            {
                "agent": str(group.get("conflict_id") or "?"),
                "vote": f"{accept}/{len(ballots)}" if ballots else "0/0",
                "reason": " · ".join(
                    f"{v.get('agent_id') or '?'}{'✓' if str(v.get('value') or '').lower().startswith('accept') else '✗'}"
                    for v in ballots
                ),
            }
        )
    if len(artifact_votes) > len(vote_rows):
        vote_rows = artifact_votes
        fell_back = True

    statement_cards = _statement_cards(statement_rows, col_w)
    query_cards = _query_cards(query_rows, col_w)
    vote_cards = _vote_cards(vote_rows, col_w)

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
    bottom = max(y1, y2, y3)
    if fell_back:
        note = "数据源：discussion_minutes.json（本次运行 P3 只落了少量事件，计数以产物为准）"
        parts.append(svg.text(PAD, bottom + 12, note, size=9, fill=INK_FAINT))
        bottom += 18
    return "".join(parts), bottom + 8
