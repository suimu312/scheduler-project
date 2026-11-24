"""B 交付包命令：真实调用或明确标记的离线验收，均可导出脱敏证据。"""
from __future__ import annotations

import argparse
import copy
import json
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .agent_signals import build_signals, redact, validate_evidence
from .examples.fixture import ScriptedModel, make_context


def now():
    return datetime.now(timezone.utc).isoformat()


class ObservedProvider:
    """观察 provider 的真实尝试；emit 注入 A 统一出口，默认只在内存收集。"""
    def __init__(self, provider, run_id, mode, emit=None, max_calls=12):
        self.provider, self.run_id, self.mode, self.emit = provider, run_id, mode, emit
        self.max_calls, self.attempts = max_calls, 0
        self.events, self.spans, self.lock = [], [], threading.Lock()

    def __call__(self, agent_id, prompt, stage):
        from .core import AgentError
        with self.lock:
            if self.attempts >= self.max_calls:
                raise AgentError("E_BUDGET_LIMIT", "达到本次授权调用上限")
            self.attempts += 1
        span_id = uuid.uuid4().hex
        started, clock, status = now(), time.perf_counter(), "ok"
        try:
            return self.provider(agent_id, prompt, stage)
        except Exception:
            status = "error"
            raise
        finally:
            # 不写 prompt、响应、供应商异常或凭据。追溯通过产物引用和 span ID。
            span = {"schema_version": "b-agent-span-1.0", "trace_id": self.run_id,
                    "span_id": span_id, "parent_span_id": None, "run_id": self.run_id,
                    "stage": stage, "agent_id": agent_id, "start_time": started,
                    "end_time": now(), "duration_ms": round((time.perf_counter()-clock)*1000, 3),
                    "status": status, "mode": self.mode,
                    "protocol_step": json.loads(prompt).get("step")}
            event = {"run_id": self.run_id, "ts": span["end_time"], "stage": stage,
                     "event_type": "model.call" if self.mode == "real" else "fixture.call",
                     "actor": agent_id, "payload_ref": "spans:"+span_id,
                     "trace_id": self.run_id, "span_id": span_id,
                     "extensions": {"status": status, "mode": self.mode, "protocol_step": span["protocol_step"]}}
            with self.lock:
                self.spans.append(span)
                self.events.append(event)
                if self.emit:
                    self.emit(copy.deepcopy(event))


def conflict_sample():
    """虚构的安全与范围取舍，不强制模型输出某种票型。"""
    ctx = make_context()
    ctx["config"]["issue"]["description"] = "是否允许第一阶段先上线商品发布与联系功能，将统一身份校验、越权拦截和失败路径验收推迟到第二阶段？"
    texts = {
        "business": "虚构课堂案例：业务方希望第一阶段立即演示发布到联系的链路。若身份服务尚未完成，业务方倾向先交可见功能，在第二阶段补安全机制；这只是业务诉求，尚未获批准。",
        "user_feedback": "虚构访谈：学生希望尽快找到商品并联系卖方，同时担心未经认证的人看到联系方式，不接受个人信息暴露。",
        "architecture": "虚构架构约束：发布和联系接口必须依赖统一身份契约，未登录或未授权请求必须拒绝；先上线再补会导致接口返工。",
        "risk": "虚构风险与验收红线：身份校验、越权拦截和失败路径测试是一期上线前置条件，禁止作为增强项推迟到第二阶段。"
    }
    for m in ctx["artifacts"]["material_catalog"]["data"]["items"]:
        if m["category"] == "spec":
            m["text"] = "虚构设计规格：校园二手交易一期包括发布、浏览和联系。当前议题是在快速交付可见功能与一期强制安全保障之间作取舍；高级筛选不在本议题中。"
        elif m["category"] in texts:
            m["text"] = texts[m["category"]]
    return ctx


