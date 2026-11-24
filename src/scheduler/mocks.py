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


AGENT_IDS = ("A1", "A2", "A3", "A4", "A5", "A6")

# 规格里一条 features 都没写时的兜底（老 demo 的三条），保证链路不至于空转
_FALLBACK_FEATURES = [
    {"feature_id": "FEA-001", "title": "讨论频道的消息收发与存储"},
    {"feature_id": "FEA-002", "title": "讨论归档与全文检索"},
    {"feature_id": "FEA-003", "title": "文件与结论的关联登记"},
]


def _spec(ctx: StageContext) -> dict[str, Any]:
    """P1 把人给的设计规格原样塞进 idea_card.design_spec，这里是唯一的输入源。"""
    spec = ctx.artifact_data("idea_card").get("design_spec") or {}
    return spec if isinstance(spec, dict) else {}


def _spec_list(ctx: StageContext, key: str) -> list[str]:
    """取规格里的字符串数组（scope / non_goals / constraints），元素是对象也能吃。"""
    raw = _spec(ctx).get(key) or []
    if isinstance(raw, str):
        raw = [raw]
    out: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            text = str(item.get("title") or item.get("name") or item.get("text") or "").strip()
        else:
            text = str(item).strip()
        if text:
            out.append(text)
    return out


def _spec_features(ctx: StageContext) -> list[dict[str, str]]:
    """把规格的 features 归一成 [{feature_id, title}]。

    规格里可以写字符串（"讨论归档与全文检索"），也可以写对象
    （{"feature_id": "FEA-007", "title": "..."}）；一条都取不到才退回兜底三条。
    """
    raw = _spec(ctx).get("features") or []
    out: list[dict[str, str]] = []
    for i, item in enumerate(raw, start=1):
        if isinstance(item, dict):
            title = str(item.get("title") or item.get("name") or item.get("feature") or "").strip()
            fid = str(item.get("feature_id") or item.get("id") or f"FEA-{i:03d}").strip()
        else:
            title, fid = str(item).strip(), f"FEA-{i:03d}"
        if title:
            out.append({"feature_id": fid, "title": title})
    return out or [dict(f) for f in _FALLBACK_FEATURES]


def _feature_ids(ctx: StageContext) -> list[str]:
    return [f["feature_id"] for f in _spec_features(ctx)]


def _feature_title(ctx: StageContext, fid: str) -> str:
    for f in _spec_features(ctx):
        if f["feature_id"] == fid:
            return f["title"]
    return fid


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
    feats = _spec_features(ctx)
    ids = [f["feature_id"] for f in feats]
    solutions = [
        {"solution_id": "SOL_A", "strategy": "cost_first", "feature_ids": ids, "constraints_ref": "flow.yaml#shared_constraints"},
        {"solution_id": "SOL_B", "strategy": "experience_first", "feature_ids": ids, "constraints_ref": "flow.yaml#shared_constraints"},
        {"solution_id": "SOL_C", "strategy": "extensibility_first", "feature_ids": ids, "constraints_ref": "flow.yaml#shared_constraints"},
    ]
    if SCENARIO["p4_mismatch"]:
        solutions[2]["feature_ids"] = ids[:2]  # 扩展优先漏了一个功能 → 违反 C4

    problem_count = len(ctx.artifact_data("problem_list").get("problems") or [])
    feature_items = []
    for i, f in enumerate(feats, start=1):
        src = f"problem_list:PRB-{i:03d}" if i <= problem_count else f"idea_card:design_spec#{f['feature_id']}"
        feature_items.append({"feature_id": f["feature_id"], "title": f["title"], "source_ref": src})
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

