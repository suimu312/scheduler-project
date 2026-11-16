"""配色与字号：集中一处，视图里不写死颜色。"""

from __future__ import annotations

FONT_STACK = "'Microsoft YaHei','PingFang SC','Noto Sans CJK SC','Source Han Sans SC',sans-serif"

BG = "#f5f6f8"
CARD = "#ffffff"
BORDER = "#dfe3ea"
GRID = "#eceff4"

INK = "#1f2430"
INK_SUB = "#5b6474"
INK_FAINT = "#8c95a6"

ACCENT = "#2f6feb"
ACCENT_SOFT = "#e8effd"
CRITICAL = "#d9480f"
CRITICAL_SOFT = "#fdece4"
OK = "#2b8a3e"
OK_SOFT = "#e6f4ea"
WARN = "#b8860b"
WARN_SOFT = "#fdf3dd"
GAP = "#ffffff"
GAP_HATCH = "#cfd5df"

AGENT_COLORS = {
    "scheduler": "#495057",
    "A1": "#2f6feb",
    "A2": "#7048e8",
    "A3": "#0b7285",
    "A4": "#c2255c",
    "A5": "#e8590c",
    "A6": "#2b8a3e",
}

STAGE_LABEL = {
    "P1": "P1 规格输入",
    "P2": "P2 模块划分",
    "P3": "P3 讨论裁决",
    "P4": "P4 三套方案",
    "P5": "P5 对抗评审",
    "P6": "P6 WBS 定稿",
    "P7": "P7 排期冻结",
}

PRIORITY_COLOR = {
    "must": "#c2255c",
    "should": "#2f6feb",
    "could": "#868e96",
    "wont": "#adb5bd",
}

LANES = ("scheduler", "A1", "A2", "A3", "A4", "A5", "A6")
LANE_LABEL = {
    "scheduler": "编排器",
    "A1": "A1 产品经理",
    "A2": "A2 用户代表",
    "A3": "A3 架构师",
    "A4": "A4 质量评审员",
    "A5": "A5 需求分析师",
    "A6": "A6 需求确认员",
}
