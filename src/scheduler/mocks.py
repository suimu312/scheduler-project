"""离线桩模块：把 P2–P7 五个模块按 §0.5 契约产出假数据。

用途有两个：
1. 让编排引擎（状态机 / 闸门 / 预算 / 事件）在没有真实 LLM、没有组员代码的情况下
   也能整条链路跑通，便于本人自查与答辩演示；
2. 单元测试与回归：`selfcheck.py` 用 flag 构造各种边界场景（冲突挂起、方案清单不一致、
   标准差超标、覆盖有空行、预算耗尽……）验证闸门是否按文档判。

真实模块接入时，把 modules.resolve() 的 provider 切到 "real" 即可，签名完全一致：
    run(ctx: StageContext) -> StageResult
"""

from __future__ import annotations

from typing import Any

from .context import (
    StageContext,
    StageResult,
    make_artifact,
    now_iso,
)

SCENARIO: dict[str, Any] = {
    "name": "happy",
    # P3
    "conflict_escalate": False,   # 一比一冲突 → G_P3_HUMAN
    "conflict_open_rounds": 0,    # 前 N 轮保持有未决冲突 → G_P3_P4 stay（满 3 轮后放行）
    "p3_burn": 4,                 # P3 计费（真实调用数）
    # P4
    "p4_mismatch": False,         # 三套方案功能清单不一致 → G_P4_P5 false
    # P5
    "p5_mode": "pass",            # pass | stddev | veto | low_total
    "p5_category": "solution",    # solution | requirement
    # P6
    "p6_gap": False,              # 覆盖矩阵有空行 → G_P6_P7 false
    "p6_missing_acceptance": False,
    "p6_cycle": False,            # dev_task 依赖成环 → G_P6_TASKS 命中，原地重新分解
    "p6_uncovered": False,        # 有功能点没有任务承接 → G_P6_P7 false
    "p6_wide": False,             # 六张互不依赖的卡 → 波次并行度超上限（A-15）
    # P7
    "p7_frozen": True,
}


def set_scenario(name: str = "happy", **kwargs: Any) -> dict[str, Any]:
    SCENARIO.update({"name": name})
    SCENARIO.update({k: v for k, v in kwargs.items() if k in SCENARIO})
    SCENARIO["name"] = name
    return SCENARIO


def reset_scenario() -> None:
    set_scenario(
        "happy",
        conflict_escalate=False,
        conflict_open_rounds=0,
        p3_burn=4,
        p4_mismatch=False,
        p5_mode="pass",
        p5_category="solution",
        p6_gap=False,
        p6_missing_acceptance=False,
        p6_cycle=False,
        p6_uncovered=False,
        p6_wide=False,
        p7_frozen=True,
    )


# ---------------------------------------------------------------- 工具

def _idea_text(ctx: StageContext) -> str:
    return str(ctx.artifact_data("idea_card").get("idea_text") or "（缺想法）")


def _feature_ids() -> list[str]:
    return ["FEA-001", "FEA-002", "FEA-003"]


def _ok(stage: str, artifacts: dict[str, Any], budget: int, events: list[dict[str, Any]] | None = None) -> StageResult:
    result = StageResult(stage=stage, status="ok", artifacts=artifacts, budget_used=budget)
    for e in events or []:
        result.events.append(_evt(stage, e))
    return result


def _evt(stage: str, e: dict[str, Any]):
    from .context import RunEvent  # 局部导入避免循环

    return RunEvent(
        event_id="",
        run_id=e.get("run_id", ""),
        ts=now_iso(),
        stage=stage,
        event_type=str(e.get("event_type") or "module.event"),
        actor=str(e.get("actor") or "mock"),
        payload_ref=str(e.get("payload_ref") or ""),
        budget_used=int(e.get("budget_used") or 0),
    )


# ---------------------------------------------------------------- P2 agents

def run_agents(ctx: StageContext) -> StageResult:
    stage = ctx.stage
    if stage == "P2":
        return _run_p2(ctx)
    if stage == "P3":
        return _run_p3(ctx)
    return StageResult(stage=stage, status="failed", errors=[{"code": "E_AGENT_UNAVAILABLE", "message": f"agents 不支持阶段 {stage}"}])


