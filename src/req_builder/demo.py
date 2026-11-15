"""自检入口：不需要编排器，也能跑通 P6 + 七视图。

    python -m req_builder.demo            # 生成 runs/R0001 并渲染 index.html
    python -m req_builder.demo --out DIR  # 指定输出根目录

它做三件事：
1. 用一份校园二手交易平台的设计规格 + 8 张任务卡草稿，跑 build_task_list；
2. 造一份与波次规则一致的 events.jsonl 与 schedule.json（A 的产物在 demo 里以样例代替）；
3. 调 render_views 输出自包含 index.html，并打印覆盖率、缺口与门禁数据。

样例只用于自检与答辩演示；真正跑流程时，产物由 A 落盘、E 只渲染。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .contracts import StageContext, make_artifact, now_iso
from .coverage import compute_coverage, gate_inputs
from .models import DevTask
from .priority import compute_priorities
from .task_list import build_task_list
from .validators import validate_task_list
from .viz.events import events_to_jsonl, load_events
from .viz.render import render_to_file

SPEC: dict[str, Any] = {
    "spec_id": "SPEC-001",
    "product": "校园二手交易平台：让学生在校内完成二手交易",
    "version": "v1.0",
    "scope_in": ["FEA-001", "FEA-002", "FEA-003", "FEA-004", "FEA-005"],
    "scope_out": ["在线支付", "物流配送"],
    "features": [
        {"feature_id": "FEA-001", "title": "发布商品与列表浏览"},
        {"feature_id": "FEA-002", "title": "关键词搜索与筛选"},
        {"feature_id": "FEA-003", "title": "商品下架与状态流转"},
        {"feature_id": "FEA-004", "title": "站内消息与未读计数"},
        {"feature_id": "FEA-005", "title": "发布到搜索的整链验收"},
    ],
    "constraints": [
        {"constraint_id": "CON-001", "title": "必须复用学校统一身份认证"},
        {"constraint_id": "CON-002", "title": "搜索 p95 ≤ 300ms"},
    ],
    "modules": [
        {"module_id": "MOD-001", "title": "商品服务", "owner_role": "前端"},
        {"module_id": "MOD-002", "title": "搜索服务", "owner_role": "后端"},
        {"module_id": "MOD-003", "title": "消息服务", "owner_role": "后端"},
        {"module_id": "MOD-004", "title": "账号接入", "owner_role": "后端"},
        {"module_id": "MOD-005", "title": "端到端", "owner_role": "测试"},
    ],
}

DRAFTS: list[dict[str, Any]] = [
    {
        "task_id": "DEV-001",
        "module_id": "MOD-001",
        "title": "发布商品表单与字段校验",
        "depends_on": [],
        "estimate_days": 2,
        "owner_role": "前端",
        "acceptance": ["填完必填项点发布后，列表页出现该商品"],
        "source_refs": ["solution_comparison:FEA-001", "conflict_register:CFL-001"],
    },
    {
        "task_id": "DEV-002",
        "module_id": "MOD-001",
        "title": "商品列表分页接口",
        "depends_on": ["DEV-001"],
        "estimate_days": 1.5,
        "owner_role": "后端",
        "acceptance": ["请求第 2 页返回 10 条且字段完整"],
        "source_refs": ["solution_comparison:FEA-001"],
    },
    {
        "task_id": "DEV-003",
        "module_id": "MOD-002",
        "title": "关键词搜索与筛选",
        "depends_on": ["DEV-002"],
        "estimate_days": 2,
        "owner_role": "后端",
        "acceptance": ["搜「教材」后返回命中商品，p95 不超过 300ms"],
        "source_refs": ["solution_comparison:FEA-002", "design_spec:CON-002"],
    },
    {
        "task_id": "DEV-004",
        "module_id": "MOD-003",
        "title": "站内消息发送与未读计数",
        "depends_on": ["DEV-002"],
        "estimate_days": 2,
        "owner_role": "后端",
        "acceptance": ["发送消息后，接收方未读数加 1"],
        "source_refs": ["solution_comparison:FEA-004"],
    },
    {
        "task_id": "DEV-005",
        "module_id": "MOD-003",
        "title": "消息列表页与已读标记",
        "depends_on": ["DEV-004"],
        "estimate_days": 1.5,
        "owner_role": "前端",
        "acceptance": ["点开消息后未读数清零"],
        "source_refs": ["solution_comparison:FEA-004"],
    },
    {
        "task_id": "DEV-006",
        "module_id": "MOD-004",
        "title": "对接学校统一身份认证",
        "depends_on": [],
        "estimate_days": 2.5,
        "owner_role": "后端",
        "acceptance": ["用学号登录后回填用户信息并进入首页"],
        "source_refs": ["design_spec:CON-001"],
    },
    {
        "task_id": "DEV-007",
        "module_id": "MOD-001",
        "title": "商品下架与状态流转",
        "depends_on": ["DEV-002"],
        "estimate_days": 1,
        "owner_role": "后端",
        "acceptance": ["下架后列表不再展示该商品，详情提示已下架"],
        "source_refs": ["solution_comparison:FEA-003"],
    },
    {
        "task_id": "DEV-008",
        "module_id": "MOD-005",
        "title": "发布到搜索的整链演练",
        "depends_on": ["DEV-003", "DEV-007"],
        "estimate_days": 1,
        "owner_role": "测试",
        "acceptance": ["新发布商品 10 秒内可被搜索到"],
        "source_refs": ["solution_comparison:FEA-005"],
    },
]

WAVES: list[dict[str, Any]] = [
    {"wave_no": 1, "tasks": ["DEV-001", "DEV-006"], "start_day": 1, "end_day": 2.5, "parallel_count": 2},
    {"wave_no": 2, "tasks": ["DEV-002"], "start_day": 2.5, "end_day": 3.5, "parallel_count": 1},
    {"wave_no": 3, "tasks": ["DEV-003", "DEV-004", "DEV-007"], "start_day": 3.5, "end_day": 5.5,
     "parallel_count": 3},
    {"wave_no": 4, "tasks": ["DEV-005", "DEV-008"], "start_day": 5.5, "end_day": 7, "parallel_count": 2},
]


def _schedule() -> dict[str, Any]:
    day_of = {
        "DEV-001": (1, 2),
        "DEV-006": (1, 2.5),
        "DEV-002": (2.5, 3.5),
        "DEV-003": (3.5, 5),
        "DEV-004": (3.5, 5),
        "DEV-007": (3.5, 4),
        "DEV-005": (5.5, 7),
        "DEV-008": (5.5, 6.5),
    }
    wave_of = {tid: wave["wave_no"] for wave in WAVES for tid in wave["tasks"]}
    critical = {"DEV-006", "DEV-002", "DEV-004", "DEV-005"}
    rows = []
    for draft in DRAFTS:
        tid = draft["task_id"]
        start, end = day_of[tid]
        rows.append(
            {
                "task_id": tid,
                "title": draft["title"],
                "module_id": draft["module_id"],
                "wave_no": wave_of[tid],
                "start_day": start,
                "end_day": end,
                "estimate_days": draft["estimate_days"],
                "critical": tid in critical,
                "on_path": tid in critical,
                "depends_on": draft["depends_on"],
            }
        )
    for row in rows:
        for draft in DRAFTS:
            if draft["task_id"] == row["task_id"]:
                row["priority"] = "must" if draft["task_id"] in {"DEV-001", "DEV-002", "DEV-003", "DEV-006"} else "should"
    return {
        "tasks": rows,
        "waves": WAVES,
        "critical_path": ["DEV-006", "DEV-002", "DEV-004", "DEV-005"],
        "total_days": 7,
        "parallel_limit": 3,
        "deferred": [
            {"task_id": "DEV-005", "wave_no": 4, "reason": "并行度上限顺延"},
        ],
    }


def build_events() -> list[dict[str, Any]]:
    """造一条完整的运行事件流：七个阶段、三段讨论、一次回炉、一次人工签署。"""
    events: list[dict[str, Any]] = []

    def add(event_type: str, stage: str, actor: str, **payload: Any) -> None:
        events.append({"event_type": event_type, "stage": stage, "actor": actor, "payload": payload})

    add("run.start", "P1", "scheduler", run_id="R0001", spec_id="SPEC-001")
    add("stage.enter", "P1", "scheduler", from_stage=None)
    add("human.decision", "P1", "human", action="comment", scope="design_spec",
        text="规格补了 CON-002 的性能指标")
    add("stage.exit", "P1", "scheduler", to_stage="P2")

    add("stage.enter", "P2", "scheduler", from_stage="P1")
    add("agent.on_stage", "P2", "A1", family="deliberative")
    add("agent.statement", "P2", "A1", stance="功能点分四类，模块归到 5 个",
        word_count=118)
    add("gate.evaluated", "P2", "scheduler", gate_id="G_P2_P3", action="advance", reason="每功能点有类型与责任人")
    add("stage.exit", "P2", "scheduler", to_stage="P3")

    add("stage.enter", "P3", "scheduler", from_stage="P2")
    others = [
        ("A2", "按用户价值切，消息与搜索必须独立成卡，粒度不超过 2 人天", 132),
        ("A1", "按 1.5 人天均值切，工时依据取历史同类卡", 96),
        ("A3", "依赖沿接口方向单向排列，搜索服务不反向依赖商品服务", 141),
        ("A4", "每卡必须带可观测验收，验收口径写 Given-When-Then", 108),
    ]
    for agent, stance, words in others:
        add("agent.statement", "P3", agent, stance=stance, word_count=words)
    add("guard.signal", "P3", "A4", signal="similarity_invalid", value=0.88,
        reason="与 A1 陈述语义相似度过高，判无效重说", invalid_block="ST-004")
    add("conflict.detected", "P3", "A4", conflict_id="CFL-001", topic_id="task_granularity",
        level="L2_weighted_vote",
        stances=[
            {"agent_id": "A2", "position": "按用户价值切，2 人天上限", "evidence_refs": ["statement:ST-001"]},
            {"agent_id": "A3", "position": "按接口边界切，可到 3 人天", "evidence_refs": []},
        ])
    add("agent.query", "P3", "A2", to="A3", quote="按接口边界切，可到 3 人天",
        question="同一接口拆三张卡，联调成本谁来兜？")
    add("agent.vote", "P3", "A1", vote="M", reason="粒度上限 2 人天，超出必须再拆")
    add("agent.vote", "P3", "A2", vote="M", reason="用户价值优先，卡要小")
    add("agent.vote", "P3", "A3", vote="S", reason="接口完整性也要保")
    add("agent.vote", "P3", "A4", vote="M", reason="小卡才可验收")
    add("conflict.resolved", "P3", "scheduler", conflict_id="CFL-001",
        resolution={"type": "weighted_vote", "winner": "A2", "reason": "1.5 对 1.0，差值达 1.0"})
    add("gate.evaluated", "P3", "scheduler", gate_id="G_P3_P4", action="advance", reason="未决冲突为零")
    add("stage.exit", "P3", "scheduler", to_stage="P4")

    add("stage.enter", "P4", "scheduler", from_stage="P3")
    add("agent.statement", "P4", "A3", stance="三套方案共用同一份功能清单，只改切法",
        word_count=154)
    add("gate.evaluated", "P4", "scheduler", gate_id="G_P4_P5", action="advance",
        reason="三套功能点集合一致，比选表覆盖全部功能点")
    add("stage.exit", "P4", "scheduler", to_stage="P5")

    add("stage.enter", "P5", "scheduler", from_stage="P4")
    add("agent.statement", "P5", "A2", stance="按模块细分方案能落地，波次清晰", word_count=88)
    add("agent.statement", "P5", "A4", stance="DEV-005 依赖链过长，末波只剩 1 天缓冲", word_count=97)
    add("gate.evaluated", "P5", "scheduler", gate_id="G_P5_P6", action="rework",
        reason="加权总分 76.4 低于 80，退 P4 改切法")
    add("stage.exit", "P5", "scheduler", to_stage="P4")
    add("stage.enter", "P4", "scheduler", from_stage="P5")
    add("gate.evaluated", "P4", "scheduler", gate_id="G_P4_P5", action="advance", reason="改切法后重跑通过")
    add("stage.enter", "P5", "scheduler", from_stage="P4")
    for agent, vote, reason in (
        ("A2", "M", "覆盖度 24/25"),
        ("A3", "M", "可执行性 22/25"),
        ("A4", "S", "依赖闭环 23/25"),
        ("A5", "M", "验收可测性 13/15"),
    ):
        add("agent.vote", "P5", agent, vote=vote, reason=reason)
    add("gate.evaluated", "P5", "scheduler", gate_id="G_P5_P6", action="advance",
        reason="加权总分 83.6，标准差 6.8，无否决项")
    add("stage.exit", "P5", "scheduler", to_stage="P6")

    add("stage.enter", "P6", "scheduler", from_stage="P5")
    for draft in DRAFTS:
        add("task.dispatched", "P6", "A5", task_id=draft["task_id"], module_id=draft["module_id"],
            wave_no=next(w["wave_no"] for w in WAVES if draft["task_id"] in w["tasks"]),
            title=draft["title"])
    for draft in DRAFTS:
        add("task.completed", "P6", "A5", task_id=draft["task_id"],
            estimate_days=draft["estimate_days"])
    add("budget.charged", "P6", "scheduler", global_used=19, limit=30)
    add("gate.evaluated", "P6", "scheduler", gate_id="G_P6_P7", action="advance",
        reason="覆盖空缺为零，每卡有验收")
    add("stage.exit", "P6", "scheduler", to_stage="P7")

    add("stage.enter", "P7", "scheduler", from_stage="P6")
    add("human.decision", "P7", "human", action="sign", scope="baseline", decided_by="A6",
        signed_by="需求确认人")
    add("gate.evaluated", "P7", "scheduler", gate_id="G_P7_BASELINE", action="freeze",
        reason="已签字且产物齐、覆盖通过")
    add("stage.exit", "P7", "scheduler", to_stage=None)
    add("run.end", "P7", "scheduler", status="frozen")
    return events


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="req_builder.demo")
    parser.add_argument("--out", default="runs", help="运行产物根目录，默认 runs/")
    args = parser.parse_args(argv)

    run_dir = Path(args.out) / "R0001"
    artifact_dir = run_dir / "artifacts"
    viz_dir = run_dir / "viz"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    viz_dir.mkdir(parents=True, exist_ok=True)

    # 1) P6 结构化 + 覆盖矩阵
    ctx = StageContext(
        run_id="R0001",
        stage="P6",
        artifacts={
            "design_spec": make_artifact(SPEC, "R0001", "P1", produced_by="scheduler"),
            "solution_comparison": make_artifact({"tasks": DRAFTS}, "R0001", "P4", produced_by="solution_gen"),
            "conflict_register": make_artifact(
                {
                    "conflicts": [
                        {"conflict_id": "CFL-001", "topic_id": "task_granularity"},
                    ]
                },
                "R0001",
                "P3",
                produced_by="conflict_review",
            ),
        },
    )
    result = build_task_list(ctx)
    if result.status != "ok":
        for error in result.errors:
            print(f"[demo] {error['code']}: {error['message']}")
        return 1

    for name, artifact in result.artifacts.items():
        (artifact_dir / f"{name}.json").write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    # 2) 排期（A 的产物，这里用与波次规则一致的样例）
    schedule = make_artifact(_schedule(), "R0001", "P7", produced_by="scheduler")
    (artifact_dir / "schedule.json").write_text(
        json.dumps(schedule, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # 3) 事件流
    raw_events = build_events()
    for index, event in enumerate(raw_events, start=1):
        event["seq"] = index
        event["event_id"] = f"EVT-{index:05d}"
        event["run_id"] = "R0001"
        event["ts"] = now_iso()
        event.setdefault("budget_used", 0)

    events_path = run_dir / "events.jsonl"
    events_path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in raw_events) + "\n", encoding="utf-8"
    )

    # 4) 渲染
    events = load_events(events_path)
    artifacts = {
        "schedule": schedule,
        "task_coverage": result.artifacts["task_coverage"],
        "dev_task_list": result.artifacts["dev_task_list"],
        "design_spec": ctx.artifacts["design_spec"],
        "conflict_register": ctx.artifacts["conflict_register"],
    }
    target = render_to_file(events, viz_dir / "index.html", until_seq=None, artifacts=artifacts)

    # 5) 打印自检结论
    data = result.artifacts["dev_task_list"]["data"]
    coverage = result.artifacts["task_coverage"]["data"]
    print("[demo] 任务卡 %d 张，合计 %.1f 人天" % (data["summary"]["task_count"], data["summary"]["total_estimate_days"]))
    print("[demo] 优先级分布：%s" % data["summary"]["priority"])
    print("[demo] 覆盖率 %.1f%%，空缺 %d 条，自检 %s"
          % (coverage["coverage_rate"] * 100, coverage["gap_count"],
             "通过" if coverage["gate"]["self_check_pass"] else "未通过"))
    gate = coverage["gate"]
    print("[demo] G_P6_P7 数据面：gap_count=%d, all_have_acceptance=%s → %s"
          % (gate["gap_count"], gate["all_have_acceptance"], gate["g_p6_p7_data"]))
    print("[demo] 事件 %d 条，已输出：%s" % (len(events), target.resolve()))
    print("[demo] 回放示例：python -m req_builder.viz --run-dir %s --until 36" % run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
