"""F 模块：确认单、基线冻结、人工意见与变更请求。"""

from .manager import (
    apply_human_decision,
    build_confirmation_sheet,
    create_change_request,
    freeze_baseline,
)
from .stage import ModuleEvent, StageResult, make_artifact, run

__all__ = [
    "run",
    "StageResult",
    "ModuleEvent",
    "make_artifact",
    "build_confirmation_sheet",
    "freeze_baseline",
    "apply_human_decision",
    "create_change_request",
]