def _run_p2(ctx: StageContext) -> StageResult:
    problems = [
        {"problem_id": "PRB-001", "summary": "小团队讨论记录散落在多个工具", "types": ["functional"], "confidence": "high", "owner_roles": ["A2"]},
        {"problem_id": "PRB-002", "summary": "文件版本与讨论结论对不上", "types": ["functional", "cost"], "confidence": "high", "owner_roles": ["A2", "A1"]},
        {"problem_id": "PRB-003", "summary": "是否需要权限隔离说不清", "types": ["risk"], "confidence": "low", "owner_roles": ["A4"]},
    ]
    convocation = {
        "by_problem": {
            "PRB-001": {"on_stage": ["A2"], "reason": "mandatory:functional"},
            "PRB-002": {"on_stage": ["A2", "A1"], "reason": "mandatory:functional+cost"},
            "PRB-003": {"on_stage": ["A1", "A2", "A3", "A4", "A5", "A6"], "reason": "low_confidence_all_hands"},
        },
        "on_stage": ["A1", "A2", "A3", "A4", "A5", "A6"],
    }
    arts = {
        "problem_list": make_artifact({"problems": problems, "idea_ref": "idea_card:IDEA-001"}, ctx.run_id, "P2", "agents"),
        "convocation_table": make_artifact(convocation, ctx.run_id, "P2", "agents"),
    }
    return _ok("P2", arts, 3, [{"event_type": "agent.on_stage", "actor": "agents", "payload_ref": "convocation_table"}])


def _run_p3(ctx: StageContext) -> StageResult:
    round_index = int(ctx.round_index)
    minute_statements = [
        {"statement_id": "ST-002-01", "agent_id": "A2", "topic_id": "message_channel", "position": "must_have_archived_search", "quote": "讨论要能按项目归档并搜到"},
        {"statement_id": "ST-003-01", "agent_id": "A3", "topic_id": "message_channel", "position": "reuse_oss_channel", "quote": "复用开源消息组件更稳"},
        {"statement_id": "ST-001-01", "agent_id": "A1", "topic_id": "scope", "position": "keep_scope_small", "quote": "先做最小闭环"},
        {"statement_id": "ST-004-01", "agent_id": "A4", "topic_id": "permission", "position": "must_isolate", "quote": "外部成员不能看到内部文件"},
    ]

    open_rounds = int(SCENARIO["conflict_open_rounds"])
    keep_open = round_index <= open_rounds

    conflicts: list[dict[str, Any]] = []
    escalated_ids: list[str] = []

    if SCENARIO["conflict_escalate"]:
        conflicts.append({
            "conflict_id": "CFL-003",
            "topic_id": "permission",
            "stances": [
                {"agent_id": "A2", "position": "no_isolation_needed", "evidence_refs": []},
                {"agent_id": "A4", "position": "must_isolate", "evidence_refs": []},
            ],
            "level": "L3_escalate",
            "status": "escalated",
            "resolution": None,
            "source_refs": ["statement:ST-002-01", "statement:ST-004-01"],
        })
        escalated_ids.append("CFL-003")
    else:
        conflicts.append({
            "conflict_id": "CFL-001",
            "topic_id": "message_channel",
            "stances": [
                {"agent_id": "A2", "position": "must_have_archived_search", "evidence_refs": ["statement:ST-002-01"]},
                {"agent_id": "A3", "position": "reuse_oss_channel", "evidence_refs": ["cost_estimate:CE-001"]},
            ],
            "level": "L1_evidence",
            "status": "resolved" if not keep_open else "open",
            "resolution": {"type": "evidence", "winner": "A3", "decided_by": "conflict_review", "reason": "有开源组件复用成本数据", "round_index": round_index},
            "source_refs": ["statement:ST-002-01", "statement:ST-003-01"],
        })
        conflicts.append({
            "conflict_id": "CFL-002",
            "topic_id": "scope",
            "stances": [
                {"agent_id": "A1", "position": "keep_scope_small", "evidence_refs": []},
                {"agent_id": "A2", "position": "include_archive", "evidence_refs": ["user_interview:UI-001"]},
            ],
            "level": "L2_weighted_vote",
            "status": "resolved" if not keep_open else "open",
            "resolution": {"type": "weighted_vote", "winner": "A2", "decided_by": "conflict_review", "reason": "A2 权重 1.5 占优", "round_index": round_index},
            "source_refs": ["statement:ST-001-01", "statement:ST-002-01"],
        })

    open_count = sum(1 for c in conflicts if str(c.get("status")) == "open")
    minutes = {
        "statements": minute_statements,
        "query_pairs": [{"asker": "A2", "responder": "A3", "quote_ref": "statement:ST-003-01", "answer": "复用组件可覆盖归档检索"}],
        "votes": [{"conflict_id": c["conflict_id"], "votes": [{"agent_id": "A2", "value": "accept"}, {"agent_id": "A3", "value": "accept"}]} for c in conflicts],
        "supplements": [],
        "round_index": round_index,
    }
    arts = {
        "discussion_minutes": make_artifact(minutes, ctx.run_id, "P3", "agents"),
        "conflict_register": make_artifact(
            {"conflicts": conflicts, "open_count": open_count, "escalated_ids": escalated_ids},
            ctx.run_id, "P3", "conflict_review",
        ),
    }
    burn = int(SCENARIO["p3_burn"])
    return _ok("P3", arts, burn, [
        {"event_type": "agent.statement", "actor": "A2", "payload_ref": "discussion_minutes:ST-002-01"},
        {"event_type": "conflict.detected", "actor": "conflict_review", "payload_ref": "conflict_register:CFL-001"},
    ])


