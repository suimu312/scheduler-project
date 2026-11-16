"""独立宿主验证入口：调用 A 的组合适配，不在 B 模块内部互调 D。"""
import json
from dataclasses import asdict
from pathlib import Path


def write_report(result, output_path):
    """从已保存结果生成可阅读记录，不再请求模型。"""
    data = result["artifacts"]["discussion_minutes"]["data"]
    lines = ["# B 模块真实模型运行记录", "", "本次使用校园二手交易虚构样例；角色回答来自实际模型 API，不是离线脚本。", "",
             "议题：" + data["issue"]["description"], "", "## 独立陈述", ""]
    for stance in data["statements"]:
        lines.append(f"- {stance['agent_id']} / {stance['position']}：{stance['claim']}")
        lines.append("  证据：" + "、".join(stance["source_refs"]))
    lines += ["", "## 定向质询", ""]
    if not data["qa"]:
        lines.append("本次未发现 FOR/AGAINST 对立立场，按规则跳过提问；没有虚构问答。")
    for qa in data["qa"]:
        lines += [f"- {qa['from']} → {qa['to']}：{qa['question']}", "  回答：" + qa["answer"]]
    lines += ["", "## 投票", ""]
    for vote in data["votes"]:
        lines.append(f"- {vote['agent_id']} / {vote['ballot']}：{vote['reason']}")
    lines += ["", f"调用次数：{result['budget_used']}。状态：{result['status']}。", "",
              "embedding 尚未接入，本次执行字面相似度检测，报告明确标记 lexical_degraded。", "",
              "这次验证覆盖 B 的真实讨论与 A 的网关/信封接入，不代替全组 P1–P7 验收。", ""]
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")


def live(input_path, output_path):
    from src.scheduler.agents_bridge import run
    from src.scheduler.context import BudgetLedger, StageContext
    from .providers import SchedulerGatewayModel
    from .__main__ import dump
    supplied = json.loads(Path(input_path).read_text(encoding="utf-8-sig"))
    ctx = StageContext(run_id=supplied["run_id"], stage="P3", round_index=supplied["config"].get("round", 1),
                       dispatch_plan=None, artifacts=supplied["artifacts"], config={"agents": supplied["config"]},
                       ledger=BudgetLedger(global_limit=supplied["budget_left"], per_stage_limit={"P3": supplied["budget_left"]}))
    # 先验证账号配置；缺失时不创建看似成功的 live 目录。
    model = SchedulerGatewayModel.from_env("P3")
    result = run(ctx, gateway=model.gateway)
    out = Path(output_path)
    dump(out / "stage_result.json", asdict(result))
    for name, item in result.artifacts.items():
        dump(out / (name + ".json"), item)
    dump(out / "gateway_usage.json", model.gateway.usage())
    if "discussion_minutes" in result.artifacts:
        write_report(asdict(result), out / "运行记录.md")
    if result.status != "ok":
        raise SystemExit("真实调用未完成：" + json.dumps(result.errors, ensure_ascii=False) + "。详情已保存到 " + str(out))
    print(f"Real model run complete; calls={result.budget_used}; output={out.resolve()}")
