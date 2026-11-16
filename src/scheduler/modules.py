"""模块注册表：把 flow.yaml 的 owner 映射成 `run(ctx) -> StageResult`。

provider 两种：
- "mock"：走 mocks.py 的离线桩，用于状态机自测、演示与回归（不需要任何 LLM）；
- "real"：import 组员真实模块（src/agents、src/solution_gen、src/conflict_review、
  src/req_builder、src/baseline_mgr），要求它们各自暴露 `run(ctx)`（architecture §8.1）。
切换不改状态机一行代码。
"""

from __future__ import annotations

import importlib
from typing import Any, Callable

from . import mocks
from .context import MODULE_OWNERS, StageContext, StageResult

ModuleFn = Callable[[StageContext], StageResult]

MOCK_REGISTRY: dict[str, ModuleFn] = {
    "agents": mocks.run_agents,
    "solution_gen": mocks.run_solution_gen,
    "conflict_review": mocks.run_conflict_review,
    "req_builder": mocks.run_req_builder,
    "baseline_mgr": mocks.run_baseline_mgr,
}


class ModuleError(RuntimeError):
    pass


def _import_real(module_id: str) -> ModuleFn:
    last_error: Exception | None = None
    for name in (f"src.{module_id}", module_id):
        try:
            mod = importlib.import_module(name)
        except Exception as exc:  # 没写完 / 依赖没装
            last_error = exc
            continue
        fn = getattr(mod, "run", None)
        if callable(fn):
            return fn
        last_error = ModuleError(f"{name} 缺少 run(ctx) 入口")
    raise ModuleError(f"模块 {module_id} 不可用：{last_error}")


def resolve(module_id: str, provider: str = "mock") -> ModuleFn | None:
    if module_id == "scheduler":
        return None
    if provider == "mock":
        if module_id not in MOCK_REGISTRY:
            raise ModuleError(f"未知模块 {module_id}")
        return MOCK_REGISTRY[module_id]
    return _import_real(module_id)


def owner_of(phase: str) -> str:
    return MODULE_OWNERS.get(phase, "scheduler")


def available(provider: str = "mock") -> dict[str, Any]:
    """自检用：报告每个模块是否可解析。"""
    out: dict[str, Any] = {}
    for module_id in MOCK_REGISTRY:
        try:
            fn = resolve(module_id, provider)
            out[module_id] = getattr(fn, "__module__", "?")
        except Exception as exc:
            out[module_id] = f"ERROR: {exc}"
    return out