# ---------------------------------------------------------------- P4 solution_gen

def run_solution_gen(ctx: StageContext) -> StageResult:
    ids = _feature_ids()
    solutions = [
        {"solution_id": "SOL_A", "strategy": "cost_first", "feature_ids": ids, "constraints_ref": "flow.yaml#shared_constraints"},
        {"solution_id": "SOL_B", "strategy": "experience_first", "feature_ids": ids, "constraints_ref": "flow.yaml#shared_constraints"},
        {"solution_id": "SOL_C", "strategy": "extensibility_first", "feature_ids": ids, "constraints_ref": "flow.yaml#shared_constraints"},
    ]
    if SCENARIO["p4_mismatch"]:
        solutions[2]["feature_ids"] = ids[:2]  # 扩展优先漏了一个功能 → 违反 C4

    feature_items = [
        {"feature_id": "FEA-001", "title": "项目内讨论频道", "source_ref": "problem_list:PRB-001"},
        {"feature_id": "FEA-002", "title": "讨论归档与检索", "source_ref": "problem_list:PRB-002"},
        {"feature_id": "FEA-003", "title": "文件与结论关联", "source_ref": "problem_list:PRB-003"},
    ]
    rows = []
    for i, fid in enumerate(ids, start=1):
        rows.append({
            "feature_id": fid,
            "options": {
                "SOL_A": {"description": "复用开源组件", "cost_estimate": i, "risk": "low"},
                "SOL_B": {"description": "自研，交互优先", "cost_estimate": i * 3, "risk": "medium"},
                "SOL_C": {"description": "抽象为可扩展中台", "cost_estimate": i * 5, "risk": "high"},
            },
            "adopted": {
                "solution_id": "SOL_B" if fid == "FEA-001" else "SOL_A",
                "interaction": fid == "FEA-001",
                "implementation_from": "SOL_A",
                "rationale": "交互取 B、实现取 A",
            },
            "source_refs": ["conflict_register:CFL-001"],
        })
    comparison = {
        "feature_items": feature_items,
        "solutions": solutions,
        "rows": rows,
        "adopted_ref": "baseline_decision",
        "shared_constraints": ["预算不超过既定上限", "必须覆盖同一份功能清单 FEA-*"],
    }
    arts = {
        "solution_comparison": make_artifact(comparison, ctx.run_id, "P4", "solution_gen"),
        "solutions": make_artifact({"solutions": solutions}, ctx.run_id, "P4", "solution_gen"),
    }
    return _ok("P4", arts, 4, [{"event_type": "solution.generated", "actor": "solution_gen", "payload_ref": "solution_comparison"}])


# ---------------------------------------------------------------- P5 conflict_review

