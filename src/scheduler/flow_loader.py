"""flow.yaml / gate_rule.yaml 的解析与引用完整性校验（architecture §5.3 / §5.4）。

纪律：解析完立刻校验，非空错误列表就 fail-fast，不允许带病运行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .context import AGENT_IDS, MODULE_OWNERS, STAGES

HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------- 路径解析

def resolve_asset(ref: str, default_dir: Path = HERE) -> Path:
    """把配置里的相对路径解析成真实文件路径。

    支持三种写法：绝对路径 / 相对本文件目录（"flow.yaml"）/ 相对仓库根（"src/scheduler/flow.yaml"）。
    """
    p = Path(ref)
    if p.is_absolute():
        return p
    if (default_dir / p).exists():
        return default_dir / p
    repo_root = default_dir.parent.parent
    if (repo_root / p).exists():
        return repo_root / p
    return default_dir / p


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置文件顶层必须是映射: {path}")
    return data


# ---------------------------------------------------------------- 结构

@dataclass
class StageSpec:
    id: str
    owner: str
    human_input: bool = False
    agents: dict[str, Any] = field(default_factory=dict)
    input_artifacts: list[str] = field(default_factory=list)
    output_artifacts: list[str] = field(default_factory=list)
    gates: list[str] = field(default_factory=list)
    max_llm_calls: int = 0
    max_rounds: int | None = None
    max_revisions: int | None = None
    max_reworks: int | None = None
    protocol: list[str] = field(default_factory=list)
    limits: dict[str, Any] = field(default_factory=dict)
    variants: list[str] = field(default_factory=list)
    fallback: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def on_stage(self) -> list[str]:
        return list(self.agents.get("on_stage") or [])

    @property
    def off_stage(self) -> list[str]:
        return list(self.agents.get("off_stage") or [])

    def to_context_config(self) -> dict[str, Any]:
        """交给模块的 ctx.config：本阶段全部配置 + 广播约束。"""
        return dict(self.raw)


@dataclass
class Flow:
    version: int
    shared_constraints: list[str]
    stages: dict[str, StageSpec]
    path: Path

    def stage(self, phase: str) -> StageSpec:
        return self.stages[phase]

    def order(self) -> list[str]:
        return [s for s in STAGES if s in self.stages]

    def next_stage(self, phase: str) -> str | None:
        order = self.order()
        if phase not in order:
            return None
        i = order.index(phase)
        return order[i + 1] if i + 1 < len(order) else None


def load_flow(path: str | Path) -> Flow:
    p = Path(path)
    raw = _read_yaml(p)
    stages: dict[str, StageSpec] = {}
    for sid, spec in (raw.get("stages") or {}).items():
        stages[sid] = StageSpec(
            id=sid,
            owner=str(spec.get("owner", MODULE_OWNERS.get(sid, "scheduler"))),
            human_input=bool(spec.get("human_input", False)),
            agents=dict(spec.get("agents") or {}),
            input_artifacts=list(spec.get("input_artifacts") or []),
            output_artifacts=list(spec.get("output_artifacts") or []),
            gates=list(spec.get("gates") or []),
            max_llm_calls=int(spec.get("max_llm_calls", 0)),
            max_rounds=spec.get("max_rounds"),
            max_revisions=spec.get("max_revisions"),
            max_reworks=spec.get("max_reworks"),
            protocol=list(spec.get("protocol") or []),
            limits=dict(spec.get("limits") or {}),
            variants=list(spec.get("variants") or []),
            fallback=dict(spec.get("fallback") or {}),
            raw=dict(spec),
        )
    return Flow(
        version=int(raw.get("version", 1)),
        shared_constraints=list(raw.get("shared_constraints") or []),
        stages=stages,
        path=p,
    )


# ---------------------------------------------------------------- 校验（§5.3）

def validate_flow(
    flow: Flow,
    gate_rules: dict[str, Any] | None,
    agent_config: dict[str, Any] | None = None,
    global_llm_calls: int = 30,
) -> list[str]:
    errors: list[str] = []

    # 1. 阶段齐备且顺序完整
    if flow.order() != list(STAGES):
        errors.append(f"阶段必须完整且按 P1..P7 排列，实际 {flow.order()}")

    gate_ids = {g.get("id") for g in ((gate_rules or {}).get("gates") or [])}
    produced: list[str] = []

    for sid in flow.order():
        spec = flow.stages[sid]

        # 2. on/off 不相交且在枚举内
        on_stage, off_stage = spec.on_stage, spec.off_stage
        unknown = [a for a in set(on_stage) | set(off_stage) if a not in AGENT_IDS]
        if unknown:
            errors.append(f"{sid}: 未定义的 Agent {unknown}")
        if set(on_stage) & set(off_stage):
            errors.append(f"{sid}: on_stage 与 off_stage 相交 {sorted(set(on_stage) & set(off_stage))}")

        # 2b. agents 必须在 agent_config.yaml 里存在（B 的配置为空时降级为不校验）
        known_agents = agent_config.get("agents") if isinstance(agent_config, dict) else None
        if known_agents:
            known = set(known_agents) if not isinstance(known_agents, dict) else set(known_agents.keys())
            missing = [a for a in set(on_stage) | set(off_stage) if a not in known]
            if missing:
                errors.append(f"{sid}: agent_config.yaml 缺角色定义 {missing}")

        # 3. input_artifacts 必须是前序产物的子集（P1 除外）
        missing_inputs = [a for a in spec.input_artifacts if a not in produced]
        if missing_inputs:
            errors.append(f"{sid}: input_artifacts 引用了前序未产出的 {missing_inputs}")

        # 4. gates 必须存在于 gate_rule.yaml
        for gid in spec.gates:
            if gate_ids and gid not in gate_ids:
                errors.append(f"{sid}: gate {gid} 未在 gate_rule.yaml 中定义")

        # 6. 回炉边必须有对应 gate，且「发起回炉的阶段」有轮次/修改上限（防死循环）
        #    上限要长在循环体上：P5 回炉 P4 时，次数上限是 P5 的 max_revisions，不是 P4 的。
        for gid in spec.gates:
            g = next((x for x in ((gate_rules or {}).get("gates") or []) if x.get("id") == gid), None)
            if not g:
                continue
            to = str(g.get("to") or "")
            if to in STAGES and to != flow.next_stage(sid):
                if flow.stages.get(to) is None:
                    errors.append(f"{gid}: 目标阶段 {to} 不存在")
                elif spec.max_rounds is None and spec.max_revisions is None and spec.max_reworks is None:
                    errors.append(f"{gid}: 从 {sid} 回炉到 {to}，但 {sid} 没有 max_rounds/max_revisions/max_reworks 上限")

        produced.extend(spec.output_artifacts)

    # 5. 单阶段预算之和不得离谱（刻意调低全局预算做压力测试时不拦）
    total = sum(flow.stages[s].max_llm_calls for s in flow.order())
    if global_llm_calls >= 20 and global_llm_calls and total > int(global_llm_calls) * 1.5:
        errors.append(f"单阶段预算之和 {total} 超过全局预算 {global_llm_calls} 的 1.5 倍")

    return errors


def load_agent_config(ref: str | Path, default_dir: Path = HERE) -> dict[str, Any]:
    """B 的 agent_config.yaml 可能还没写（空文件），此时返回 {} 而不是报错。"""
    p = resolve_asset(str(ref), default_dir) if not Path(ref).is_absolute() else Path(ref)
    try:
        return _read_yaml(p)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
