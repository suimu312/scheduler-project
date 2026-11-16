"""可视化子包：只读事件流与产物，输出自包含 HTML，不回写任何运行状态。"""

from __future__ import annotations

from .events import ReplayWindow, load_events, stage_spans
from .render import render_to_file, render_views
from .views import VIEWS

__all__ = ["ReplayWindow", "load_events", "stage_spans", "render_views", "render_to_file", "VIEWS"]