def run_conflict_review(ctx: StageContext) -> StageResult:
    mode = str(SCENARIO["p5_mode"])
    category = str(SCENARIO["p5_category"])

    dims = {"completeness": 88, "feasibility": 86, "risk": 84, "testability": 85}
    if mode == "stddev":
        dims = {"completeness": 95, "feasibility": 92, "risk": 60, "testability": 90}  # stddev ≈ 14.4
    elif mode == "low_total":
        dims = {"completeness": 70, "feasibility": 72, "risk": 68, "testability": 71}

    raw = 25 * dims["completeness"] + 25 * dims["feasibility"] + 25 * dims["risk"] + 15 * dims["testability"]
    weighted_total = round(raw / 90.0, 2)  # 0–100 分制（分母是权重和 90，成本不计分）
    scores = [
        {"dimension": "completeness", "scorer": "A2", "score": dims["completeness"], "reason": "核心场景可走通"},
        {"dimension": "feasibility", "scorer": "A3", "score": dims["feasibility"], "reason": "技术可实现"},
        {"dimension": "risk", "scorer": "A4", "score": dims["risk"], "reason": "存在可控风险"},
        {"dimension": "testability", "scorer": "A5", "score": dims["testability"], "reason": "可写 Given-When-Then"},
        {"dimension": "cost", "scorer": "A1", "score": 80, "reason": "仅估算不计分"},
    ]

    challenges: list[dict[str, Any]] = []
    if mode in ("stddev", "low_total"):
        challenges.append({
            "challenge_id": "CHG-004",
            "description": "风险维度明显偏低：权限隔离方案没落地",
            "impact_ref": "solution_comparison:FEA-003.adopted",
            "severity": "major",
            "consequence": "外部成员可能看到内部文件",
            "verdict": "accepted",
            "category": category,
        })
    if mode == "veto":
        challenges.append({
            "challenge_id": "CHG-005",
            "description": "缺少审计日志，无法追责",
            "impact_ref": "solution_comparison:FEA-002.adopted",
            "severity": "blocking",
            "consequence": "数据被改后无法定位责任人",
            "verdict": "accepted",
            "category": category,
        })

    review = {
        "challenges": challenges,
        "scores": scores,
        "weighted_total": weighted_total,
        "stddev": None,
        "veto": mode == "veto",
        "blocker_count": 1 if mode == "veto" else 0,
        "reviewer_absent": ["A1"],
        "recuse_reason": "author_must_not_review_own_work",
    }
    # conflict_register 在 P5 会被更新裁决结果，按 flow.yaml 的 output_artifacts 必须回传
    prev_conflicts = ctx.artifact_data("conflict_register").get("conflicts") or []
    open_count = sum(1 for c in prev_conflicts if str(c.get("status")) == "open")
    arts = {
        "review_sheet": make_artifact(review, ctx.run_id, "P5", "conflict_review"),
        "conflict_register": make_artifact(
            {"conflicts": prev_conflicts, "open_count": open_count, "escalated_ids": []},
            ctx.run_id, "P5", "conflict_review",
        ),
    }
    return _ok("P5", arts, 5, [{"event_type": "score.published", "actor": "conflict_review", "payload_ref": "review_sheet"}])


# ---------------------------------------------------------------- P6 req_builder（含 PM Agent 的 dev_task 分解）

