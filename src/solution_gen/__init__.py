from .generator import solution_gen_entry
import asyncio
# 改用A模块统一定义的标准结构，替换原来的导入
from src.scheduler.context import StageResult, RunEvent, now_iso


def run(ctx) -> StageResult:
    requirement: str = ctx.requirement
    all_events = []  # 用来存本阶段所有埋点事件

    # ========== 新增：P4开始事件 ==========
    all_events.append(RunEvent(
        event_id="",
        run_id=ctx.run_id,
        ts=now_iso(),
        stage=ctx.stage,
        event_type="p4.start",
        actor="solution_gen",
        payload_ref=f"requirement_len:{len(requirement)}",
        budget_used=0
    ))

    loop = asyncio.new_event_loop()
    try:
        result_data = loop.run_until_complete(solution_gen_entry(requirement))
    finally:
        loop.close()

    # ========== 新增：P4结束事件 ==========
    all_events.append(RunEvent(
        event_id="",
        run_id=ctx.run_id,
        ts=now_iso(),
        stage=ctx.stage,
        event_type="p4.end",
        actor="solution_gen",
        payload_ref="solution_and_comparison_done",
        budget_used=3
    ))

    # ========== 改成标准返回结构，把事件和产物都带回去 ==========
    return StageResult(
        stage=ctx.stage,
        status="ok",
        artifacts={
            "solution_comparison": result_data,
            "solutions": result_data.get("solutions", [])
        },
        events=all_events,
        budget_used=3,
        guard_signals=[],
        extensions={}
    )
