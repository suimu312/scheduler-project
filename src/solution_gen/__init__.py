from .generator import solution_gen_entry
import asyncio
from src.conflict_review.contracts import StageResult


def run(ctx) -> StageResult:
    requirement: str = ctx.requirement
    loop = asyncio.new_event_loop()
    try:
        result_data = loop.run_until_complete(solution_gen_entry(requirement))
    finally:
        loop.close()
    return StageResult(data=result_data)
