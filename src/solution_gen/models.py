# src/solution_gen/models.py
from pydantic import BaseModel
from typing import List

class PlanItem(BaseModel):
    plan_name: str
    desc: str

class CompareRow(BaseModel):
    dim: str
    plan1: str
    plan2: str
    plan3: str

class SolutionResult(BaseModel):
    plans: List[PlanItem]
    compare_table: List[CompareRow]
