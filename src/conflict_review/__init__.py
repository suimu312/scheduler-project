# -*- coding: utf-8 -*-
"""conflict_review：D 模块（P3 冲突裁决 + P5 对抗式评审）。

对外只暴露一个入口，A 的 modules.resolve 靠它找到本模块：

    run(ctx: StageContext) -> StageResult

内部结构：
    contracts.py  信封适配层（StageContext / StageResult / 产物 meta 壳）
    models.py     数据结构（Stance / Conflict / Challenge / ScoreSheet）与字段归一
    rules.py      纯函数规则（打分、四要素校验、否决项、冲突扫描、三级裁决、回炉分流）
    stage.py      把规则装进 run(ctx)，P3 / P5 各一条分支

约定：本模块不调模型，budget_used 恒为 0；落盘与事件序号由 A 统一负责。
"""

from .contracts import (
    PRODUCER,
    SCHEMA_VERSION,
    ErrorCode,
    GuardSignal,
    RunEvent,
    StageContext,
    StageResult,
    make_artifact,
    make_error,
    make_event,
)
from .models import (
    DIM_SCORER,
    Challenge,
    Conflict,
    ScoreSheet,
    Stance,
    canon_dim,
    canon_position,
)
from .rules import (
    DIM_WEIGHTS,
    PASS_TOTAL,
    STD_LIMIT,
    VOTE_WEIGHT,
    adjudicate,
    canon_scores,
    challenges_from_findings,
    detect_conflicts,
    find_veto_items,
    gate,
    oversize_items_from_rows,
    review,
    review_scope,
    route_rework,
    score_from_artifacts,
    stddev,
    triage_challenges,
    validate_challenge,
    weighted_total,
)
from .stage import run, run_p3, run_p5

__all__ = [
    # A 的入口
    "run",
    "run_p3",
    "run_p5",
    # 信封
    "StageContext",
    "StageResult",
    "RunEvent",
    "GuardSignal",
    "make_artifact",
    "make_event",
    "make_error",
    "ErrorCode",
    "SCHEMA_VERSION",
    "PRODUCER",
    # 数据
    "Stance",
    "Conflict",
    "Challenge",
    "ScoreSheet",
    "canon_dim",
    "canon_position",
    # 规则
    "detect_conflicts",
    "adjudicate",
    "validate_challenge",
    "triage_challenges",
    "find_veto_items",
    "canon_scores",
    "weighted_total",
    "stddev",
    "gate",
    "route_rework",
    "review",
    # 评审依据（P5 从产物算分）
    "review_scope",
    "oversize_items_from_rows",
    "score_from_artifacts",
    "challenges_from_findings",
    "DIM_SCORER",
    "DIM_WEIGHTS",
    "VOTE_WEIGHT",
    "PASS_TOTAL",
    "STD_LIMIT",
]

__version__ = "1.1.0"
