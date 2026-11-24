# -*- coding: utf-8 -*-
"""联调演示：P3 冲突扫描与裁决 → P5 评审（两条来路都要能跑）。

P5 演两遍，正好对上 flow.yaml 的两种现实：

  A. 真实链路 —— 只给 P5 声明的那两个输入（solution_comparison + conflict_register）。
     分是从产物算出来的，算不出的维度如实标注，不补分。
  B. 有任务卡时 —— 拿到 dev_task_list 后，粒度/验收/依赖那三条否决项才真正生效。

全程不调模型，输入就是 A 的产物信封与 B 的 discussion_minutes 真实格式。

运行： python demo.py          （B 段需要一次 mock 运行，见 README「快速开始」）
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))

from conflict_review import run

LINE = "=" * 66


def load_sample():
    with open(os.path.join(HERE, "sample_input.json"), encoding="utf-8") as f:
        return json.load(f)


def latest_run_artifact(name):
    """找最近一次编排运行的产物（runs/<id>/artifacts/<name>.json）。"""
    runs = os.path.join(ROOT, "runs")
    if not os.path.isdir(runs):
        return None
    dirs = [os.path.join(runs, d) for d in os.listdir(runs)]
    dirs = [d for d in dirs if os.path.isfile(os.path.join(d, "artifacts", name + ".json"))]
    if not dirs:
        return None
    newest = max(dirs, key=os.path.getmtime)
    with open(os.path.join(newest, "artifacts", name + ".json"), encoding="utf-8") as f:
        return os.path.basename(newest), json.load(f)


def print_sheet(sheet):
    origin = {"derived": "从产物算出", "explicit": "上游给的分", "input": "上游给的分"}
    print("  分数来源  : %s（%d 维有分）"
          % (origin.get(sheet["score_origin"], sheet["score_origin"]), len(sheet["scores"])))
    for dim in sheet["scores"]:
        print("    %-13s %-3s %5.1f 权重 %-2s | %s"
              % (dim["dimension"], dim["scorer"], dim["score"], dim["weight"], dim["reason"]))
    for miss in sheet["unavailable"]:
        print("    [未评]        %-13s %s" % (miss["dimension"], miss["reason"]))

    scope = sheet.get("reviewed_scope") or {}
    print("  审了什么  : %d 个功能点（采纳 %d）/ 方案 %s / 任务卡 %d 张"
          % (scope.get("feature_count", 0), scope.get("adopted_count", 0),
             "、".join(scope.get("solution_ids") or []) or "—", scope.get("task_count", 0)))
    if scope.get("unadopted_features"):
        print("    未被任何方案覆盖: %s" % "、".join(scope["unadopted_features"]))

    print("  加权总分  : %.2f（权重和 90，已归一到百分制）" % sheet["weighted_total"])
    print("  标准差    : %.2f（阈值 12）" % sheet["stddev"])
    print("  门禁结论  : %s" % ("通过" if sheet["pass"] else "不通过"))
    for reason in sheet["reasons"]:
        print("      - %s" % reason)

    print("  否决项    : %s" % ("无" if not sheet["veto_items"] else ""))
    for item in sheet["veto_items"]:
        print("      [%s] %s%s" % (item["code"], item["detail"],
                                   "（判据来自 %s）" % item["source"] if item.get("source") else ""))

    triage = sheet.get("triage") or {}
    print("  质疑      : 提交 %d 条，受理 %d 条，退回 %d 条（驳回率 %.0f%%）"
          % (triage.get("submit_count", 0), len(sheet["challenges"]),
             triage.get("reject_count", 0), (triage.get("reject_rate") or 0) * 100))
    for ch in sheet["challenges"]:
        print("      %s（%s/%s）-> %s" % (ch["challenge_id"], ch["category"],
                                         ch["severity"], ch["description"]))
    for bad in triage.get("rejected") or []:
        print("      退回 %s：%s" % (bad["challenge"]["challenge_id"], "；".join(bad["errors"])))
    print("  回炉去向  : %s" % (sheet.get("rework_target") or "无需回炉（已通过）"))


def main():
    sample = load_sample()
    run_id = sample.get("run_id", "R0001")

    print(LINE)
    print("P3  读 discussion_minutes → 冲突扫描 → 三级裁决")
    print(LINE)
    res_p3 = run({
        "run_id": run_id,
        "stage": "P3",
        "artifacts": {"discussion_minutes": sample["discussion_minutes"]},
        "config": {},
    })
    register = res_p3.artifacts["conflict_register"]["data"]
    for conf in register["conflicts"]:
        verdict = conf.get("resolution") or {}
        print("  %s  议题=%-14s %-16s -> %-5s  %s"
              % (conf["conflict_id"], conf["topic_id"], conf["level"],
                 conf["status"], verdict.get("winner") or "—"))
        print("      理由: %s" % verdict.get("reason", ""))
    stats = register.get("stats") or {}
    print()
    print("  议题 %d 个 / 立场 %d 条 / 新登记冲突 %d 条 / 未决 %d 条"
          % (stats.get("topic_count", 0), stats.get("stance_count", 0),
             stats.get("new_conflict_count", 0), register["open_count"]))
    print("  挂起交人: %s" % (", ".join(register["escalated_ids"]) or "无"))
    print("  本阶段状态: %s（budget_used=%d，裁决不调模型）" % (res_p3.status, res_p3.budget_used))

    print()
    print(LINE)
    print("P5-A 真实链路：只给 flow.yaml 声明的那两个输入")
    print(LINE)
    found = latest_run_artifact("solution_comparison")
    if not found:
        print("  跳过：runs/ 下还没有 P4 产物。先跑一次 mock（见 README「快速开始」）再回来看。")
    else:
        src_run, comparison = found
        print("  上游产物: runs/%s/artifacts/solution_comparison.json" % src_run)
        res = run({
            "run_id": run_id,
            "stage": "P5",
            "artifacts": {
                "solution_comparison": comparison,
                "conflict_register": res_p3.artifacts["conflict_register"],
            },
            "config": {},
        })
        print("  阶段状态: %s" % res.status)
        print_sheet(res.artifacts["review_sheet"]["data"])
        for g in res.guard_signals:
            print("  信号      : %s — %s" % (g.signal, g.reason))

    print()
    print(LINE)
    print("P5-B 拿到 dev_task_list 之后：粒度 / 验收 / 依赖三条否决项才真正生效")
    print(LINE)
    res_p5 = run({
        "run_id": run_id,
        "stage": "P5",
        "artifacts": {
            "dev_task_list": sample["dev_task_list"],
            "conflict_register": res_p3.artifacts["conflict_register"],
        },
        "config": {"review": sample["review"]},
    })
    sheet = res_p5.artifacts["review_sheet"]["data"]
    print_sheet(sheet)
    print("  元信息    : produced_by=%s stage=%s schema=%s"
          % (res_p5.artifacts["review_sheet"]["meta"]["produced_by"],
             res_p5.artifacts["review_sheet"]["meta"]["stage"],
             res_p5.artifacts["review_sheet"]["meta"]["schema_version"]))
    print()


if __name__ == "__main__":
    main()
