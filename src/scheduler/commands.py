"""编排层命令处理（作业3 O2）。

三视图/交互控件归 E；这里是服务端：暂停、批注、单卡重试三类命令先落
`command.issued`，处理完再落 `command.applied`，两条事件成对出现。
单卡重试不是在原运行上改产物，而是 fork 出一条 `<run_id>.fork.<n>` 的
新运行，父子两条并排对比；父运行的状态不被回写。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .context import RunConfig, RunState, now_iso
from .events import EventEmitter
from .store import RunStore

COMMAND_ACTIONS = ("pause", "comment", "retry_task")


def issue_command(
    state: RunState,
    action: str,
    target: str = "",
    note: str = "",
    by: str = "user",
) -> dict[str, Any]:
    """命令只登记不生效：状态机看到的是 applied 之后的结果。"""
    if action not in COMMAND_ACTIONS:
        raise ValueError(f"非法命令 {action}，允许：{COMMAND_ACTIONS}")
    cmd: dict[str, Any] = {
        "command_id": f"CMD-{len(state.commands) + 1:03d}",
        "run_id": state.run_id,
        "action": action,
        "target": target,
        "note": note,
        "issued_by": by,
        "issued_at": now_iso(),
        "status": "issued",
        "applied_at": None,
        "fork_run_id": None,
    }
    state.commands.append(cmd)
    return cmd


def apply_command(
    store: RunStore,
    config: RunConfig,
    state: RunState,
    cmd: dict[str, Any],
) -> dict[str, Any]:
    """把已登记的命令翻译成状态变更；retry_task 在此 fork 新运行。"""
    action = str(cmd["action"])
    note = str(cmd.get("note") or "")
    target = str(cmd.get("target") or "")

    effect: dict[str, Any]
    if action == "pause":
        state.status = "waiting_human"
        state.phase = "HUMAN_GATE"
        state.pending = {
            "from": state.phase,
            "to": state.phase,
            "gate_id": "CMD_PAUSE",
            "reason": note or "paused_by_command",
            "command_id": cmd["command_id"],
        }
        effect = {"action": "pause", "phase": "HUMAN_GATE", "status": "waiting_human"}

    elif action == "comment":
        if note and note not in state.constraints:
            state.constraints.append(note)
        state.human_decisions.append(
            {
                "decision_id": f"HD-{len(state.human_decisions) + 1:03d}",
                "run_id": state.run_id,
                "at_phase": state.phase,
                "action": "comment",
                "reason": note,
                "payload_ref": target,
                "decided_by": str(cmd.get("issued_by") or "user"),
                "decided_at": now_iso(),
                "command_id": cmd["command_id"],
            }
        )
        state.status = "waiting_human"
        state.phase = "HUMAN_GATE"
        effect = {"action": "comment", "phase": "HUMAN_GATE", "status": "waiting_human"}

    elif action == "retry_task":
        fork_id = fork_run(store, config, state, cmd)
        cmd["fork_run_id"] = fork_id
        effect = {
            "action": "retry_task",
            "target": target,
            "fork_run_id": fork_id,
            "status": "running",
        }

    else:  # 防御：issue_command 已校验，这里不会到
        raise ValueError(f"非法命令 {action}")

    cmd["status"] = "applied"
    cmd["applied_at"] = now_iso()
    state.touch()
    return effect


def fork_run(store: RunStore, config: RunConfig, state: RunState, cmd: dict[str, Any]) -> str:
    """复制父运行目录为 `<parent>.fork.<n>`，带产物/事件/配置，状态从 P3 重跑被重试卡。"""
    parent = state.run_id
    n = 1
    while store.run_dir(f"{parent}.fork.{n}").exists():
        n += 1
    fork_id = f"{parent}.fork.{n}"

    src = store.run_dir(parent)
    dst = store.run_dir(fork_id)
    if not src.exists():
        raise FileNotFoundError(f"父运行目录不存在：{src}")
    shutil.copytree(src, dst)

    # 配置：改 run_id 与父链，其余阈值/预算沿用父运行
    fork_cfg = RunConfig.from_dict(json.loads((dst / "config.json").read_text(encoding="utf-8")))
    fork_cfg.run_id = fork_id
    fork_cfg.parent_run_id = parent
    store.save_config(fork_id, fork_cfg.to_dict())

    # 状态：父子隔离，索引路径指到 fork 目录；ledger 沿用父运行已花的预算
    fork_state = RunState.from_dict(state.to_dict())
    fork_state.run_id = fork_id
    fork_state.parent_run_id = parent
    fork_state.status = "running"
    fork_state.phase = "P3"
    fork_state.termination_reason = None
    fork_state.commands = []
    fork_state.pending = {
        "forked_from": parent,
        "retry_task": str(cmd.get("target") or ""),
        "reason": str(cmd.get("note") or ""),
        "command_id": cmd["command_id"],
    }
    fork_state.artifacts_index = {
        name: (store.artifacts_dir(fork_id) / f"{name}.json").as_posix() for name in state.artifacts_index
    }
    store.save_state(fork_state)

    # fork 事件：父运行与 fork 各记一条，便于复盘并排对比
    parent_emitter = EventEmitter(store, parent, state.ledger.global_used)
    parent_emitter.emit(
        "fork.created",
        stage=state.phase,
        actor="scheduler",
        payload_ref=fork_id,
        detail={"parent_run_id": parent, "fork_run_id": fork_id, "retry_task": cmd.get("target"), "command_id": cmd["command_id"]},
    )
    fork_emitter = EventEmitter(store, fork_id, fork_state.ledger.global_used)
    fork_emitter.emit(
        "fork.created",
        stage="P3",
        actor="scheduler",
        payload_ref=parent,
        detail={"parent_run_id": parent, "fork_run_id": fork_id, "retry_task": cmd.get("target"), "command_id": cmd["command_id"]},
    )
    return fork_id
