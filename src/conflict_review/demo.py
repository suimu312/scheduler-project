# -*- coding: utf-8 -*-
"""联调演示：P3 冲突扫描与裁决 → P5 四要素校验、打分、否决项、回炉分流。

全程不调模型，输入就是 B 的 discussion_minutes 真实格式。

运行： python demo.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from conflict_review import run

LINE = "=" * 66


def load_sample():
    with open(os.path.join(HERE, "sample_input.json"), encoding="utf-8") as f:
        return json.load(f)


def main():
    sample = load_sample()
    run_id = sample.get("run_id", "R0001")

    print(LINE)
    print("P3  读 discussion_minutes → 冲突扫描 → 三级裁决")
    print(LINE)
    ctx_p3 = {
        "run_id": run_id,
        "stage": "P3",
        "artifacts": {"discussion_minutes": sample["discussion_minutes"]},
        "config": {},
    }
    res_p3 = run(ctx_p3)
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
    print("P5  四要素校验 → 打分门禁 → 否决项 → 回炉分流")
    print(LINE)
    ctx_p5 = {
        "run_id": run_id,
        "stage": "P5",
        "artifacts": {
            "dev_task_list": sample["dev_task_list"],
            "conflict_register": res_p3.artifacts["conflict_register"],
        },
        "config": {"review": sample["review"]},
    }
    res_p5 = run(ctx_p5)
    sheet = res_p5.artifacts["review_sheet"]["data"]

    dims = ", ".join("%s=%s" % (s["dimension"], s["score"]) for s in sheet["scores"])
    print("  四维分数  : %s" % dims)
    print("  加权总分  : %.2f（百分制，权重和 90）" % sheet["weighted_total"])
    print("  标准差    : %.2f（阈值 12）" % sheet["stddev"])
    print("  门禁结论  : %s" % ("通过" if sheet["pass"] else "不通过"))
    for reason in sheet["reasons"]:
        print("      - %s" % reason)

    print()
    print("  否决项（结构硬伤，命中即整单不放行）：")
    if not sheet["veto_items"]:
        print("      无")
    for item in sheet["veto_items"]:
        print("      [%s] %s" % (item["code"], item["detail"]))

    print()
    print("  受理质疑 %d 条，退回 %d 条："
          % (len(sheet["challenges"]), len(sample["review"]["challenges"]) - len(sheet["challenges"])))
    for ch in sheet["challenges"]:
        print("      %s（%s/%s）-> %s" % (ch["challenge_id"], ch["category"], ch["severity"],
                                         ch["description"]))
    print("  回炉去向  : %s" % (sheet.get("rework_target") or "无需回炉（已通过）"))
    print("  元信息    : produced_by=%s stage=%s schema=%s"
          % (res_p5.artifacts["review_sheet"]["meta"]["produced_by"],
             res_p5.artifacts["review_sheet"]["meta"]["stage"],
             res_p5.artifacts["review_sheet"]["meta"]["schema_version"]))
    print()


if __name__ == "__main__":
    main()
