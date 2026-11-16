"""七个视图的注册表：顺序即页面顺序，也即 docx 表 19 的编号。"""

from __future__ import annotations

from . import v1_timeline, v2_discussion, v3_conflict, v4_gate_budget, v5_dag_gantt, v6_coverage, v7_replay

VIEWS = (
    {
        "id": "V1",
        "module": v1_timeline,
        "title": "运行时间轴",
        "question": "现在跑到哪了、谁在说话",
    },
    {
        "id": "V2",
        "module": v2_discussion,
        "title": "讨论看板",
        "question": "三段（并行陈述 / 定向质询 / 投票）各自进行到哪",
    },
    {
        "id": "V3",
        "module": v3_conflict,
        "title": "冲突矩阵",
        "question": "哪些议题未裁决、谁与谁不一致",
    },
    {
        "id": "V4",
        "module": v4_gate_budget,
        "title": "闸门灯与预算条",
        "question": "能不能过、还剩几次调用",
    },
    {
        "id": "V5",
        "module": v5_dag_gantt,
        "title": "任务依赖图与甘特",
        "question": "任务谁先谁后、哪些并行、哪条链最紧",
    },
    {
        "id": "V6",
        "module": v6_coverage,
        "title": "覆盖热图",
        "question": "设计规格的哪条要求没被任务覆盖",
    },
    {
        "id": "V7",
        "module": v7_replay,
        "title": "回放控制",
        "question": "那一刻到底发生了什么",
    },
)

__all__ = ["VIEWS"]
