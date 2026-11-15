# -*- coding: utf-8 -*-
"""读样例 -> 扫冲突 -> 裁决 -> 打分门禁，全程不调模型。

运行： python demo.py
"""

import json
import os

from rules import detect_conflicts, adjudicate, review

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    with open(os.path.join(HERE, "sample_input.json"), encoding="utf-8") as f:
        data = json.load(f)

    print("=" * 62)
    print("P3 冲突扫描（同一议题出现两种以上立场即登记）")
    print("=" * 62)
    conflicts = detect_conflicts(data["stances"])
    for c in conflicts:
        print("  %s  议题=%-16s 立场数=%d  状态=%s"
              % (c["conflict_id"], c["topic_id"], len(c["stances"]), c["status"]))
    if not conflicts:
        print("  无冲突，可进入 P4")

    print()
    print("=" * 62)
    print("三级裁决：证据优先 -> 加权投票 -> 挂起交人")
    print("=" * 62)
    for c in conflicts:
        r = adjudicate(c)
        res = r["resolution"]
        print("  %s  ->  %-18s %s" % (c["conflict_id"], r["level"], r["status"]))
        print("      胜出: %s（%s）" % (res["winner"], res["proposal"]) if res["winner"]
              else "      胜出: 无，需人工裁决")
        print("      理由: %s" % res["reason"])

    print()
    print("=" * 62)
    print("P5 五维打分与门禁 G_P5_P6")
    print("=" * 62)
    sheet = review(data["scores"], data.get("veto", False))
    for k, v in sheet["scores"].items():
        print("  %-12s %s" % (k, v))
    print("  加权总分（百分制）: %.2f" % sheet["weighted_total"])
    print("  标准差            : %.2f" % sheet["stddev"])
    print("  门禁结论          : %s" % ("通过" if sheet["pass"] else "不通过"))
    for reason in sheet["reasons"]:
        print("      - %s" % reason)


if __name__ == "__main__":
    main()
