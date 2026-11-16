"""修订版 B：角色配置、召集、分步讨论与代码防趋同（Python >= 3.10）。"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DELIBERATIVE = ("A1", "A2", "A3", "A4")
PROCESSING = ("A5", "A6")
STEPS = ("statement", "query", "vote")
POSITIONS = ("FOR", "AGAINST", "NEUTRAL")
BALLOTS = ("FOR", "AGAINST", "ABSTAIN")
TOPICS = ("granularity", "dependency", "estimate", "acceptance")


class AgentError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(f"{code}: {message}")


def require(condition, code, message):
    if not condition:
        raise AgentError(code, message)


def load_config(path=None):
    # JSON 是 YAML 1.2 的子集，随项目配置不需要第三方依赖。
    path = Path(path) if path else ROOT / "agent_config.yaml"
    text = path.read_text(encoding="utf-8-sig")
    try:
        config = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml
        except ImportError as exc:
            raise AgentError("E_CONFIG", "普通 YAML 需要安装 PyYAML；默认配置可直接运行") from exc
        config = yaml.safe_load(text)
    validate_config(config)
    return config


def validate_config(config):
    roles = config.get("roles", {})
    require(set(roles) == set(DELIBERATIVE + PROCESSING), "E_ROLE_INVALID", "必须且仅有 A1–A6")
    require(config.get("schema_version") == "1.0", "E_ROLE_INVALID", "schema_version 必须为 1.0")
    require(set(config.get("topics", {})) == set(TOPICS), "E_ROLE_INVALID", "必须包含四类分解议题")
    banned = ("参考其他角色观点", "与大家保持一致", "忽略知识边界")
    for aid, role in roles.items():
        require(re.fullmatch(r"\d+\.\d+\.\d+", role.get("version", "")), "E_ROLE_INVALID", aid + " 缺合法版本")
        require(role.get("family") == ("deliberative" if aid in DELIBERATIVE else "processing"), "E_ROLE_INVALID", aid + " family 错误")
        for section in ("goal", "stance", "knowledge", "entry"):
            require(bool(role.get(section)), "E_ROLE_INVALID", aid + " 缺 " + section)
        knowledge = role["knowledge"]
        visible, forbidden = set(knowledge["visible"]), set(knowledge["forbidden"])
        require(not visible & forbidden, "E_ROLE_INVALID", aid + " 可见/禁见冲突")
        if aid in DELIBERATIVE:
            require(role["stance"].get("must_defend") and role["stance"].get("must_challenge"), "E_ROLE_INVALID", aid + " 缺立场约束")
            require(knowledge.get("exclusive") and forbidden, "E_ROLE_INVALID", aid + " 缺独占/禁见范围")
            require(set(knowledge["exclusive"]) <= visible, "E_ROLE_INVALID", aid + " 独占范围必须可见")
        require(not any(s in json.dumps(role, ensure_ascii=False) for s in banned), "E_ROLE_INVALID", aid + " 包含趋同指令")
        template_path = ROOT / "prompt_templates" / (aid + ".md")
        require(template_path.is_file(), "E_ROLE_INVALID", aid + " 缺提示词文件")
        template = template_path.read_text(encoding="utf-8")
        require(all(section in template for section in ("角色目标", "立场约束", "知识边界", "上场规则")), "E_ROLE_INVALID", aid + " 模板缺段")
        require(not any(s in template for s in banned), "E_ROLE_INVALID", aid + " 模板含趋同指令")
    for a, b in combinations(DELIBERATIVE, 2):
        require(set(roles[a]["knowledge"]["visible"]) != set(roles[b]["knowledge"]["visible"]), "E_ROLE_INVALID", "讨论角色必须有信息差")
    return True


def artifact(run_id, stage, name, data):
    return {"meta": {"schema_version": "1.0", "run_id": run_id, "stage": stage,
                     "producer": "B", "artifact_name": name}, "data": data}


def context(ctx, stage):
    require(isinstance(ctx, dict), "E_SCHEMA_INVALID", "StageContext 必须为对象")
    require(ctx.get("run_id") and ctx.get("stage") == stage, "E_SCHEMA_INVALID", "run_id/stage 不合法")
    require(type(ctx.get("budget_left")) is int and ctx["budget_left"] >= 0, "E_SCHEMA_INVALID", "budget_left 为非负整数")
    for name, item in ctx.get("artifacts", {}).items():
        meta = item.get("meta", {})
        require(meta.get("schema_version") == "1.0" and meta.get("run_id") == ctx["run_id"], "E_SCHEMA_INVALID", name + " 元数据或 run_id 不匹配")
        require("data" in item, "E_SCHEMA_INVALID", name + " 缺 data")


def read_artifact(ctx, name):
    require(name in ctx.get("artifacts", {}), "E_ARTIFACT_MISSING", name)
    return copy.deepcopy(ctx["artifacts"][name]["data"])


def result(artifacts, events=(), usage=0):
    return {"artifacts": artifacts, "events": list(events), "usage": usage}


def build_convocation(ctx):
    """P2：消费 A 的分类结果，不自行分类/猜测业务事实。"""
    context(ctx, "P2")
    cfg = load_config(ctx.get("config", {}).get("agent_config_path"))
    spec = read_artifact(ctx, "design_spec")
    classification = read_artifact(ctx, "classification")
    require(spec.get("features"), "E_SCHEMA_INVALID", "设计规格缺功能点")
    by_ref = {row["source_ref"]: row for row in classification["items"]}
    mapping = {"functional": "A2", "technical": "A3", "cost": "A1", "risk": "A4"}
    rows = []
    for feature in spec["features"]:
        ref = "design_spec:" + feature["feature_id"]
        require(ref in by_ref, "E_ARTIFACT_MISSING", ref + " 缺分类")
        row = by_ref[ref]
        confidence = row.get("confidence")
        require(type(confidence) in (float, int) and math.isfinite(confidence) and 0 <= confidence <= 1, "E_SCHEMA_INVALID", "置信度超范围")
        categories = row.get("categories", [])
        require(categories and set(categories) <= set(mapping), "E_SCHEMA_INVALID", "分类只能为功能/技术/成本/风险，可多选")
        require(row.get("owner_role", mapping[categories[0]]) in DELIBERATIVE + PROCESSING, "E_SCHEMA_INVALID", "责任角色编号非法")
        low = confidence < cfg["convocation"]["confidence_threshold"]
        invited = set(DELIBERATIVE + PROCESSING) if low else {"A1", "A3", "A4"} | {mapping[c] for c in categories}
        rows.append({"source_ref": ref, "categories": categories, "confidence": confidence,
                     "responsibility": row.get("responsibility", "待 A1 明确模块责任"),
                     "owner_role": row.get("owner_role", mapping[categories[0]]),
                     "invited": sorted(invited), "reason": "低置信度，全员召集" if low else "按分类召集；A3/A4 必须在场",
                     "participation": {a: "processing" if a in PROCESSING else "deliberative" for a in sorted(invited)}})
    return result({"convocation_table": artifact(ctx["run_id"], "P2", "convocation_table", {"items": rows}),
                   "role_cards": artifact(ctx["run_id"], "P2", "role_cards", {"version": cfg["version"], "roles": cfg["roles"]})})


def _material_view(ctx, aid, cfg):
    catalog = read_artifact(ctx, "material_catalog")["items"]
    refs = [m.get("source_ref") for m in catalog]
    require(all(isinstance(r, str) and r.startswith("material_catalog:") for r in refs) and len(refs) == len(set(refs)), "E_SCHEMA_INVALID", "材料引用必须唯一且带产物名前缀")
    spec = read_artifact(ctx, "design_spec")
    roots = {"design_spec:" + spec["spec_id"]}
    for collection, key in (("features", "feature_id"), ("constraints", "constraint_id"), ("modules", "module_id")):
        roots.update("design_spec:" + item[key] for item in spec.get(collection, []))
    for material in catalog:
        sources = material.get("source_refs")
        require(isinstance(sources, list) and sources and all(isinstance(r, str) and r in roots for r in sources), "E_SCHEMA_INVALID", "材料缺有效设计规格来源")
    scope = cfg["roles"][aid]["knowledge"]
    visible = {m["source_ref"]: m for m in catalog if m["category"] in scope["visible"] and m["category"] not in scope["forbidden"]}
    exclusive = {ref for ref, m in visible.items() if m["category"] in scope.get("exclusive", [])}
    require(visible and exclusive, "E_ARTIFACT_MISSING", aid + " 缺可见/独占证据材料")
    return visible, exclusive


def render_prompt(agent_id, protocol_step, ctx):
    """只渲染该角色可见材料；原始 ctx、他人陈述不会传给模型。"""
    context(ctx, "P3")
    require(agent_id in DELIBERATIVE, "E_ROLE_INVALID", "A5/A6 不参加 P3 立场与投票")
    require(protocol_step in STEPS, "E_SCHEMA_INVALID", "protocol_step 错误")
    cfg = load_config(ctx.get("config", {}).get("agent_config_path"))
    role = cfg["roles"][agent_id]
    visible, _ = _material_view(ctx, agent_id, cfg)
    issue = ctx["config"]["issue"]
    require(issue["topic_id"] in cfg["topics"], "E_SCHEMA_INVALID", "未知议题")
    # 顺序读取只来自固定、受信任的项目模板。
    common = (ROOT / "prompt_templates" / "common.md").read_text(encoding="utf-8")
    template = (ROOT / "prompt_templates" / (agent_id + ".md")).read_text(encoding="utf-8")
    user = {"issue": issue, "visible_materials": list(visible.values()),
            "step": protocol_step, "retry": ctx["config"].get("retry_instruction", "")}
    if protocol_step != "statement":
        # 仅分享已通过边界检查的主张；不跨角色泄露证据原文/引用。
        minutes = read_artifact(ctx, "discussion_minutes")
        user["published_claims"] = [{k: s[k] for k in ("id", "agent_id", "position", "claim")} for s in minutes["statements"]]
        user["question_task"] = ctx["config"].get("question_task")
        if protocol_step == "vote":
            user["published_qa"] = [{k: q[k] for k in ("id", "from", "to", "question", "answer")} for q in minutes["qa"]]
    return {"system": common + "\n" + template + "\n角色约束：\n" + json.dumps(role, ensure_ascii=False),
            "user": json.dumps(user, ensure_ascii=False), "agent_id": agent_id, "step": protocol_step}


def estimate_similarity(a, b, embedder=None):
    """语义 cosine + 字符 bigram Jaccard。无 embedding 时明确降级。"""
    def grams(text):
        text = re.sub(r"[\W_]+", "", text.lower())
        return {text[i:i + 2] for i in range(max(0, len(text) - 1))} or ({text} if text else set())
    ga, gb = grams(a), grams(b)
    lexical = len(ga & gb) / len(ga | gb) if ga | gb else 0.0
    semantic = None
    degraded = True
    if embedder:
        try:
            va, vb = embedder([a, b])
            require(len(va) == len(vb) and len(va) > 0, "E_EMBEDDING", "向量维度错误")
            require(all(type(x) in (int, float) and math.isfinite(x) for x in [*va, *vb]), "E_EMBEDDING", "向量含非有限数")
            denominator = math.sqrt(sum(x * x for x in va) * sum(x * x for x in vb))
            require(denominator > 0, "E_EMBEDDING", "零向量")
            semantic = max(0.0, min(1.0, sum(x * y for x, y in zip(va, vb)) / denominator))
            degraded = False
        except Exception:
            pass  # 服务失败仍执行字面检测，不能跳过。
    return {"semantic_score": semantic, "lexical_score": lexical, "degraded": degraded,
            "flagged": lexical >= 0.70 or (semantic is not None and semantic >= 0.85)}


def _check_response(value, aid, step, visible, exclusive, retry=False):
    require(isinstance(value, dict), "E_SCHEMA_INVALID", "模型输出必须为 JSON 对象")
    refs = value.get("source_refs", [])
    require(isinstance(refs, list) and refs and all(isinstance(r, str) and r in visible for r in refs), "E_EVIDENCE_SCOPE", "必须引用本角色可见证据，禁止伪造/越界")
    if step == "statement":
        require(value.get("position") in POSITIONS and isinstance(value.get("claim"), str) and value["claim"].strip(), "E_SCHEMA_INVALID", "缺有效立场/主张")
        if retry:
            require(bool(set(refs) & exclusive), "E_GROUPTHINK", "补发言必须引用独占证据")
        return {"agent_id": aid, "position": value["position"], "claim": value["claim"].strip(), "source_refs": refs}
    if step == "query":
        require(isinstance(value.get("answer"), str) and value["answer"].strip(), "E_EVASIVE", "必须正面回答指定问题")
        # 正确性/是否实质回避由 D 检查；代码只检查结构与引用，不能冒充语义裁决。
        return {"answer": value["answer"].strip(), "source_refs": refs}
    require(value.get("ballot") in BALLOTS and isinstance(value.get("reason"), str) and value["reason"].strip(), "E_INVALID_VOTE", "票型不合法或缺理由（含弃权）")
    return {"agent_id": aid, "ballot": value["ballot"], "reason": value["reason"].strip(), "source_refs": refs}


def run_discussion(ctx, model=None, embedder=None):
    """P3 单步执行：statement/query/vote。A 决定下一步，D 登记/裁决冲突。"""
    context(ctx, "P3")
    ctx = copy.deepcopy(ctx)
    cfg = load_config(ctx.get("config", {}).get("agent_config_path"))
    step = ctx["config"].get("protocol_step", "statement")
    require(step in STEPS, "E_SCHEMA_INVALID", "未知讨论步骤")
    issue = ctx["config"].get("issue", {})
    require(issue.get("issue_id") and issue.get("topic_id") in TOPICS and issue.get("description"), "E_SCHEMA_INVALID", "议题字段不完整")
    round_no = ctx["config"].get("round", 1)
    require(type(round_no) is int and 1 <= round_no <= 3, "E_SCHEMA_INVALID", "P3 最多三轮，由 A 控制")
    old = ctx.get("artifacts", {}).get("discussion_minutes")
    fingerprint = hashlib.sha256((json.dumps(cfg, sort_keys=True, ensure_ascii=False) + "".join(
        (ROOT / "prompt_templates" / name).read_text(encoding="utf-8")
        for name in ["common.md", *(a + ".md" for a in DELIBERATIVE + PROCESSING)]
    )).encode("utf-8")).hexdigest()
    if old:
        minutes = read_artifact(ctx, "discussion_minutes")
        require(minutes["issue"] == issue and minutes["round"] == round_no, "E_SCHEMA_INVALID", "议题/轮次不能混用")
        require(minutes["extensions"].get("config_fingerprint") == fingerprint, "E_ROLE_INVALID", "运行中禁止热替换角色配置或模板")
    else:
        require(step == "statement", "E_ARTIFACT_MISSING", "必须先有并行陈述")
        minutes = {"issue": issue, "round": round_no, "completed_steps": [], "statements": [], "qa": [], "votes": [], "similarity_reports": [], "guard_signals": [], "conflict_candidates": [], "extensions": {"template_versions": {a: r["version"] for a, r in cfg["roles"].items()}}}
        minutes["extensions"]["config_fingerprint"] = fingerprint
    require(step not in minutes["completed_steps"], "E_PROTOCOL_ORDER", "同一步骤不能重复提交；重开使用新轮次")
    if step != "statement":
        previous = "statement" if step == "query" else "query"
        require(previous in minutes["completed_steps"], "E_PROTOCOL_ORDER", "前一步未完成")
        require(not minutes.get("suspended"), "E_PROTOCOL_ORDER", "议题已挂起，需 A 重开")
    events, attempts, usage = [], 0, 0
    requested_limit = ctx["config"].get("max_calls", 12)
    require(type(requested_limit) is int and requested_limit >= 0, "E_SCHEMA_INVALID", "调用上限错误")
    limit = min(ctx["budget_left"], requested_limit)
    if model is None:
        from .providers import HttpModel
        model = HttpModel.from_env()

    def signal(code, **details):
        item = {"code": code, "issue_id": issue["issue_id"], "step": step, **details}
        minutes["guard_signals"].append(item)
        events.append({"type": "guard.signal", "payload": item})

    def invoke(jobs):
        nonlocal attempts, usage
        pending = []
        for aid, prompt in jobs:
            if attempts >= limit:
                signal("E_BUDGET_LIMIT", agent_id=aid)
                continue
            attempts += 1
            pending.append((aid, prompt))
        def call(job):
            aid, prompt = job
            try:
                response = model(prompt, timeout=cfg["discussion"]["timeout_seconds"])
                return aid, response, None
            except Exception as exc:
                return aid, None, exc
        # 每个 prompt 在提交前已固定；第一段绝不读取先返回者的输出。
        with ThreadPoolExecutor(max_workers=3) as pool:
            responses = list(pool.map(call, pending))
        output = {}
        for aid, response, error in responses:
            if isinstance(error, TimeoutError):
                signal("E_AGENT_UNAVAILABLE", agent_id=aid)
                continue  # 修订版：超时不扣模型调用预算。
            usage += 1
            if error:
                signal("E_MODEL_UNAVAILABLE", agent_id=aid, detail=type(error).__name__)
                continue
            try:
                visible, exclusive = _material_view(ctx, aid, cfg)
                output[aid] = _check_response(response, aid, step, visible, exclusive)
            except AgentError as exc:
                signal(exc.code, agent_id=aid)
        return output

    def collect(jobs):
        outputs = invoke(jobs)
        retry = []
        for aid, prompt in jobs:
            if aid not in outputs and attempts < limit:
                user = json.loads(prompt["user"])
                user["retry"] = "上次输出无效，请按协议与证据范围修正。"
                retry.append((aid, {**prompt, "user": json.dumps(user, ensure_ascii=False)}))
        if retry:
            outputs.update(invoke(retry))
        return outputs

    if step == "statement":
        views = {aid: _material_view(ctx, aid, cfg) for aid in DELIBERATIVE}
        statements = collect([(aid, render_prompt(aid, step, ctx)) for aid in DELIBERATIVE])
        # 固定角色顺序保留，避免并发调度先后影响重放结果。
        accepted = []
        for aid in DELIBERATIVE:
            if aid not in statements:
                signal("E_ABSENT", agent_id=aid)
                continue
            current = statements[aid]
            passed = False
            for retry_no in range(3):
                reports = [{"pair": [s["agent_id"], aid], **estimate_similarity(s["claim"], current["claim"], embedder)} for s in accepted]
                minutes["similarity_reports"].extend({**r, "retry": retry_no} for r in reports)
                if not any(r["flagged"] for r in reports):
                    passed = True
                    break
                signal("GROUPTHINK_FLAGGED", agent_id=aid, reports=reports)
                if retry_no == 2:
                    break
                previous_claim = current["claim"]
                retry_ctx = copy.deepcopy(ctx)
                retry_ctx["config"]["retry_instruction"] = "独立换角度陈述；至少引用一条独占材料；不能重述原观点。原文：" + previous_claim
                replacement = invoke([(aid, render_prompt(aid, step, retry_ctx))]).get(aid)
                if replacement:
                    try:
                        _check_response(replacement, aid, step, *views[aid], retry=True)
                        change = estimate_similarity(previous_claim, replacement["claim"], embedder)
                        # 降级时用字面 < 0.6，并明确记录；不伪造语义通过。
                        score = change["semantic_score"] if not change["degraded"] else change["lexical_score"]
                        require(score < 0.6, "E_GROUPTHINK", "补发言仍重复原观点")
                        minutes["similarity_reports"].append({"pair": [aid, aid], "kind": "rewrite", **change})
                        current = replacement
                    except AgentError as exc:
                        signal(exc.code, agent_id=aid)
            if passed:
                current.update(id=f"ST-{issue['issue_id']}-{round_no}-{aid}", issue_id=issue["issue_id"])
                accepted.append(current)
                events.append({"type": "agent.statement", "payload": current})
            else:
                signal("E_GROUPTHINK", agent_id=aid, action="suspend_and_rephrase_by_scheduler")
                minutes["suspended"] = True
        minutes["statements"] = accepted
        if any(r["degraded"] for r in minutes["similarity_reports"]):
            signal("SIMILARITY_DEGRADED", action="lexical_detection_active")
        for a, b in combinations(accepted, 2):
            if {a["position"], b["position"]} == {"FOR", "AGAINST"}:
                minutes["conflict_candidates"].append({"topic_id": issue["topic_id"], "source_refs": ["discussion_minutes:" + a["id"], "discussion_minutes:" + b["id"]]})
        minutes["completed_steps"].append(step)
        if len(accepted) < 3:
            minutes["suspended"] = True
    elif step == "query":
        # 必须让 D 在质询前登记冲突；B 不生成 conflict_register。
        register = read_artifact(ctx, "conflict_register")
        conflicts = [c for c in register["items"] if c["topic_id"] == issue["topic_id"] and c.get("extensions", {}).get("issue_id") == issue["issue_id"]]
        stances = {s["id"]: s for s in minutes["statements"]}
        jobs, tasks = [], {}
        for conflict in conflicts:
            refs = [s["source_ref"] for s in conflict["stances"]]
            for ref in refs:
                require(ref.startswith("discussion_minutes:") and ref.split(":", 1)[1] in stances, "E_SCHEMA_INVALID", "D 冲突引用无效")
            opposing = [stances[r.split(":", 1)[1]] for r in refs if stances[r.split(":", 1)[1]]["position"] != "NEUTRAL"]
            require({s["position"] for s in opposing} >= {"FOR", "AGAINST"}, "E_SCHEMA_INVALID", "只对对立立场发起质询")
            for target in opposing:
                aid = target["agent_id"]
                if aid in tasks:
                    continue
                challenger = next(s for s in opposing if {s["position"], target["position"]} == {"FOR", "AGAINST"})
                # 问题引用的是已经公开的主张，不能引用禁见材料原文。
                task = {"from": challenger["agent_id"], "to": aid, "target_ref": "discussion_minutes:" + target["id"],
                        "conflict_ref": "conflict_register:" + conflict["conflict_id"],
                        "question": "你提出“" + target["claim"] + "”。请说明该切分决策的适用条件、代价及可验证依据。"}
                tasks[aid] = task
                prompt_ctx = copy.deepcopy(ctx)
                prompt_ctx["config"]["question_task"] = task
                jobs.append((aid, render_prompt(aid, step, prompt_ctx)))
        for aid, response in collect(jobs).items():
            qa = {"id": f"QA-{issue['issue_id']}-{round_no}-{aid}", "issue_id": issue["issue_id"], "round": 1, **tasks[aid], **response}
            minutes["qa"].append(qa)
            events.append({"type": "agent.query", "payload": qa})
        if len(minutes["qa"]) < len(tasks):
            minutes["suspended"] = True
        minutes["completed_steps"].append(step)
    else:
        votes = collect([(aid, render_prompt(aid, step, ctx)) for aid in DELIBERATIVE])
        for aid, vote in votes.items():
            vote.update(id=f"VOTE-{issue['issue_id']}-{round_no}-{aid}", issue_id=issue["issue_id"])
            minutes["votes"].append(vote)
            events.append({"type": "agent.vote", "payload": vote})
        tally = {ballot: sum(v["ballot"] == ballot for v in votes.values()) for ballot in BALLOTS}
        minutes["tally"] = tally
        minutes["valid_vote_count"] = len(votes)
        if len(votes) < 3:
            signal("E_INSUFFICIENT_VOTES", action="suspend")
            minutes["suspended"] = True
        elif tally["FOR"] == tally["AGAINST"]:
            signal("CONTROVERSIAL", action="refer_to_D")
        # 只记录原始票型，不计算 D 的加权结果、不宣告胜方。
        minutes["completed_steps"].append(step)
    minutes["extensions"]["similarity_mode"] = "embedding_and_lexical" if embedder and not any(r["degraded"] for r in minutes["similarity_reports"]) else "lexical_degraded"
    minutes["extensions"]["model_attempts_this_step"] = attempts
    return result({"discussion_minutes": artifact(ctx["run_id"], "P3", "discussion_minutes", minutes),
                   "guard_signals": artifact(ctx["run_id"], "P3", "guard_signals", {"items": minutes["guard_signals"]})}, events, usage)


def evaluate_user_value(ctx):
    """B 的用户价值建议，不能修改 DEV 任务/排期；只消费显式业务标记。"""
    context(ctx, "P6")
    tasks = read_artifact(ctx, "dev_task_list")["items"]
    spec = read_artifact(ctx, "design_spec")
    features = {"design_spec:" + f["feature_id"]: f for f in spec["features"]}
    features.update({"design_spec:" + c["constraint_id"]: c for c in spec.get("constraints", [])})
    rows = []
    for task in tasks:
        refs = task.get("source_refs", [])
        require(refs and all(isinstance(r, str) and ":" in r for r in refs), "E_SCHEMA_INVALID", "任务缺规范来源")
        linked = [features[r] for r in refs if r in features]
        require(linked, "E_ARTIFACT_MISSING", task["task_id"] + " 缺有效 FEA/CON 来源")
        mandatory = any(f.get("mandatory") is True for f in linked)
        core = any(f.get("core_journey") is True for f in linked)
        excluded = all(f.get("out_of_scope") is True for f in linked)
        require(not(excluded and (mandatory or core)), "E_SCHEMA_INVALID", "范围标记冲突")
        priority = "W" if excluded else "M" if mandatory or core else "S" if any(f.get("user_value") == "high" for f in linked) else "C" if all(f.get("user_value") == "low" for f in linked) else None
        rows.append({"task_ref": "dev_task_list:" + task["task_id"], "source_refs": refs, "suggested_priority": priority,
                     "reason": "不在本期范围" if excluded else "强制要求或核心用户流程" if mandatory or core else "显式高用户价值" if priority == "S" else "显式低用户价值" if priority == "C" else "价值信息不足，交人确认，禁止猜测",
                     "needs_confirmation": priority is None, "acceptance_present": bool(task.get("acceptance")),
                     "granularity_warning": "单卡超过 3 人天，建议拆分，D/E 负责正式校验" if task.get("estimate_days", 0) > 3 else None})
    return result({"user_value_assessment": artifact(ctx["run_id"], "P6", "user_value_assessment", {"items": rows, "binding": False})})