def _dev_tasks() -> list[dict[str, Any]]:
    """PM Agent（A6）按设计规格分解出的开发任务清单（WBS）。

    八项字段与 taskgraph.REQUIRED_FIELDS 对齐：任务号、模块、标题、依赖、工时、负责人、验收、功能追溯。
    """
    def task(tid, title, module, kind, fea, req, role, days, deps, acceptance):
        return {
            "task_id": tid,
            "title": title,
            "module": module,
            "kind": kind,
            "feature_refs": list(fea),
            "requirement_refs": list(req),
            "owner_role": role,
            "estimate_days": days,
            "depends_on": list(deps),
            "acceptance": acceptance,
            "deliverable": f"src/{module}",
        }

    if SCENARIO["p6_wide"]:
        # 六张卡全部互不依赖 → 一个波次并行 6 张，用于验证并行度上限与资源冲突提示（A-15）
        return [
            task("DEV-101", "账号与登录", "auth", "backend", ["FEA-001"], ["REQ-001"], "dev_backend", 2, [],
                 ["能注册并登录，会话可续"]),
            task("DEV-102", "讨论频道骨架", "channel", "frontend", ["FEA-001"], ["REQ-001"], "dev_frontend", 2, [],
                 ["能创建频道并看到列表"]),
            task("DEV-103", "消息存储", "store", "backend", ["FEA-001"], ["REQ-001"], "dev_data", 2, [],
                 ["消息落库，重启不丢"]),
            task("DEV-104", "归档索引", "search", "backend", ["FEA-002"], ["REQ-002"], "dev_search", 2, [],
                 ["按关键词能搜到历史讨论"]),
            task("DEV-105", "文件关联", "attach", "integration", ["FEA-003"], ["REQ-003"], "dev_integration", 2, [],
                 ["文件与结论能互链"]),
            task("DEV-106", "验收脚本", "tests", "test", ["FEA-001", "FEA-002", "FEA-003"], ["REQ-001"], "dev_test", 2, [],
                 ["主链路脚本可一键跑"]),
        ]

    tasks = [
        task("DEV-001", "讨论频道的消息收发与存储", "messaging", "backend",
             ["FEA-001"], ["REQ-001"], "dev_backend", 3, [],
             ["发送一条讨论后能在该频道读到", "重启服务后历史消息不丢"]),
        task("DEV-002", "讨论归档与全文检索", "archive", "backend",
             ["FEA-002"], ["REQ-002"], "dev_backend", 4, ["DEV-001"],
             ["能按项目归档讨论", "关键词能命中 90 天内的归档"]),
        task("DEV-003", "讨论列表与详情页", "web-chat", "frontend",
             ["FEA-001"], ["REQ-001"], "dev_frontend", 3, ["DEV-001"],
             ["列表按时间倒序", "详情页可回复"]),
        task("DEV-004", "归档筛选与搜索框", "web-archive", "frontend",
             ["FEA-002"], ["REQ-002"], "dev_frontend", 2, ["DEV-003", "DEV-002"],
             ["可按项目与时间筛选", "搜索无结果有兜底提示"]),
        task("DEV-005", "文件与结论的关联登记", "attachment", "data",
             ["FEA-003"], ["REQ-003"], "dev_data", 3, ["DEV-001"],
             ["文件可挂到某条结论上", "关联关系可反查"]),
        task("DEV-006", "外部成员的可见性边界", "permission", "integration",
             ["FEA-003"], ["REQ-003"], "dev_integration", 2, ["DEV-005"],
             ["外部成员看不到内部文件", "越权请求被拒并留痕"]),
        task("DEV-007", "主链路验收用例", "tests", "test",
             ["FEA-001", "FEA-002", "FEA-003"], ["REQ-001", "REQ-002", "REQ-003"], "dev_test", 2,
             ["DEV-004", "DEV-006"],
             ["主链路脚本全部通过", "失败用例能定位到具体卡"]),
        task("DEV-008", "接口说明与部署手册", "docs", "docs",
             ["FEA-001", "FEA-002", "FEA-003"], ["REQ-001", "REQ-002", "REQ-003"], "dev_docs", 1,
             ["DEV-007"],
             ["README 能按步骤跑起来", "接口字段与返回码齐全"]),
    ]
    if SCENARIO["p6_uncovered"]:
        # FEA-003 没有任何任务承接 → 分解漏项
        for t in tasks:
            t["feature_refs"] = [f for f in t["feature_refs"] if f != "FEA-003"]
    if SCENARIO["p6_cycle"]:
        tasks[0]["depends_on"] = ["DEV-008"]  # 001 → 008 → 007 → 004 → 002 → 001，成环
    return tasks


