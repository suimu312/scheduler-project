"""src/scheduler —— 编排引擎（A 负责：状态机 · 闸门 · 调度 · 预算 · 事件 · 人工闸门）。

对外只需要两个入口：
    from src.scheduler import boot, RunConfig
    rt = boot(RunConfig(run_id="R0001", idea_text="..."))
    result = rt.run(rt.load_or_init())

模块目录：
    context.py      统一数据契约（§0.4 / §2.1 / §2.2）
    store.py        runs/<run_id>/ 状态与产物的唯一读写入口
    events.py       events.jsonl 追加写与复盘
    flow_loader.py  flow.yaml 解析 + 引用完整性校验（fail-fast）
    gate_check.py   闸门纯函数白名单（gate_rule.yaml 只写函数名）
    gate_rule.py    闸门优先级求值
    dispatcher.py   谁上场 / 不上场 / 三段式协议 / 回避
    budget.py       计费口径与终止判定
    human_gate.py   人工动作落库与提交后的状态解析
    llm_gateway.py  统一 LLM 调用计数
    modules.py      五个模块的注册表（mock / real 两种 provider）
    mocks.py        离线桩（让状态机不依赖组员代码也能整链跑通）
    selfcheck.py    §15 验收用例的可执行版本
    statemachine.py 主循环 step() / run() / apply_transition()
    cli.py          命令行入口
"""

from .context import BudgetLedger, RunConfig, RunState, StageContext, StageResult, make_artifact
from .statemachine import ConfigError, Runtime, boot

__all__ = [
    "BudgetLedger",
    "RunConfig",
    "RunState",
    "StageContext",
    "StageResult",
    "make_artifact",
    "ConfigError",
    "Runtime",
    "boot",
]