def _dev_tasks(ctx: StageContext) -> list[dict[str, Any]]:
    """PM Agent（A6）按设计规格分解出的开发任务清单（WBS）。

    八项字段与 taskgraph.REQUIRED_FIELDS 对齐：任务号、模块、标题、依赖、工时、负责人、验收、功能追溯。

    分解口径（跟着输入规格走，不再写死）：
    - 规格里每个功能点拆两张卡：后端实现（2 人天）+ 界面交互（1 人天）；
    - 后端按功能点先后串成主链（后一个功能挂在前一个后端后面），前端挂在自己后端后面；
    - 最后补两张横切卡：主链路联调验收（2 人天）+ 接口说明与部署手册（1 人天）；
    - 单卡工时封顶 2 人天，不会命中 >3 人天的 oversize 否决项。
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
        # 六张卡全部互不依赖 → 一个波次并行 6 张，用于验证并行度上限与资源冲突提示（A-15）。
        # 这一组是「故意造出来」的测试夹具，不跟规格走，其他场景才按规格分解。
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

    feats = _spec_features(ctx)
    tasks: list[dict[str, Any]] = []
    backend_chain: list[str] = []
    frontend_ids: list[str] = []

    for i, f in enumerate(feats, start=1):
        fid, title = f["feature_id"], f["title"]
        module = f"fea{i:02d}"
        dev_id = f"DEV-{2 * i - 1:03d}"    # 后端卡
        ui_id = f"DEV-{2 * i:03d}"         # 界面卡
        tasks.append(task(
            dev_id, f"{title}：后端实现", module, "backend",
            [fid], [f"REQ-{i:03d}"], "dev_backend", 2,
            [backend_chain[-1]] if backend_chain else [],
            [f"「{title}」的主流程在后端可跑通", "接口返回结构与规格描述一致"],
        ))
        tasks.append(task(
            ui_id, f"{title}：界面与交互", module, "frontend",
            [fid], [f"REQ-{i:03d}"], "dev_frontend", 1,
            [dev_id],
            [f"「{title}」在界面上能走完整流程", "空态、失败提示都有兜底"],
        ))
        backend_chain.append(dev_id)
        frontend_ids.append(ui_id)

    all_fea = [f["feature_id"] for f in feats]
    all_req = [f"REQ-{i:03d}" for i in range(1, len(feats) + 1)]
    test_id = f"DEV-{2 * len(feats) + 1:03d}"
    docs_id = f"DEV-{2 * len(feats) + 2:03d}"
    tasks.append(task(
        test_id, "主链路联调与验收用例", "tests", "test",
        all_fea, all_req, "dev_test", 2, frontend_ids,
        ["主链路脚本全部通过", "失败用例能定位到具体卡"],
    ))
    tasks.append(task(
        docs_id, "接口说明与部署手册", "docs", "docs",
        all_fea, all_req, "dev_docs", 1, [test_id],
        ["README 能按步骤跑起来", "接口字段与返回码齐全"],
    ))

    if SCENARIO["p6_uncovered"]:
        # 最后一个功能点没有任何卡承接 → 分解漏项（把它在卡里的追溯改成第一个功能点，
        # 这样卡的 feature_refs 仍然非空，只会让 every_feature_has_task 失败）
        miss = feats[-1]["feature_id"]
        keep = feats[0]["feature_id"]
        if miss != keep:
            for t in tasks:
                t["feature_refs"] = [keep if x == miss else x for x in t["feature_refs"]]
    if SCENARIO["p6_cycle"]:
        tasks[0]["depends_on"] = [tasks[-1]["task_id"]]  # 第一张卡挂到最后一张卡上 → 成环
    return tasks


def run_req_builder(ctx: StageContext) -> StageResult:
    adopted = []
    for row in (ctx.artifact_data("solution_comparison").get("rows") or []):
        if isinstance(row, dict) and row.get("adopted"):
            adopted.append(row.get("feature_id"))

    reqs = []
    for i, fid in enumerate(adopted or _feature_ids(ctx), start=1):
        f_title = _feature_title(ctx, fid)
        ac = [] if (SCENARIO["p6_missing_acceptance"] and i == 1) else [{
            "given": "用户已完成登录",
            "when": f"用户使用「{f_title}」这条能力",
            "then": f"「{f_title}」按规格描述返回预期结果",
        }]
        reqs.append({
            "req_id": f"REQ-{i:03d}",
            "title": f_title,
            "description": f"承接 {fid}：{f_title}，按采纳方案的实现口径落地",
            "acceptance_criteria": ac,
            "priority": "must",
            "depends_on": [],
            "source_refs": [f"solution_comparison:{fid}.adopted"],
            "baseline_version": "v1.0",
        })

    rows = []
    feature_ids = _feature_ids(ctx)
    for i, fid in enumerate(feature_ids, start=1):
        covered = [] if (SCENARIO["p6_gap"] and i == len(feature_ids)) else [f"REQ-{i:03d}"]
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
        "tasks": _dev_tasks(ctx),
        "source_ref": "idea_card:design_spec",
        "decomposition_by": "A6/pm_agent",
        "note": "开发任务清单（WBS），按设计规格的 features 逐条分解，与 §4.1 的执行卡 T-P1-01…T-P7-02 不是同一套",
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
