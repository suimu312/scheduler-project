"""统一 LLM 调用入口与计数（architecture §7.1 / §12）。

一次调用 = 1，不论输入输出长度；模块不得自己记账，由 Gateway 计数后统一 charge。
默认 provider 是离线桩：不联网、不花钱，用于把状态机整体跑通与回归。

接真实模型只有一条路：`set_provider()` 注入（或在 `register_provider()` 里登记名字，
用 `--llm-provider <名字>` 选择）。模块侧的调用点固定为 `ctx.ask(agent_id, prompt)`，
**任何流程代码都不用改**——换模型只换注入的函数。

三种注入方式：
1. 进程内直接注入：`rt.gateway.set_provider(fn)`，fn(agent_id, prompt, stage) -> str；
2. 登记名字：`register_provider("my-llm", factory)`，CLI 传 `--llm-provider my-llm`；
3. 接组员现成的适配器：`make_provider("agents")` 用 B 的 `src.agents.providers.HttpModel`
   （读环境变量 `B_MODEL_ENDPOINT` / `B_MODEL_TOKEN`），由 `adapt_model()` 转换签名。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any, Callable, Protocol

# agent_id, prompt, stage -> 文本
Provider = Callable[[str, str, str], str]

# 名字 -> 工厂（工厂返回 Provider），供 CLI --llm-provider 选择
_PROVIDER_FACTORIES: dict[str, Callable[[], Provider]] = {}


def offline_provider(agent_id: str, prompt: str, stage: str) -> str:
    """确定性桩：同样的输入永远得到同样的输出，方便断点重放与单测。"""
    digest = hashlib.sha256(f"{stage}|{agent_id}|{prompt}".encode("utf-8")).hexdigest()[:8]
    return f"[offline:{agent_id}:{stage}:{digest}]"


def register_provider(name: str, factory: Callable[[], Provider]) -> None:
    """登记一个可命名选择的 provider；factory 每次创建 run 时调用一次。"""
    if not name:
        raise ValueError("provider 名字不能为空")
    _PROVIDER_FACTORIES[name] = factory


def known_providers() -> list[str]:
    return sorted({"offline", "agents", *_PROVIDER_FACTORIES})


def adapt_model(model: Any, *, system: str = "", timeout: int = 120) -> Provider:
    """把"收一个 dict、吐一个 dict"的模型适配成 Gateway 的 Provider 签名。

    兼容组员 B 的 `HttpModel(prompt_dict) -> {"response": {...}}`：
    入参组装为 `{agent_id, step, system, user}`，返回值依次尝试
    text / content / response / output，都没有就整体 JSON 序列化。
    """
    def _provider(agent_id: str, prompt: str, stage: str) -> str:
        payload = {"agent_id": agent_id, "step": stage, "system": system, "user": prompt}
        try:
            reply = model(payload, timeout=timeout)
        except TypeError:
            reply = model(payload)
        if isinstance(reply, str):
            return reply
        if isinstance(reply, dict):
            for key in ("text", "content", "response", "output"):
                value = reply.get(key)
                if isinstance(value, str):
                    return value
            return json.dumps(reply, ensure_ascii=False)
        raise TypeError(f"provider 返回值不支持的类型：{type(reply).__name__}")

    return _provider


def make_provider(name: str = "offline", **kwargs: Any) -> Provider:
    """按名字造一个 provider。未知名字直接报错，不静默退回桩。"""
    if name in ("offline", "mock", ""):
        return offline_provider
    factory = _PROVIDER_FACTORIES.get(name)
    if factory is not None:
        return factory()
    if name in ("agents", "agents-http", "http"):
        return _agents_http_provider(**kwargs)
    raise ValueError(f"未知的 llm_provider：{name}（可用：{', '.join(known_providers())}）")


def _agents_http_provider(**kwargs: Any) -> Provider:
    """接组员 B 的 HTTP 适配器；无端点时给出明确错误而不是空跑。"""
    from src.agents.providers import HttpModel  # 延迟导入：没装组员代码时也能跑离线

    endpoint = kwargs.get("endpoint") or os.environ.get("B_MODEL_ENDPOINT", "")
    if not endpoint:
        raise ValueError("使用 agents provider 需要设置环境变量 B_MODEL_ENDPOINT（或 --llm-endpoint）")
    model = HttpModel(endpoint, kwargs.get("token") or os.environ.get("B_MODEL_TOKEN"))
    return adapt_model(model, system=str(kwargs.get("system", "")), timeout=int(kwargs.get("timeout", 120)))


class LLMGateway:
    def __init__(self, provider: Provider | None = None, name: str = "offline") -> None:
        self._provider: Provider = provider or offline_provider
        self.name = name
        self._lock = threading.Lock()
        self.calls = 0
        self.by_stage: dict[str, int] = {}
        self.by_agent: dict[str, int] = {}
        # 观测回调：on_call(agent_id, stage, ok, latency_ms, error)；由 Runtime 接事件/span
        self.on_call: Callable[[str, str, bool, float, str | None], None] | None = None

    def set_provider(self, provider: Provider, name: str | None = None) -> None:
        self._provider = provider
        if name:
            self.name = name

    def _notify(self, agent_id: str, stage: str, ok: bool, latency_ms: float, error: str | None) -> None:
        if self.on_call is None:
            return
        try:
            self.on_call(agent_id, stage, ok, latency_ms, error)
        except Exception:
            # 观测出口失败不能反过来打断模型调用主链路
            pass

    def call(self, agent_id: str, prompt: str, stage: str) -> str:
        started = time.perf_counter()
        with self._lock:
            self.calls += 1
            self.by_stage[stage] = self.by_stage.get(stage, 0) + 1
            self.by_agent[agent_id] = self.by_agent.get(agent_id, 0) + 1
        try:
            reply = self._provider(agent_id, prompt, stage)
            self._notify(agent_id, stage, True, round((time.perf_counter() - started) * 1000, 2), None)
            return reply
        except TimeoutError:
            # 与 B 和修订版一致：超时记事件，不扣本次生成调用预算。
            with self._lock:
                self.calls -= 1
                self.by_stage[stage] -= 1
                self.by_agent[agent_id] -= 1
            self._notify(agent_id, stage, False, round((time.perf_counter() - started) * 1000, 2), "TimeoutError")
            raise
        except Exception as exc:
            self._notify(agent_id, stage, False, round((time.perf_counter() - started) * 1000, 2), f"{type(exc).__name__}: {exc}")
            raise

    def stage_calls(self, stage: str) -> int:
        return int(self.by_stage.get(stage, 0))

    def usage(self) -> dict[str, Any]:
        with self._lock:
            return {"calls": self.calls, "by_stage": dict(self.by_stage), "by_agent": dict(self.by_agent)}

    def reset(self) -> None:
        self.calls = 0
        self.by_stage.clear()
        self.by_agent.clear()