def boundary_sample():
    """同一安全要求下，仅讨论任务边界，允许业务与架构有不同合理取舍。"""
    ctx = make_context()
    ctx["config"]["issue"]["description"] = "是否把统一身份接口和安全验收纳入各业务流程任务卡内部，不单列公共前置卡，以完整用户流程作为第一批任务的唯一分解边界？"
    texts = {
        "spec": "虚构设计规格：一期支持发布、浏览和联系，必须完成身份校验与安全验收。本议题不减少功能或安全要求，只讨论公共依赖是否单列任务卡；任何方案都必须写明依赖和可验证结果。",
        "business": "虚构业务评审：团队希望用一张发布流程卡和一张联系流程卡组织首批交付；把需要的身份接口和风险验收作为卡内子任务，有助于按业务闭环确认价值。独立公共前置卡暂时没有直接面向用户的交付结果，还会增加协调成本。请从交付价值与范围管理判断该切分。",
        "user_feedback": "虚构访谈：学生按发布到联系的操作路径判断产品是否可用，不理解基础设施任务卡的完成状态；希望每张交付卡包含从入口、正常流程到失败反馈的完整可操作验收。身份和安全要求必须保留。",
        "architecture": "虚构架构评审：发布与联系共享同一个身份接口。若只放到业务卡内部，会出现公共接口没有唯一负责人、两卡互等或重复实现的风险。建议将统一身份契约明确列为独立前置卡，再让两张业务卡引用它，不能只写隐含内部依赖。",
        "risk": "虚构测试评审：跨发布与联系的越权和异常输入需要统一的负向测试集，建议单列可独立审计的安全验收卡，明确全局通过条件。只在每张业务卡中附带测试可能遗漏跨模块风险，不能以业务流程唯一分解边界限制独立验证。"
    }
    for m in ctx["artifacts"]["material_catalog"]["data"]["items"]:
        if m["category"] in texts:
            m["text"] = texts[m["category"]]
    return ctx