def run_req_builder(ctx: StageContext) -> StageResult:
    adopted = []
    for row in (ctx.artifact_data("solution_comparison").get("rows") or []):
        if isinstance(row, dict) and row.get("adopted"):
            adopted.append(row.get("feature_id"))

    reqs = []
    for i, fid in enumerate(adopted or _feature_ids(), start=1):
        ac = [] if (SCENARIO["p6_missing_acceptance"] and i == 1) else [{
            "given": "用户已在项目内",
            "when": "用户提交一条讨论",
            "then": "讨论出现在该项目的归档列表中",
        }]
        reqs.append({
            "req_id": f"REQ-{i:03d}",
            "title": f"承接 {fid} 的需求",
            "description": f"依据 {fid} 的采纳实现落地",
            "acceptance_criteria": ac,
            "priority": "must",
            "depends_on": [],
            "source_refs": [f"solution_comparison:{fid}.adopted"],
            "baseline_version": "v1.0",
        })

    rows = []
    for i, fid in enumerate(_feature_ids(), start=1):
        covered = [] if (SCENARIO["p6_gap"] and i == 3) else [f"REQ-{i:03d}"]
        rows.append({
            "source_type": "feature",
            "source_id": fid,
            "source_ref": f"solution_comparison:{fid}",
            "covered_by": covered,
            "status": "covered" if covered else "gap",
        })
    gap_count = sum(1 for r in rows if not r["covered_by"])
    coverage = {
        "rows": rows,
        "columns": [r["req_id"] for r in reqs],
        "gap_count": gap_count,
        "coverage_rate": round((len(rows) - gap_count) / len(rows), 3),
    }
    dev_tasks = {
        "tasks": _dev_tasks(),
        "source_ref": "idea_card:design_spec",
        "decomposition_by": "A6/pm_agent",
        "note": "开发任务清单（WBS），与 §4.1 的执行卡 T-P1-01…T-P7-02 不是同一套",
    }
    arts = {
        "requirement_spec": make_artifact(
            {"requirements": reqs, "hard_validation": {"all_have_acceptance_criteria": all(r["acceptance_criteria"] for r in reqs)}},
            ctx.run_id, "P6", "req_builder",
        ),
        "coverage_matrix": make_artifact(coverage, ctx.run_id, "P6", "req_builder"),
        "dev_task_list": make_artifact(dev_tasks, ctx.run_id, "P6", "req_builder"),
    }
    return _ok("P6", arts, 3, [
        {"event_type": "coverage.computed", "actor": "req_builder", "payload_ref": "coverage_matrix"},
        {"event_type": "tasks.decomposed", "actor": "req_builder", "payload_ref": f"dev_task_list:{len(dev_tasks['tasks'])}"},
    ])


# ---------------------------------------------------------------- P7 baseline_mgr

def run_baseline_mgr(ctx: StageContext) -> StageResult:
    signed = any(str(d.get("action")) == "sign" for d in ctx.human_decisions)
    req_ids = [r.get("req_id") for r in (ctx.artifact_data("requirement_spec").get("requirements") or []) if isinstance(r, dict)]
    sched = ctx.artifact_data("schedule")
    tasks = sched.get("tasks") or []
    baseline = {
        "version": "v1.0",
        "requirement_ids": req_ids,
        "task_ids": [t.get("task_id") for t in tasks],
        "schedule_ref": f"schedule:W{len(sched.get('waves') or [])}",
        "total_days": sched.get("total_days"),
        "critical_path": (sched.get("critical_path") or {}).get("task_ids") or [],
        "signed_by": "user" if signed else None,
        "signed_at": now_iso() if signed else None,
        "frozen": bool(signed and SCENARIO["p7_frozen"]),
    }
    sheet = {
        "run_id": ctx.run_id,
        "requirements": len(req_ids),
        "coverage_rate": ctx.artifact_data("coverage_matrix").get("coverage_rate"),
        "gap_count": ctx.artifact_data("coverage_matrix").get("gap_count"),
        "tasks": len(tasks),
        "waves": len(sched.get("waves") or []),
        "total_days": sched.get("total_days"),
        "critical_path": (sched.get("critical_path") or {}).get("task_ids") or [],
        "parallel_width": sched.get("max_wave_width_actual"),
        "opinions": [],
    }
    arts = {
        "opinion_handling": make_artifact(
            {"opinions": [], "note": "P5 意见在 P7 由 F 汇总处理（通过/驳回/待定）"},
            ctx.run_id, "P7", "baseline_mgr",
        ),
        "confirmation_sheet": make_artifact(sheet, ctx.run_id, "P7", "baseline_mgr"),
        "baseline": make_artifact(baseline, ctx.run_id, "P7", "baseline_mgr"),
    }
    return _ok("P7", arts, 0 if signed else 1, [{"event_type": "human.decision", "actor": "baseline_mgr", "payload_ref": "baseline"}])
