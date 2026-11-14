"""闸门函数的白名单注册表（architecture §4.2）。

全部是纯函数：只读 artifacts / ledger / state，不调用 LLM、不写盘、不产生副作用，
所以闸门判定天生不计费。gate_rule.yaml 里只写函数名 + 参数，不允许 eval。

统一签名：fn(env: GateEnv, args: dict) -> (bool, detail)
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Callable

from .context import AGENT_IDS, BudgetLedger, RunState

GateFn = Callable[["GateEnv", dict[str, Any]], tuple[bool, dict[str, Any]]]
REGISTRY: dict[str, GateFn] = {}


@dataclass
class GateEnv:
    """一次闸门求值能看到的一切（只读）。"""

    artifacts: dict[str, Any] = field(default_factory=dict)
    ledger: BudgetLedger = field(default_factory=BudgetLedger)
    state: RunState | None = None
    flow: Any = None
    config: Any = None

    def artifact(self, name: str) -> dict[str, Any]:
        art = self.artifacts.get(name)
        return art if isinstance(art, dict) else {}

    def data(self, name: str) -> dict[str, Any]:
        d = self.artifact(name).get("data")
        return d if isinstance(d, dict) else {}

    def limit(self, key: str, default: Any = None) -> Any:
        if self.config is not None and hasattr(self.config, "limit"):
            value = self.config.limit(key, default)
            return default if value is None else value
        return default

    @property
    def idea_text(self) -> str:
        if self.config is not None and getattr(self.config, "idea_text", ""):
            return str(self.config.idea_text)
        return str(self.data("idea_card").get("idea_text") or "")


def register(fn: GateFn | None = None, *, name: str | None = None) -> Any:
    """注册到白名单。可作 @register 或 @register(name="xxx") 使用。"""

    def _wrap(f: GateFn) -> GateFn:
        REGISTRY[name or f.__name__] = f
        return f

    return _wrap(fn) if fn else _wrap


def get_fn(name: str) -> GateFn:
    if name not in REGISTRY:
        raise KeyError(f"闸门函数 {name} 未注册（gate_rule.yaml 只能引用白名单函数）")
    return REGISTRY[name]


def list_fns() -> list[str]:
    return sorted(REGISTRY)


# ---------------------------------------------------------------- 归一化小工具

def _s(value: Any) -> str:
    return str(value or "").strip()


def _low(value: Any) -> str:
    return _s(value).lower()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


OPEN_STATUS = {"open", "opened", "pending", "draft"}
SETTLED_STATUS = {"resolved", "settled", "closed", "done", "adjudicated"}
SUSPENDED_STATUS = {"escalated", "suspended", "escalate", "l3", "挂起"}

# D 的文档用 id / positions / OPEN..；architecture §0.5 用 conflict_id / stances / open..
_CONFLICT_ID_KEYS = ("conflict_id", "id", "cfl_id")


def _conflict_id(conflict: dict[str, Any]) -> str:
    for key in _CONFLICT_ID_KEYS:
        if conflict.get(key):
            return _s(conflict[key])
    return ""


def _conflict_status(conflict: dict[str, Any]) -> str:
    return _low(conflict.get("status"))


def _conflict_level(conflict: dict[str, Any]) -> str:
    return _low(conflict.get("level"))


def _conflict_is_open(conflict: dict[str, Any]) -> bool:
    status = _conflict_status(conflict)
    if status in SETTLED_STATUS or status in SUSPENDED_STATUS:
        return False
    if status in OPEN_STATUS:
        return True
    # 没写 status 的视为未决：宁可拦一道，也不放未登记的分歧过去
    return status == ""


def _conflict_is_escalated(conflict: dict[str, Any]) -> bool:
    status = _conflict_status(conflict)
    level = _conflict_level(conflict)
    if status in SUSPENDED_STATUS:
        return True
    return level in {"l3", "l3_escalate", "escalate"} and status not in SETTLED_STATUS


def _conflicts(env: "GateEnv") -> list[dict[str, Any]]:
    data = env.data("conflict_register")
    raw = data.get("conflicts") or data.get("conflict_list") or []
    return [c for c in raw if isinstance(c, dict)]


def _latest_decision(state: RunState | None) -> dict[str, Any]:
    if not state or not state.human_decisions:
        return {}
    last = state.human_decisions[-1]
    return last if isinstance(last, dict) else {}


def _latest_action(state: RunState | None) -> str:
    return _low(_latest_decision(state).get("action"))


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 打分指标（兼容 D 文档与 §0.5 两套字段）

DIM_WEIGHTS = {
    "completeness": 25.0,
    "feasibility": 25.0,
    "risk": 25.0,
    "testability": 15.0,
}
DIM_KEYWORDS = {
    "completeness": ("完整", "功能", "completeness", "function"),
    "feasibility": ("可行", "技术", "feasibility", "technical"),
    "risk": ("风险", "安全", "risk", "security", "safe"),
    "testability": ("测试", "可测", "testability", "test"),
}


def _dim_of(name: Any) -> str | None:
    low = _low(name)
    for dim, keys in DIM_KEYWORDS.items():
        if any(k in low for k in keys):
            return dim
    return None


def score_metrics(env: "GateEnv") -> dict[str, Any]:
    """从 review_sheet 取（或算）总分 / 标准差 / 否决位。

    优先用 D 直接给出的 `weighted_total` / `stddev` / `veto`（architecture §0.5.5），
    缺失时回退到 D 文档的 ScoreCard 五维原始分自己算：
        weighted = (25*完整性 + 25*可行性 + 25*风险 + 15*可测试性) / 90 * 100
        stddev   = 四个原始分的总体标准差（不含成本、不含加权分）
    """
    data = env.data("review_sheet")

    total = _num(data.get("weighted_total"))
    if total is None:
        total = _num(data.get("total_score"))
    if total is None:
        total = _num(data.get("weighted"))

    raw_dims: dict[str, float] = {}
    for card in (data.get("scores") or data.get("score_card") or []):
        if not isinstance(card, dict):
            continue
        dim = _dim_of(
            card.get("dimension") or card.get("dim") or card.get("name") or card.get("scorer")
        )
        value = _num(card.get("score"))
        if dim and value is not None:
            raw_dims.setdefault(dim, value)

    if total is None and raw_dims:
        # Σ(权重 × 分数) / Σ权重：权重和是 90，所以结果仍是 0–100 分制。
        # 注意：D 文档里写的「raw / 90 * 100」多乘了 100，会让所有方案都轻松过线；
        # 正确写法是 raw / 90（成本那 10 分不计入权重和，已由分母吸收）。
        weights = sum(DIM_WEIGHTS[d] for d in raw_dims)
        raw = sum(DIM_WEIGHTS[d] * raw_dims[d] for d in raw_dims)
        total = round(raw / weights, 2) if weights else None

    stddev = _num(data.get("stddev"))
    if stddev is None:
        stddev = _num(data.get("std_dev"))
    if stddev is None and raw_dims:
        stddev = round(statistics.pstdev(list(raw_dims.values())), 2) if len(raw_dims) > 1 else 0.0

    veto = data.get("veto")
    blocker_count = data.get("blocker_count")
    if blocker_count is None:
        blocker_count = 0
        for item in (data.get("challenges") or data.get("opinions") or []):
            if not isinstance(item, dict):
                continue
            if _low(item.get("severity")) in ("blocker", "blocking", "阻断"):
                if _low(item.get("verdict") or item.get("status") or "pending") not in ("rejected", "驳回"):
                    blocker_count += 1
    if veto is None:
        veto = bool(blocker_count)

    return {
        "weighted_total": total,
        "stddev": stddev,
        "veto": bool(veto),
        "blocker_count": int(blocker_count or 0),
        "dims": raw_dims,
        "source": "explicit" if _num(data.get("weighted_total")) is not None else "computed",
    }


# ---------------------------------------------------------------- 闸门函数

@register(name="idea_valid")
def idea_valid(env: "GateEnv", args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    min_chars = int(args.get("min_chars") or env.limit("idea_min_chars", 10))
    text = env.idea_text.strip()
    return len(text) >= min_chars, {"length": len(text), "min_chars": min_chars}


@register(name="every_problem_classified")
def every_problem_classified(env: "GateEnv", args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    problems = [p for p in (env.data("problem_list").get("problems") or []) if isinstance(p, dict)]
    bad: list[str] = []
    for p in problems:
        pid = _s(p.get("problem_id") or p.get("id"))
        types = _as_list(p.get("types") or p.get("problem_types"))
        confidence = _low(p.get("confidence") or p.get("confidence_band"))
        owners = _as_list(p.get("owner_roles") or p.get("owners"))
        if not types or confidence not in ("high", "low") or not owners:
            bad.append(pid or "<no-id>")
    return (bool(problems) and not bad), {"problems": len(problems), "invalid": bad}


@register(name="low_confidence_all_hands")
def low_confidence_all_hands(env: "GateEnv", args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    problems = [p for p in (env.data("problem_list").get("problems") or []) if isinstance(p, dict)]
    lows = [p for p in problems if _low(p.get("confidence") or p.get("confidence_band")) == "low"]
    if not lows:
        return True, {"low_confidence_problems": 0}

    conv = env.data("convocation_table")
    by_problem = conv.get("by_problem") or {}
    rows = [r for r in (conv.get("rows") or []) if isinstance(r, dict)]
    bad: list[str] = []
    for p in lows:
        pid = _s(p.get("problem_id") or p.get("id"))
        entry = by_problem.get(pid) if isinstance(by_problem, dict) else None
        if entry is None:
            entry = next((r for r in rows if _s(r.get("problem_id") or r.get("id")) == pid), None)
        if not isinstance(entry, dict):
            bad.append(pid)
            continue
        on_stage = set(_as_list(entry.get("on_stage")))
        if "low_confidence_all_hands" in _low(entry.get("reason")):
            continue
        if on_stage != set(AGENT_IDS):
            bad.append(pid)
    return (not bad), {"low_confidence_problems": len(lows), "not_all_hands": bad}


@register(name="no_open_conflict")
def no_open_conflict(env: "GateEnv", args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    data = env.data("conflict_register")
    open_count = data.get("open_count")
    if not isinstance(open_count, int):
        open_count = sum(1 for c in _conflicts(env) if _conflict_is_open(c))
    round_limit = int(args.get("or_round_limit") or env.limit("max_discussion_rounds", 3))
    rounds = env.ledger.round_of("P3")
    if open_count == 0:
        return True, {"open_count": 0, "reason": "no_open_conflict", "rounds_P3": rounds}
    if rounds >= round_limit:
        return True, {"open_count": open_count, "reason": "max_rounds", "rounds_P3": rounds}
    return False, {"open_count": open_count, "reason": "open_conflicts_remain", "rounds_P3": rounds}


@register(name="has_escalated_conflict")
def has_escalated_conflict(env: "GateEnv", args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    data = env.data("conflict_register")
    escalated_ids = [_s(i) for i in (data.get("escalated_ids") or []) if i]
    flagged = [_conflict_id(c) for c in _conflicts(env) if _conflict_is_escalated(c)]
    all_ids = sorted({i for i in (set(escalated_ids) | set(flagged)) if i})
    return bool(all_ids), {"escalated": all_ids}


@register(name="round_limit_reached")
def round_limit_reached(env: "GateEnv", args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    stage = _s(args.get("stage") or (env.state.phase if env.state else "P3"))
    limit = int(args.get("limit") or env.limit("max_discussion_rounds", 3))
    rounds = env.ledger.round_of(stage)
    return rounds >= limit, {"stage": stage, "rounds": rounds, "limit": limit}


@register(name="same_feature_set")
def same_feature_set(env: "GateEnv", args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """C4：三套方案的 feature_items 必须是同一份、ID 一致、顺序一致。"""
    data = env.data("solution_comparison")
    items = [i for i in (data.get("feature_items") or []) if isinstance(i, dict)]
    ids = [_s(i.get("feature_id") or i.get("id")) for i in items]
    if not ids or any(not i for i in ids):
        return False, {"feature_ids": ids, "reason": "feature_items 缺失或含空 ID"}

    mismatch: list[str] = []
    expected_solutions = [s for s in (data.get("solutions") or []) if isinstance(s, dict)]
    for sol in expected_solutions:
        sid = _s(sol.get("solution_id") or sol.get("id"))
        feature_ids = sol.get("feature_ids")
        if feature_ids is None and isinstance(sol.get("feature_items"), list):
            feature_ids = [_s(f.get("feature_id") or f.get("id")) for f in sol["feature_items"] if isinstance(f, dict)]
        if feature_ids is None and isinstance(sol.get("feature_list"), list):
            feature_ids = [
                _s(f.get("target_req_id") or f.get("feature_id") or f.get("id"))
                for f in sol["feature_list"]
                if isinstance(f, dict)
            ]
        if feature_ids is not None and list(feature_ids) != ids:
            mismatch.append(f"{sid}:{len(list(feature_ids))}")

    rows = [r for r in (data.get("rows") or []) if isinstance(r, dict)]
    row_ids = [_s(r.get("feature_id") or r.get("id")) for r in rows]
    if rows and row_ids != ids:
        mismatch.append("rows_order_mismatch")

    return (not mismatch), {
        "feature_ids": ids,
        "feature_count": len(ids),
        "solution_ids": [_s(s.get("solution_id") or s.get("id")) for s in expected_solutions],
        "mismatch": mismatch,
    }


@register(name="comparison_rows_complete")
def comparison_rows_complete(env: "GateEnv", args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    data = env.data("solution_comparison")
    ids = [_s(i.get("feature_id") or i.get("id")) for i in (data.get("feature_items") or []) if isinstance(i, dict)]
    if not ids:
        return False, {"reason": "feature_items 为空"}
    rows = {_s(r.get("feature_id") or r.get("id")): r for r in (data.get("rows") or []) if isinstance(r, dict)}
    missing = [i for i in ids if i not in rows]
    sol_ids = [
        _s(s.get("solution_id") or s.get("id"))
        for s in (data.get("solutions") or [])
        if isinstance(s, dict)
    ] or ["SOL_A", "SOL_B", "SOL_C"]

    incomplete: list[str] = []
    for fid, row in rows.items():
        options = row.get("options")
        if isinstance(options, dict):
            have = set(options.keys())
        elif isinstance(options, list):
            have = {_s(o.get("solution_id") or o.get("id")) for o in options if isinstance(o, dict)}
        else:
            have = set()
        if not have or any(s not in have for s in sol_ids) or not (row.get("adopted") or row.get("adoption")):
            incomplete.append(fid)
    return (not missing and not incomplete), {
        "rows": len(rows),
        "missing_rows": missing,
        "incomplete_rows": sorted(set(incomplete)),
        "solutions": sol_ids,
    }


@register(name="score_pass")
def score_pass(env: "GateEnv", args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    min_total = float(args.get("min_total") or env.limit("score_pass", 80))
    max_stddev = float(args.get("max_stddev") or env.limit("score_stddev_max", 12))
    require_no_veto = bool(args.get("require_no_veto", True))
    m = score_metrics(env)
    if m["weighted_total"] is None:
        return False, {**m, "reason": "review_sheet 缺 weighted_total 且无法由五维分数推算"}
    total_ok = m["weighted_total"] >= min_total
    std_ok = (m["stddev"] is None) or (float(m["stddev"]) < max_stddev)
    veto_ok = (not require_no_veto) or (not m["veto"])
    return (total_ok and std_ok and veto_ok), {
        "total": m["weighted_total"],
        "min_total": min_total,
        "stddev": m["stddev"],
        "max_stddev": max_stddev,
        "veto": m["veto"],
        "blocker_count": m["blocker_count"],
        "total_ok": total_ok,
        "std_ok": std_ok,
        "veto_ok": veto_ok,
        "source": m["source"],
    }


@register(name="score_not_pass")
def score_not_pass(env: "GateEnv", args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    ok, detail = score_pass(env, args)
    return (not ok), detail


CATEGORY_ALIASES = {
    "solution": {"solution", "sol", "方案", "solution_level", "selection"},
    "requirement": {"requirement", "req", "需求", "requirement_level", "scope"},
}


@register(name="challenge_category_is")
def challenge_category_is(env: "GateEnv", args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """P5 失败后分流：solution 退 P4 改方案，requirement 退 P3 重开质询。

    都读不到 category 字段时按 D 文档「第 1 次失败退 C」默认判 solution，
    detail 里写明走了 fallback，方便复盘时发现 D 没打标。
    """
    category = _low(args.get("category") or "solution")
    wanted = CATEGORY_ALIASES.get(category, {category})
    data = env.data("review_sheet")
    items = [i for i in (data.get("challenges") or data.get("opinions") or []) if isinstance(i, dict)]
    hits: list[str] = []
    seen_category = False
    for item in items:
        raw = item.get("category") or item.get("category_class") or item.get("target_kind")
        text = _low(raw)
        if text:
            seen_category = True
            if any(k in text for k in wanted):
                hits.append(_s(item.get("challenge_id") or item.get("id") or item.get("ref")))
        target = _low(item.get("target"))
        if target.startswith(("solution", "sol_", "solution_comparison")):
            seen_category = True
            if category == "solution":
                hits.append(_s(item.get("challenge_id") or item.get("id") or item.get("ref")))
    if not seen_category and category == "solution":
        return True, {"category": category, "hits": [], "fallback": "no_category_field_default_to_solution"}
    return bool(hits), {"category": category, "hits": hits, "items": len(items)}


@register(name="revision_limit_reached")
def revision_limit_reached(env: "GateEnv", args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    limit = int(args.get("limit") or env.limit("max_p5_revisions", 3))
    used = env.ledger.p5_revision_count
    return used >= limit, {"p5_revision_count": used, "limit": limit}


@register(name="budget_exhausted")
def budget_exhausted(env: "GateEnv", args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """T_BUDGET 只认全局预算：单阶段超限由 step() 就地停机交闸门，不是整轮终止（§7.2）。"""
    exhausted = env.ledger.exhausted()
    detail = {
        "global_used": env.ledger.global_used,
        "global_limit": env.ledger.global_limit,
        "per_stage_used": dict(env.ledger.per_stage_used),
        "per_stage_limit": dict(env.ledger.per_stage_limit),
    }
    if not exhausted:
        detail["reason"] = "budget_remaining"
    return exhausted, detail


@register(name="rework_below_limit")
def rework_below_limit(env: "GateEnv", args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    limit = int(args.get("limit") or env.limit("max_reworks", 2))
    return env.ledger.rework_count < limit, {"rework_count": env.ledger.rework_count, "limit": limit}


@register(name="coverage_no_gap")
def coverage_no_gap(env: "GateEnv", args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    data = env.data("coverage_matrix")
    gap_count = data.get("gap_count")
    if not isinstance(gap_count, int):
        gap_count = sum(
            1
            for r in (data.get("rows") or [])
            if isinstance(r, dict) and (not (r.get("covered_by") or []) or _low(r.get("status")) == "gap")
        )
    rows_all = data.get("rows") or []
    ok = gap_count == 0 and bool(rows_all)
    return ok, {"gap_count": gap_count, "rows": len(rows_all), "coverage_rate": data.get("coverage_rate")}


@register(name="all_have_acceptance")
def all_have_acceptance(env: "GateEnv", args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """E 的硬校验由 A 自己再扫一遍：非 WONT 条目缺验收标准整份规格不得进 P7（§0.5.3）。"""
    reqs = [r for r in (env.data("requirement_spec").get("requirements") or []) if isinstance(r, dict)]
    if not reqs:
        return False, {"requirements": 0, "missing": [], "reason": "requirements 为空"}
    missing: list[str] = []
    checked = 0
    for req in reqs:
        if _low(req.get("priority")) == "wont":
            continue
        checked += 1
        acs = [a for a in _as_list(req.get("acceptance_criteria")) if a not in (None, "", [], {})]
        if not acs:
            missing.append(_s(req.get("req_id") or req.get("id")))
    return (not missing), {"requirements": len(reqs), "checked": checked, "missing": missing}


# ---------------------------------------------------------------- 人工动作

@register(name="human_signed")
def human_signed(env: "GateEnv", args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    d = _latest_decision(env.state)
    action = _low(d.get("action"))
    consumed = bool(d.get("consumed"))
    return (action == "sign" and not consumed), {"action": action, "consumed": consumed}


@register(name="human_rejected")
def human_rejected(env: "GateEnv", args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    d = _latest_decision(env.state)
    action = _low(d.get("action"))
    consumed = bool(d.get("consumed"))
    return (action == "reject" and not consumed), {"action": action, "consumed": consumed, "reason": d.get("reason")}


@register(name="human_aborted")
def human_aborted(env: "GateEnv", args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    action = _latest_action(env.state)
    return action in ("abort", "aborted"), {"action": action}


@register(name="baseline_frozen")
def baseline_frozen(env: "GateEnv", args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    data = env.data("baseline")
    frozen = bool(data.get("frozen"))
    return frozen, {"frozen": frozen, "version": data.get("version")}
