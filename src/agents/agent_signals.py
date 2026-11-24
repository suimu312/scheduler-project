"""B 的可观测证据链。纯函数；不写盘、不计费、不替 A/D 作状态裁决。"""
from __future__ import annotations

import copy
import hashlib
import json
import re

ROLES = ("A1", "A2", "A3", "A4")
SECRET_KEYS = re.compile(r"api.?key|authorization|password|secret|token|cookie", re.I)


def redact(value):
    """导出边界脱敏；稳定产物 ID/引用不改写，不导出私有 prompt。"""
    if isinstance(value, dict):
        return {k: "[REDACTED]" if SECRET_KEYS.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if not isinstance(value, str):
        return value
    value = re.sub(r"(?i)\bBearer\s+[^\s\"',;]+", "Bearer [REDACTED]", value)
    value = re.sub(r"(?i)\b(?:sk-|ark-)[a-z0-9_-]{10,}", "[REDACTED_KEY]", value)
    value = re.sub(r"(?i)\b(?:api[_-]?key|password|secret|token)\s*[:=]\s*[^\s,;，；]+", "[REDACTED_CREDENTIAL]", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", value)
    return re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[REDACTED_PHONE]", value)


def prompt_snapshot(request):
    """只保留隔离证明与摘要；不保存原始角色材料/系统提示词。"""
    user = json.loads(request["user"])
    return {"agent_id": request["agent_id"], "step": request["step"],
            "prompt_sha256": hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            "visible_source_refs": sorted(m["source_ref"] for m in user["visible_materials"]),
            "published_statement_ids": [s["id"] for s in user.get("published_claims", [])],
            "published_qa_ids": [q["id"] for q in user.get("published_qa", [])]}


def enrich_minutes(minutes):
    """增加投票矩阵和摘要，保留已有字段以兼容 D/E。"""
    out = copy.deepcopy(minutes)
    votes = {v["agent_id"]: v for v in out.get("votes", [])}
    out["vote_matrix"] = [{"agent_id": a, "vote_ref": "discussion_minutes:" + votes[a]["id"] if a in votes else None,
                           "ballot": votes[a]["ballot"] if a in votes else "MISSING",
                           "reason": votes[a]["reason"] if a in votes else "未收到有效投票",
                           "source_refs": votes[a].get("source_refs", []) if a in votes else []} for a in ROLES]
    snapshots = out.get("extensions", {}).get("prompt_snapshots", [])
    initial = [s for s in snapshots if s["step"] == "statement"][:4]
    out["meeting_summary"] = {"issue": out.get("issue", {}), "completed_steps": out.get("completed_steps", []),
        "statement_count": len(out.get("statements", [])), "query_count": len(out.get("qa", [])),
        "vote_count": len(votes), "tally": {b: sum(v["ballot"] == b for v in votes.values()) for b in ("FOR", "AGAINST", "ABSTAIN")},
        "independent_statement_evidence": "verified_request_snapshots" if len(initial) == 4 and {s["agent_id"] for s in initial} == set(ROLES) and all(not s["published_statement_ids"] and not s["published_qa_ids"] for s in initial) else "not_recorded",
        "decision_owner": "D / scheduler；B 只记录原始陈述和票型", "suspended": bool(out.get("suspended"))}
    return out


def build_signals(minutes, register=None, run_id=""):
    """发言→引用→质询→投票，以及应由 A 接收的插嘴/挂起信号。"""
    records = []
    for kind, rows in (("statement", minutes.get("statements", [])), ("query", minutes.get("qa", [])), ("vote", minutes.get("votes", []))):
        for row in rows:
            records.append({"record_ref": "discussion_minutes:" + row["id"], "kind": kind,
                            "agent_id": row.get("agent_id", row.get("to")), "source_refs": row.get("source_refs", []),
                            "target_ref": row.get("target_ref"), "conflict_ref": row.get("conflict_ref")})
    by_id = {s["id"]: s for s in minutes.get("statements", [])}
    conflicts = []
    for c in (register or {}).get("conflicts", []):
        quotes = []
        original = c.get("extensions", {}).get("original_stances", c.get("stances", []))
        for stance in original:
            ref = stance.get("quote_ref", stance.get("source_ref", ""))
            s = by_id.get(ref.split(":", 1)[-1])
            if s:
                quotes.append({"statement_ref": ref, "agent_id": s["agent_id"], "quote": s["claim"], "source_refs": s["source_refs"]})
        conflicts.append({"conflict_ref": "conflict_register:" + c["conflict_id"], "status": c.get("status"),
                          "quotes": quotes, "traceable_to_opposing_quotes": {q["agent_id"] for q in quotes} != set() and {by_id[q["statement_ref"].split(":", 1)[-1]]["position"] for q in quotes} >= {"FOR", "AGAINST"}})
    interruptions = [{"code": s["code"], "agent_id": s.get("agent_id"), "issue_id": s.get("issue_id"),
                      "step": s.get("step"), "action": s.get("action", "scheduler_review"),
                      "signal_ref": "guard_signals:" + str(i), "requires_scheduler_action": s["code"] != "SIMILARITY_DEGRADED"}
                     for i, s in enumerate(minutes.get("guard_signals", []))]
    return redact({"schema_version": "1.0", "run_id": run_id, "stage": "P3", "records": records,
                   "conflict_evidence": conflicts, "interruptions": interruptions,
                   "privacy": {"raw_prompts_exported": False, "private_material_text_exported": False,
                               "policy": "字段凭据与常见密钥/手机号/邮箱脱敏；非任意个人信息识别器，发布前仍需审阅"}})


def validate_evidence(minutes, register, require_conflict=False):
    """W4 可复算验收；未记录隔离快照的旧产物不会被自动升级为已验证。"""
    errors = []
    statements = minutes.get("statements", [])
    votes = minutes.get("votes", [])
    if len(statements) != 4 or {s.get("agent_id") for s in statements} != set(ROLES):
        errors.append("需要 A1–A4 各一份陈述")
    if len(votes) != 4 or {v.get("agent_id") for v in votes} != set(ROLES):
        errors.append("需要 A1–A4 各一份有效投票")
    if enrich_minutes(minutes)["meeting_summary"]["independent_statement_evidence"] != "verified_request_snapshots":
        errors.append("缺少四角色互不可见的请求快照")
    report = build_signals(minutes, register)
    if require_conflict and not report["conflict_evidence"]:
        errors.append("该样例无冲突，不能证明冲突原话追溯")
    if any(not c["traceable_to_opposing_quotes"] for c in report["conflict_evidence"]):
        errors.append("冲突未追溯到对立陈述的两句原话")
    if minutes.get("suspended") or set(minutes.get("completed_steps", [])) != {"statement", "query", "vote"}:
        errors.append("讨论步骤未完整完成或已挂起")
    return errors
