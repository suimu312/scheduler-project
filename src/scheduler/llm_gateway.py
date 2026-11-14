"""统一 LLM 调用入口与计数（architecture §7.1 / §12）。

一次调用 = 1，不论输入输出长度；模块不得自己记账，由 Gateway 计数后 A 统一 charge。
默认 provider 是离线桩：不联网、不花钱，用于把状态机整体跑通与回归；
接真实模型时调用 set_provider() 注入即可，计数口径不变。
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Protocol


class Provider(Protocol):
    def __call__(self, agent_id: str, prompt: str, stage: str) -> str: ...


def offline_provider(agent_id: str, prompt: str, stage: str) -> str:
    """确定性桩：同样的输入永远得到同样的输出，方便断点重放与单测。"""
    digest = hashlib.sha256(f"{stage}|{agent_id}|{prompt}".encode("utf-8")).hexdigest()[:8]
    return f"[offline:{agent_id}:{stage}:{digest}]"


class LLMGateway:
    def __init__(self, provider: Provider | None = None) -> None:
        self._provider: Provider = provider or offline_provider
        self.calls = 0
        self.by_stage: dict[str, int] = {}
        self.by_agent: dict[str, int] = {}

    def set_provider(self, provider: Provider) -> None:
        self._provider = provider

    def call(self, agent_id: str, prompt: str, stage: str) -> str:
        self.calls += 1
        self.by_stage[stage] = self.by_stage.get(stage, 0) + 1
        self.by_agent[agent_id] = self.by_agent.get(agent_id, 0) + 1
        return self._provider(agent_id, prompt, stage)

    def usage(self) -> dict[str, Any]:
        return {"calls": self.calls, "by_stage": dict(self.by_stage), "by_agent": dict(self.by_agent)}

    def reset(self) -> None:
        self.calls = 0
        self.by_stage.clear()
        self.by_agent.clear()
