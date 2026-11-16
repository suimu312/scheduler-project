"""B 模块公共接口；不调用其他模块、不写运行目录。"""
from .core import (
    AgentError, build_convocation, estimate_similarity, evaluate_user_value,
    load_config, render_prompt, run_discussion, validate_config,
)

__all__ = [
    "AgentError", "build_convocation", "estimate_similarity", "evaluate_user_value",
    "load_config", "render_prompt", "run_discussion", "validate_config",
]
