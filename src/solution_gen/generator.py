import json
from llm_gateway import llm_gateway
from agents.core import HttpModel
from .models import SolutionResult
from .comparison import validate_compare_table

PROMPT_TPL = """
你是方案拆解专家。基于下面需求，输出3套分解方案+对比选型表。
对比维度固定：实现难度、成本、风险、交付周期、推荐优先级。
严格输出JSON，不要额外文字、不要解释。
{
  "plans": [
    {"plan_name": "", "desc": ""},
    {"plan_name": "", "desc": ""},
    {"plan_name": "", "desc": ""}
  ],
  "compare_table": [
    {"dim": "实现难度", "plan1": "", "plan2": "", "plan3": ""},
    {"dim": "成本", "plan1": "", "plan2": "", "plan3": ""},
    {"dim": "风险", "plan1": "", "plan2": "", "plan3": ""},
    {"dim": "交付周期", "plan1": "", "plan2": "", "plan3": ""},
    {"dim": "推荐优先级", "plan1": "", "plan2": "", "plan3": ""}
  ]
}
需求：{requirement}
"""

llm_model = HttpModel.from_env()
llm_gateway.set_provider(llm_model)

async def generate_solution(requirement: str) -> SolutionResult:
    prompt = PROMPT_TPL.format(requirement=requirement)
    raw = llm_model(prompt)
    data = json.loads(raw)
    result = SolutionResult(**data)
    if not validate_compare_table(result):
        raise ValueError("返回的对比表维度不全")
    return result

async def solution_gen_entry(requirement: str):
    return await generate_solution(requirement)
