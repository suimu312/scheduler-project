"""SVG 原语：只做字符串拼装，不依赖任何前端框架与外部资源。

所有文本进入 SVG 前必须经过 esc()，否则事件里的用户文本会破坏文档结构。
"""

from __future__ import annotations

from typing import Iterable

ESC_MAP = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}


def esc(value: object) -> str:
    text = "" if value is None else str(value)
    for raw, safe in ESC_MAP.items():
        text = text.replace(raw, safe)
    return text


def char_width(ch: str, size: float) -> float:
    return size * (1.0 if ord(ch) > 127 else 0.55)


def text_width(text: str, size: float) -> float:
    return sum(char_width(ch, size) for ch in str(text))


def clip(text: str, size: float, max_width: float) -> str:
    """按估算字宽截断并补省略号，防止文字压出格子。"""
    text = str(text)
    if text_width(text, size) <= max_width:
        return text
    out = ""
    for ch in text:
        if text_width(out + ch + "…", size) > max_width:
            break
        out += ch
    return out + "…"


def attr(name: str, value: object) -> str:
    if value is None or value == "":
        return ""
    return f' {name}="{esc(value)}"'


def seq_attr(seq: int | None) -> str:
    """回放用：带 data-seq 的元素可以被进度条按截止序号隐藏。"""
    if not seq:
        return ""
    return f' data-seq="{int(seq)}"'


def svg_open(width: float, height: float, extra: str = "") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="100%" height="{height:.0f}" role="img"{extra}>'
    )


def group(children: Iterable[str], extra: str = "") -> str:
    return f"<g{extra}>" + "".join(children) + "</g>"


def rect(
    x: float,
    y: float,
    w: float,
    h: float,
    fill: str = "#ffffff",
    stroke: str | None = None,
    sw: float = 1,
    rx: float = 0,
    cls: str = "",
    seq: int | None = None,
    extra: str = "",
) -> str:
    parts = [f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0.5):.1f}" height="{max(h, 0.5):.1f}"']
    parts.append(f' fill="{fill}"')
    if stroke:
        parts.append(f' stroke="{stroke}" stroke-width="{sw}"')
    if rx:
        parts.append(f' rx="{rx}"')
    if cls:
        parts.append(f' class="{cls}"')
    parts.append(seq_attr(seq))
    parts.append(extra)
    parts.append("/>")
    return "".join(parts)


def line(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    stroke: str = "#c8ced8",
    sw: float = 1,
    dash: str = "",
    cls: str = "",
    seq: int | None = None,
) -> str:
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    cls_attr = f' class="{cls}"' if cls else ""
    return (
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
        f'stroke="{stroke}" stroke-width="{sw}"{dash_attr}{cls_attr}{seq_attr(seq)}/>'
    )


def circle(
    cx: float,
    cy: float,
    r: float,
    fill: str = "#2f6feb",
    stroke: str | None = None,
    sw: float = 1,
    cls: str = "",
    seq: int | None = None,
) -> str:
    stroke_attr = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
    cls_attr = f' class="{cls}"' if cls else ""
    return (
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{fill}"'
        f"{stroke_attr}{cls_attr}{seq_attr(seq)}/>"
    )


def text(
    x: float,
    y: float,
    content: object,
    size: float = 12,
    fill: str = "#1f2430",
    anchor: str = "start",
    weight: str = "normal",
    cls: str = "",
    seq: int | None = None,
    rotate: float | None = None,
) -> str:
    anchor_attr = f' text-anchor="{anchor}"' if anchor != "start" else ""
    weight_attr = f' font-weight="{weight}"' if weight != "normal" else ""
    cls_attr = f' class="{cls}"' if cls else ""
    rotate_attr = (
        f' transform="rotate({rotate:.1f} {x:.1f} {y:.1f})"' if rotate is not None else ""
    )
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}"'
        f"{anchor_attr}{weight_attr}{cls_attr}{rotate_attr}{seq_attr(seq)}>"
        f"{esc(content)}</text>"
    )


def path(d: str, stroke: str = "#c8ced8", sw: float = 1, fill: str = "none", cls: str = "", seq: int | None = None) -> str:
    cls_attr = f' class="{cls}"' if cls else ""
    return (
        f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"'
        f"{cls_attr}{seq_attr(seq)}/>"
    )


def arrow_marker(marker_id: str, color: str) -> str:
    return (
        f'<defs><marker id="{marker_id}" viewBox="0 0 10 10" refX="9" refY="5" '
        f'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{color}"/></marker></defs>'
    )


def hatch_pattern(pattern_id: str, color: str = "#d0d5dd") -> str:
    """空缺用留白 + 斜纹底表示，不用满屏红（docx §6.4 第二条）。"""
    return (
        f'<defs><pattern id="{pattern_id}" width="6" height="6" patternUnits="userSpaceOnUse" '
        f'patternTransform="rotate(45)">'
        f'<line x1="0" y1="0" x2="0" y2="6" stroke="{color}" stroke-width="1.2"/>'
        f"</pattern></defs>"
    )
