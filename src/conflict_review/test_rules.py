# -*- coding: utf-8 -*-
"""12 条断言，覆盖 D 模块的四类判定。

    1-4   基础规则：证据优先 / 加权投票 / 挂起 / 归一化打分（旧用例，语义不变）
    5     字段兼容：B 的 FOR + claim + source_refs 能不能直接被吃进去
    6-7   质疑四要素校验与驳回率
    8-9   否决项：依赖成环 / 无验收 / 超 3 人天
    10    回炉分流：方案层退 P4、需求层退 P3、超限交人
    11-12 集成：run(ctx) 在 P3 / P5 各跑一遍，产物带 meta 且形状能被 A 的门禁读

运行： python test_rules.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from conflict_review import (  # noqa: E402
    adjudicate,
    detect_conflicts,
    find_veto_items,
    gate,
    review,
    route_rework,
    run,
    stddev,
    triage_challenges,
    validate_challenge,
    weighted_total,
)
from conflict_review.models import Challenge  # noqa: E402

DIMS = {"completeness": 92, "feasibility": 78, "risk": 85, "testability": 88}


def st(agent, topic, position, ev=(), claim=None):
    """造一条立场；默认用旧字段名（proposal / evidence_refs）。"""
    return {"agent_id": agent, "topic_id": topic, "position": position,
            "proposal": claim or (position + "_" + agent), "evidence_refs": list(ev),
            "quote_ref": "q"}


def st_b(agent, topic, position, refs=(), claim=None):
    """造一条立场；用 B 的真实字段名（claim / source_refs / 大写立场）。"""
    return {"agent_id": agent, "topic_id": topic, "position": position,
            "claim": claim or (position + "_" + agent), "source_refs": list(refs),
            "id": "ST-%s-%s" % (topic, agent)}


# ---------------------------------------------------------------- 基础规则

def test_evidence_first():
    """一方有依据、另一方没有 -> L1，直接判，不投票。"""
    c = detect_conflicts([st("A2", "t1", "for"), st("A3", "t1", "against", ["FEA-001"])])[0]
    r = adjudicate(c)
    assert r["level"] == "L1_evidence", r
    assert r["resolution"]["winner"] == "A3", r


def test_weighted_vote():
    """双方都无依据 -> 按权重算票，A2(1.5)+A1(1.0) 对 A3(1.0) = 1.5，判定成立。"""
    c = detect_conflicts([st("A2", "t2", "for"), st("A1", "t2", "for"),
                          st("A3", "t2", "against")])[0]
    r = adjudicate(c)
    assert r["level"] == "L2_weighted_vote", r
    assert r["resolution"]["winner"] == "A2", r


def test_escalate():
    """A1(1.0) 对 A2(1.5) -> -0.5，未达 1.0，挂起交人。"""
    c = detect_conflicts([st("A1", "t3", "for"), st("A2", "t3", "against")])[0]
    r = adjudicate(c)
    assert r["level"] == "L3_escalate", r
    assert r["status"] == "suspended", r


def test_normalized_score():
    """四维权重和为 90，必须归一化到百分制；不能再乘 100。"""
    assert weighted_total(DIMS) == 85.5, weighted_total(DIMS)
    assert 0 <= stddev(DIMS) <= 100
    ok = gate(DIMS)
    assert ok["pass"] is True, ok
    bad = gate(DIMS, veto=True)
    assert bad["pass"] is False and "否决" in bad["reasons"][0], bad


# ---------------------------------------------------------------- 字段兼容

def test_b_format_compat():
    """B 的 FOR/AGAINST + claim + source_refs 必须能直接吃：大小写与字段名都做归一。"""
    stances = [st_b("A2", "t5", "FOR"), st_b("A1", "t5", "FOR"), st_b("A3", "t5", "AGAINST")]
    conflicts = detect_conflicts(stances)
    assert len(conflicts) == 1, conflicts
    conf = conflicts[0]
    assert {s["position"] for s in conf["stances"]} == {"for", "against"}, conf
    assert conf["stances"][0]["claim"], "claim 字段没被读进来"

    r = adjudicate(conf)
    assert r["level"] == "L2_weighted_vote", r
    assert r["resolution"]["winner"] == "A2", r
    assert r["resolution"]["score"] == 1.5, r


# ---------------------------------------------------------------- 质疑四要素

def test_challenge_four_elements():
    """缺对象 ID 或缺后果的质疑要退回，合格的才进评审表。"""
    bad = Challenge.from_any({"description": "整体感觉不太好"})
    errors = validate_challenge(bad)
    assert len(errors) >= 3, errors

    good = Challenge.from_any({
        "description": "第三方登录的合规审查没有排进任何任务卡",
        "impact_ref": "dev_task_list:TASK-0007",
        "consequence": "上线前才发现要过审，里程碑顺延两周",
        "severity": "blocking",
        "category": "solution",
    })
    assert validate_challenge(good) == []


def test_challenge_reject_rate():
    """驳回率由规则算：6 条里 4 条不合格 -> 0.667，触发灌水信号。"""
    items = [
        {"description": "整体感觉不太好"},
        {"description": "说不上来哪里不对"},
        {"description": "可能有问题"},
        {"description": "不太行"},
        {"description": "合规审查没排进任务卡", "impact_ref": "TASK-0007",
         "consequence": "上线顺延两周", "severity": "major", "category": "solution"},
        {"description": "验收标准写得太笼统", "impact_ref": "TASK-0002",
         "consequence": "无法判定做完了", "severity": "minor", "category": "requirement"},
    ]
    triage = triage_challenges(items)
    assert triage["submit_count"] == 6, triage
    assert triage["reject_rate"] > 0.6, triage
    assert triage["guard"]["signal"] == "challenge_reject_rate_high", triage


# ---------------------------------------------------------------- 否决项

def test_veto_dep_cycle():
    """依赖成环必须被拦下来，环上节点要报出来。"""
    tasks = [
        {"task_id": "T1", "depends_on": ["T3"], "acceptance": ["a"]},
        {"task_id": "T2", "depends_on": ["T1"], "acceptance": ["a"]},
        {"task_id": "T3", "depends_on": ["T2"], "acceptance": ["a"]},
    ]
    items = find_veto_items(tasks)
    codes = [i["code"] for i in items]
    assert "dep_cycle" in codes, items
    cycle = next(i for i in items if i["code"] == "dep_cycle")
    assert cycle["task_ids"] == ["T1", "T2", "T3"], cycle


def test_veto_task_quality():
    """无验收 + 超 3 人天，两条都要报。"""
    tasks = [
        {"task_id": "T1", "depends_on": [], "acceptance": [], "estimate_days": 1},
        {"task_id": "T2", "depends_on": ["T1"], "acceptance": ["列表页出现该商品"],
         "estimate_days": 5},
    ]
    items = find_veto_items(tasks)
    codes = sorted(i["code"] for i in items)
    assert codes == ["no_acceptance", "oversize"], items
    assert gate(DIMS, veto_items=items)["pass"] is False


# ---------------------------------------------------------------- 回炉分流

def test_route_rework():
    """方案层退 P4；只有需求层才退 P3；改满三次交人。"""
    sol = [{"description": "风险漏了合规审查", "impact_ref": "FEA-001",
            "consequence": "上线顺延", "category": "solution", "severity": "major"}]
    req = [{"description": "验收口径没谈拢", "impact_ref": "REQ-001",
            "consequence": "无法判定完成", "category": "requirement", "severity": "major"}]
    assert route_rework(sol)["target"] == "P4", route_rework(sol)
    assert route_rework(req)["target"] == "P3", route_rework(req)
    assert route_rework(sol, revision_count=3)["target"] == "HUMAN"


# ---------------------------------------------------------------- 集成

def _sample():
    with open(os.path.join(HERE, "sample_input.json"), encoding="utf-8") as f:
        return json.load(f)


def test_run_p3():
    """run(ctx) 在 P3：产物带 meta，三级裁决各出一条，挂起的进 escalated_ids。"""
    sample = _sample()
    result = run({
        "run_id": sample["run_id"],
        "stage": "P3",
        "artifacts": {"discussion_minutes": sample["discussion_minutes"]},
        "config": {},
    })
    assert result.status == "need_human", result.status
    artifact = result.artifacts["conflict_register"]
    assert artifact["meta"]["produced_by"] == "conflict_review", artifact["meta"]
    assert artifact["meta"]["stage"] == "P3", artifact["meta"]

    data = artifact["data"]
    assert len(data["conflicts"]) == 3, data["conflicts"]
    levels = sorted(c["level"] for c in data["conflicts"])
    assert levels == ["L1_evidence", "L2_weighted_vote", "L3_escalate"], levels
    assert data["escalated_ids"] == ["CFL-003"], data["escalated_ids"]
    assert data["open_count"] == 0, data["open_count"]
    assert result.budget_used == 0, "裁决是纯规则，不该计费"


def test_run_p5():
    """run(ctx) 在 P5：review_sheet 能被门禁读，否决项与回炉去向都要落进去。"""
    sample = _sample()
    result = run({
        "run_id": sample["run_id"],
        "stage": "P5",
        "artifacts": {"dev_task_list": sample["dev_task_list"]},
        "config": {"review": sample["review"]},
    })
    assert result.status == "ok", result.status
    sheet = result.artifacts["review_sheet"]["data"]

    assert sheet["weighted_total"] == 85.5, sheet["weighted_total"]
    codes = sorted(i["code"] for i in sheet["veto_items"])
    assert codes == ["dep_cycle", "no_acceptance", "oversize"], codes
    assert sheet["pass"] is False, sheet
    assert sheet["rework_target"] == "P4", sheet["rework_target"]
    # 只有一条质疑合格，另一条被四要素拦下
    assert len(sheet["challenges"]) == 1, sheet["challenges"]
    assert sheet["challenges"][0]["challenge_id"] == "CHG-001", sheet["challenges"]

    # 空转：没有打分输入时必须 blocked，而不是假装通过
    blocked = run({"run_id": "R1", "stage": "P5", "artifacts": {}, "config": {}})
    assert blocked.status == "blocked", blocked.status
    assert blocked.error_codes() == ["E_ARTIFACT_MISSING"], blocked.errors


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
            print("PASS  %s  %s" % (name, (fn.__doc__ or "").split("\n")[0]))
    print("\n%d 条全部通过" % passed)