def write_json(path, obj):
    path.write_text(json.dumps(redact(obj), ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def verify_package(output):
    """只读复验已导出的文件，不调用模型，不依赖 acceptance 中的通过标记。"""
    output = Path(output)
    def read(name):
        return json.loads((output/name).read_text(encoding="utf-8-sig"))
    acceptance = read("acceptance.json")
    minutes = read("discussion_minutes.json")["data"]
    register = read("conflict_register.json")["data"]
    errors = validate_evidence(minutes, register, require_conflict=True)
    if read("vote_matrix.json")["data"] != minutes["vote_matrix"]:
        errors.append("独立投票矩阵与会议纪要不一致")
    actual = read("agent_signals.json")["data"]
    expected = build_signals(minutes, register, acceptance["run_id"])
    if actual != expected:
        errors.append("证据链文件与讨论及冲突记录不一致")
    events = [json.loads(s) for s in (output/"events.jsonl").read_text(encoding="utf-8").splitlines() if s]
    spans = [json.loads(s) for s in (output/"spans.jsonl").read_text(encoding="utf-8").splitlines() if s]
    kind = "model.call" if acceptance["mode"] == "real" else "fixture.call"
    calls = [e for e in events if e["event_type"] == kind]
    if not calls or len(calls) != len(spans) or {e["span_id"] for e in calls} != {s["span_id"] for s in spans}:
        errors.append("调用事件与耗时记录不能一一关联")
    if len(calls) != (acceptance["actual_model_attempts"] if acceptance["mode"] == "real" else acceptance["fixture_attempts"]):
        errors.append("调用次数与实际观测记录不一致")
    print(json.dumps({"output": str(output.resolve()), "verified": not errors, "mode": acceptance["mode"], "errors": errors}, ensure_ascii=False))
    return errors


def run_package(output, mode="offline", sample="conflict"):
    from src.scheduler.agents_bridge import run
    from src.scheduler.context import BudgetLedger, StageContext
    from src.scheduler.llm_gateway import LLMGateway
    from .providers import ChatCompletionsProvider
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("输出目录非空，请使用新的 run 目录，避免覆盖真实记录")
    supplied = boundary_sample() if sample == "boundary" else conflict_sample() if sample == "conflict" else make_context()
    run_id = ("B-REAL-" if mode == "real" else "B-OFFLINE-")+datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:6]
    supplied["run_id"] = run_id
    for a in supplied["artifacts"].values():
        a["meta"]["run_id"] = run_id
    if mode == "real":
        provider = ChatCompletionsProvider.from_env()
    else:
        provider = lambda aid, prompt, stage: json.dumps(ScriptedModel()(json.loads(prompt)), ensure_ascii=False)
    observed = ObservedProvider(provider, run_id, mode)
    gateway = LLMGateway(observed)
    ctx = StageContext(run_id=run_id, stage="P3", round_index=1, dispatch_plan=None,
        artifacts=supplied["artifacts"], config={"agents": supplied["config"]},
        ledger=BudgetLedger(global_limit=12, per_stage_limit={"P3": 12}))
    result = run(ctx, gateway=gateway)
    minutes = result.artifacts.get("discussion_minutes", {}).get("data", {})
    register = result.artifacts.get("conflict_register", {}).get("data", {})
    errors = validate_evidence(minutes, register, require_conflict=sample in ("conflict", "boundary"))
    if result.status != "ok":
        errors.append("P3 StageResult 状态非 ok")
    output.mkdir(parents=True, exist_ok=True)
    for key, value in result.artifacts.items():
        write_json(output/(key+".json"), value)
    write_json(output/"stage_result.json", asdict(result))
    write_json(output/"gateway_usage.json", gateway.usage())
    write_json(output/"input_snapshot.json", {"run_id": run_id, "issue": supplied["config"]["issue"],
        "sample": "仓库虚构校园交易材料 / "+sample, "private_material_text_exported": False})
    write_json(output/"vote_matrix.json", {"run_id": run_id, "data": minutes.get("vote_matrix", [])})
    for name, values in (("spans.jsonl", observed.spans), ("events.jsonl", observed.events+[asdict(e) for e in result.events])):
        # 单一宿主分配序号；A 集成时改为 emit，不在模块中维护全局序号。
        with (output/name).open("w", encoding="utf-8") as f:
            for i, value in enumerate(values, 1):
                f.write(json.dumps(redact({**value, "seq": i}), ensure_ascii=False)+"\n")
    acceptance = {"run_id": run_id, "mode": mode, "status": "passed" if not errors else "failed",
                  "errors": errors, "actual_model_attempts": observed.attempts if mode == "real" else 0,
                  "fixture_attempts": observed.attempts if mode != "real" else 0,
                  "evidence": build_signals(minutes, register, run_id),
                  "scope": "仅 B P3 与 A 网关 / D 规则，不代表 P1–P7 或全组 15 产物验收"}
    write_json(output/"acceptance.json", acceptance)
    lines = ["# B P3 交付记录", "", "运行模式："+mode+"。验收："+acceptance["status"]+"。", "",
        "该记录只覆盖 B 的 P3，不能作为全组真实链路通过证明。", "", "## 独立陈述", ""]
    for s in minutes.get("statements", []):
        lines += [s["agent_id"]+" / "+s["position"]+"："+s["claim"], "", "引用："+"、".join(s["source_refs"]), ""]
    lines += ["## 冲突原话追溯", ""]
    for c in acceptance["evidence"]["conflict_evidence"]:
        lines += [c["conflict_ref"], ""]
        for q in c["quotes"]:
            lines += [q["statement_ref"]+"："+q["quote"], ""]
    if not register.get("conflicts"):
        lines += ["本次没有冲突，不能据此声称冲突追溯验收通过。", ""]
    lines += ["## 投票矩阵", "", "| 角色 | 票型 | 理由 |", "|---|---|---|"]
    for v in minutes.get("vote_matrix", []):
        lines += ["| "+v["agent_id"]+" | "+v["ballot"]+" | "+v["reason"].replace("|", "／").replace("\n", " ")+" |"]
    lines += ["", "## 验收", "", "调用尝试数："+str(observed.attempts)+"；真实模式才计为模型调用。",
        "", "隔离证明："+minutes.get("meeting_summary", {}).get("independent_statement_evidence", "not_recorded"),
        "", "验收错误："+json.dumps(errors, ensure_ascii=False), "", "spans 为 B 的适配格式，需由 A/C 对齐最终 obs-schema；未冒充全组 O1 完成。", ""]
    (output/"运行记录.md").write_text(redact("\n".join(lines)), encoding="utf-8")
    print(json.dumps({"output": str(output.resolve()), "status": acceptance["status"], "mode": mode,
                      "attempts": observed.attempts, "errors": errors}, ensure_ascii=False))
    return acceptance


def main():
    parser = argparse.ArgumentParser(description="B W4 / 智能体证据链交付包")
    parser.add_argument("--mode", choices=["offline", "real"], default="offline")
    parser.add_argument("--sample", choices=["conflict", "boundary", "original"], default="conflict")
    parser.add_argument("--output", required=True)
    parser.add_argument("--verify", action="store_true", help="只读复验已保存包；不联网")
    args = parser.parse_args()
    if args.verify:
        if verify_package(args.output):
            raise SystemExit(1)
        return
    result = run_package(args.output, args.mode, args.sample)
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
