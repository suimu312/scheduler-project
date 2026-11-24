"""独立演示/导出器；文件写入属于此工具，不属于 B 的模块函数。"""
import argparse
import copy
import json
from pathlib import Path

from .core import artifact, build_convocation, evaluate_user_value, run_discussion


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def demo(output):
    from .examples.fixture import ScriptedModel, make_context
    output = Path(output)
    ctx = make_context()
    dump(output / "input_context.json", ctx)
    p2 = make_context("P2")
    p6 = make_context("P6")
    dump(output / "p2_input_context.json", p2)
    dump(output / "p6_input_context.json", p6)
    dump(output / "convocation_result.json", build_convocation(p2))
    dump(output / "user_value_result.json", evaluate_user_value(p6))
    events, total = [], 0
    for step in ("statement", "query", "vote"):
        ctx["config"]["protocol_step"] = step
        out = run_discussion(ctx, model=ScriptedModel())
        total += out["usage"]
        ctx["budget_left"] -= out["usage"]
        ctx["artifacts"].update(out["artifacts"])
        dump(output / (step + "_result.json"), out)
        events.extend(out["events"])
        if step == "statement":
            # 演示夹具代表 D 已完成登记，仅用于验证跨模块输入位置。
            sts = out["artifacts"]["discussion_minutes"]["data"]["statements"]
            register = artifact(ctx["run_id"], "P3", "conflict_register", {"items": [
                {"conflict_id": "CFL-001", "extensions": {"issue_id": "ISS-001"}, "topic_id": "granularity",
                 "stances": [{"source_ref": "discussion_minutes:" + s["id"]} for s in sts],
                 "status": "open", "level": "L1_evidence"}], "extensions": {"fixture_only": True}})
            register["meta"]["producer"] = "D-DEMO-FIXTURE"
            ctx["artifacts"]["conflict_register"] = register
            dump(output / "d_conflict_register_fixture.json", register)
    # seq 的分配由演示宿主模拟 A；B 自身不赋全局序号。
    for seq, event in enumerate(events, 1):
        event.update(seq=seq, run_id=ctx["run_id"], stage="P3")
    (output / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
    minutes = ctx["artifacts"]["discussion_minutes"]
    dump(output / "discussion_minutes.json", minutes)
    data = minutes["data"]
    lines = ["# B 模块离线演示记录", "", "本记录由固定脚本生成，验证 B 协议；不是实际模型推理，不代表六模块联调完成。", "",
             "议题：" + data["issue"]["description"], "", "## 独立陈述", ""]
    lines += [f"- {s['agent_id']} / {s['position']}：{s['claim']}（证据：{', '.join(s['source_refs'])}）" for s in data["statements"]]
    lines += ["", "## 定向质询", ""]
    for q in data["qa"]:
        lines += [f"- {q['from']} → {q['to']}：{q['question']}", f"  回答：{q['answer']}"]
    lines += ["", "## 原始投票", ""]
    lines += [f"- {v['agent_id']} / {v['ballot']}：{v['reason']}" for v in data["votes"]]
    lines += ["", f"调用次数：{total} / 12。语义检测未接入，已执行字面检测并标注 lexical_degraded。", "",
              "投票是原始记录，B 没有执行加权裁决。A/D 负责继续处理，不能据此宣告方案已通过。", ""]
    (output / "演示记录.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"B demo OK: {len(data['statements'])} statements, {len(data['qa'])} QA, {len(data['votes'])} votes; calls={total}; output={output.resolve()}")


def main():
    parser = argparse.ArgumentParser(description="B 模块离线验收及模型网关单步调用")
    sub = parser.add_subparsers(dest="command", required=True)
    demo_cmd = sub.add_parser("demo", help="固定脚本，不请求网络，不消耗模型额度")
    demo_cmd.add_argument("--output", default="runs/b-demo")
    run_cmd = sub.add_parser("run-step", help="使用 B_MODEL_ENDPOINT 或宿主注入适配器")
    run_cmd.add_argument("--input", required=True)
    run_cmd.add_argument("--output", required=True)
    live_cmd = sub.add_parser("live", help="通过 A 网关调用真实豆包，D 规则登记/裁决；不使用脚本回答")
    live_cmd.add_argument("--input", default=str(Path(__file__).parent / "examples" / "expected" / "input_context.json"))
    live_cmd.add_argument("--output", default="runs/b-live")
    args = parser.parse_args()
    if args.command == "demo":
        demo(args.output)
    elif args.command == "run-step":
        ctx = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
        dump(Path(args.output), run_discussion(ctx))
    else:
        from .live import live
        live(args.input, args.output)


if __name__ == "__main__":
    main()
