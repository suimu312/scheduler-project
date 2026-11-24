# -*- coding: utf-8 -*-
"""P3 冲突裁决与 P5 对抗式评审的全部判定规则。

设计约束（见 docs/review-conflict.md）：
1. 全部为纯函数：不调模型、不读时钟、不写文件、不依赖本包之外的模块；
2. 只返回判定结果，落盘与事件由编排器 A 负责；
3. 加权总分必须归一化到百分制（四维权重和为 90，成本只估算不计分）；
4. 字段一律先过 models 的归一，禁止在规则里直接比较 "FOR" / "for" 这种原始值。

对外函数（与设计 §2.8 一一对应）：
    detect_conflicts(statements)       -> list[dict]     P3 冲突扫描
    adjudicate(conflict)               -> dict           P3 三级裁决
    validate_challenge(challenge)      -> list[str]      质疑四要素校验
    find_veto_items(tasks)             -> list[dict]     否决项判定
    review(scores, ...)                -> ScoreSheet     P5 打分 + 门禁 + 回炉分流

P5 的评审依据从产物里来（见文末「产物驱动的评审依据」一节）：
    review_scope(comparison, tasks)    -> dict           这次审了哪些功能点/方案/任务卡
    oversize_items_from_rows(...)      -> list[dict]     无任务卡时按方案层估算判粒度
    score_from_artifacts(comparison)   -> dict           四维分 + 每维依据 + 算不出的维度
    challenges_from_findings(...)      -> list[dict]     把扫出的硬伤翻成四要素齐全的质疑
"""

from __future__ import annotations

import re
from statistics import pstdev
from typing import Any, Iterable

from .models import (
    DIM_WEIGHTS,
    SEVERITIES,
    Challenge,
    Conflict,
    ScoreSheet,
    Stance,
    canon_dim,
)

# 二级裁决权重：用户代表与质量评审员的诉求被牺牲后返工代价最大
VOTE_WEIGHT: dict[str, float] = {"A1": 1.0, "A2": 1.5, "A3": 1.0, "A4": 1.5, "A5": 0.5, "A6": 0.5}

PASS_TOTAL = 80.0
STD_LIMIT = 12.0
VOTE_MARGIN = 1.0
MAX_TASK_DAYS = 3.0
MAX_P5_REVISIONS = 3
CHALLENGE_REJECT_LIMIT = 0.6
# 四维里至少这么多维有分才允许过闸：靠一两个维度凑出 80 分不算评审通过。
MIN_SCORED_DIMS = 3

_ID_RE = re.compile(r"[A-Z]{2,5}-\d+")


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 打分（P5）

def canon_scores(scores: dict[str, Any] | None) -> dict[str, float]:
    """任意写法 → 四维标准名；成本与未知维度丢弃，缺维度不补。"""
    out: dict[str, float] = {}
    for key, value in (scores or {}).items():
        dim = canon_dim(key)
        num = _num(value)
        if dim and num is not None and dim not in out:
            out[dim] = num
    return out


def weighted_total(scores: dict[str, Any]) -> float | None:
    """四个维度原始分 -> 百分制总分。

    raw = Σ(score × weight)，权重和为 90，因此除以 90 即得百分制。
    注意不要再乘 100：那样会得到 8550 这种量级，门禁阈值 80 就永远成立。
    """
    dims = canon_scores(scores)
    if not dims:
        return None
    denom = sum(DIM_WEIGHTS[d] for d in dims)
    raw = sum(dims[d] * DIM_WEIGHTS[d] for d in dims)
    return round(raw / denom, 2)


def stddev(scores: dict[str, Any]) -> float | None:
    """四维原始分的总体标准差（不含成本、不用加权分），衡量「有维度严重分歧」。"""
    dims = canon_scores(scores)
    if len(dims) < 2:
        return None
    return round(pstdev(list(dims.values())), 2)


def gate(
    scores: dict[str, Any],
    veto: bool = False,
    veto_items: Iterable[dict[str, Any]] | None = None,
    min_dims: int = MIN_SCORED_DIMS,
) -> dict[str, Any]:
    """G_P5_P6：总分 >= 80、无否决项、标准差 < 12，三条同时满足才放行。

    再加一条底线：有分数的维度不少于 min_dims。缺的维度不补分（不编数），
    但也绝不允许「只有一维有分、总分照样 88」这种通过 —— 那不是评审，是凑数。
    """
    veto_items = list(veto_items or [])
    dims = canon_scores(scores)
    total = weighted_total(scores)
    sd = stddev(scores)
    reasons: list[str] = []

    if total is None:
        reasons.append("缺少可用的维度分数，无法计算总分")
    else:
        if total < PASS_TOTAL:
            reasons.append("加权总分 %.2f < %.0f" % (total, PASS_TOTAL))
        if len(dims) < min_dims:
            reasons.append("只有 %d 个维度有分数（至少 %d 个才能过闸），本次评审判不动"
                           % (len(dims), min_dims))
    if veto or veto_items:
        codes = sorted({str(v.get("code") or "veto") for v in veto_items}) or ["veto"]
        reasons.append("存在阻断级否决项：" + "、".join(codes))
    if sd is not None and sd >= STD_LIMIT:
        reasons.append("标准差 %.2f >= %.0f（有维度分歧过大）" % (sd, STD_LIMIT))

    return {
        "pass": not reasons,
        "weighted_total": total,
        "stddev": sd,
        "scored_dims": len(dims),
        "veto": bool(veto or veto_items),
        "veto_items": veto_items,
        "reasons": reasons,
    }


# ---------------------------------------------------------------- 质疑四要素（P5）

def validate_challenge(challenge: Challenge | dict[str, Any], seq: int = 1) -> list[str]:
    """质疑四要素校验。返回错误列表，空列表即合格。

    设计要求：质疑不能是「整体感觉不太好」，必须说清 对象、问题、后果、归属。
    """
    ch = Challenge.from_any(challenge, seq)
    errors: list[str] = []
    if len(ch.description) < 8:
        errors.append("description 太短（<8 字），不构成可核查的问题描述")
    if not _ID_RE.search(ch.impact_ref):
        errors.append("impact_ref 未带对象 ID（应形如 FEA-001 / TASK-0001）")
    if len(ch.consequence) < 4:
        errors.append("consequence 缺失，未说明不改的后果")
    if ch.category not in ("solution", "requirement"):
        errors.append("category 必须是 solution 或 requirement")
    if ch.severity not in SEVERITIES:
        errors.append("severity 必须是 minor / major / blocking")
    return errors


def triage_challenges(challenges: Iterable[Any]) -> dict[str, Any]:
    """把质疑分成受理 / 退回两堆，并算驳回率（灌水抑制）。

    驳回率 = 退回条数 / 提交条数，全部由规则算，不允许模型自评。
    """
    accepted: list[Challenge] = []
    rejected: list[dict[str, Any]] = []
    for seq, raw in enumerate(challenges or [], start=1):
        ch = Challenge.from_any(raw, seq)
        errors = validate_challenge(ch, seq)
        if errors:
            rejected.append({"challenge": ch.to_dict(), "errors": errors})
        else:
            accepted.append(ch)
    total = len(accepted) + len(rejected)
    rate = round(len(rejected) / total, 3) if total else 0.0
    guard = None
    if total and rate > CHALLENGE_REJECT_LIMIT:
        guard = {
            "signal": "challenge_reject_rate_high",
            "value": rate,
            "reason": "驳回率 %.0f%% 超过 %.0f%%，评审员可能在建无效质疑"
            % (rate * 100, CHALLENGE_REJECT_LIMIT * 100),
        }
    return {
        "accepted": accepted,
        "rejected": rejected,
        "submit_count": total,
        "reject_count": len(rejected),
        "reject_rate": rate,
        "guard": guard,
    }


# ---------------------------------------------------------------- 否决项（P5 结构硬伤）

def _dep_cycle_ids(tasks: list[dict[str, Any]]) -> list[str]:
    """Kahn 拓扑排序，剩下没出队的就是成环节点。"""
    graph: dict[str, set[str]] = {}
    for task in tasks:
        tid = str(task.get("task_id") or task.get("id") or "").strip()
        if not tid:
            continue
        deps = task.get("depends_on") or task.get("dependencies") or []
        if isinstance(deps, str):
            deps = [deps]
        graph[tid] = {str(d).strip() for d in deps if str(d).strip()}

    indeg = {tid: 0 for tid in graph}
    for tid, deps in graph.items():
        for dep in deps:
            if dep in indeg:
                indeg[tid] += 1

    ready = [t for t, d in indeg.items() if d == 0]
    seen = 0
    while ready:
        node = ready.pop()
        seen += 1
        for tid, deps in graph.items():
            if node in deps:
                indeg[tid] -= 1
                if indeg[tid] == 0:
                    ready.append(tid)
    if seen >= len(graph):
        return []
    return sorted(tid for tid, d in indeg.items() if d > 0)


def find_veto_items(
    tasks: Iterable[dict[str, Any]],
    max_days: float = MAX_TASK_DAYS,
) -> list[dict[str, Any]]:
    """否决项判定（设计 7.1 D 的第一条职责）：三条结构硬伤，命中即整单不放行。

    ① 依赖成环 —— 排期算法永远算不出波次；
    ② 有任务无验收 —— 无法判定「做完了」，基线不可冻结；
    ③ 单卡超 3 人天 —— 粒度太粗，估时不可信。
    """
    tasks = [t for t in (tasks or []) if isinstance(t, dict)]
    items: list[dict[str, Any]] = []

    cycle = _dep_cycle_ids(tasks)
    if cycle:
        items.append({
            "code": "dep_cycle",
            "severity": "blocking",
            "task_ids": cycle,
            "detail": "依赖成环：" + "、".join(cycle),
        })

    for task in tasks:
        tid = str(task.get("task_id") or task.get("id") or "?").strip()
        acceptance = task.get("acceptance") or task.get("acceptance_criteria") or []
        if isinstance(acceptance, str):
            acceptance = [acceptance]
        acceptance = [a for a in acceptance if str(a).strip()]
        if not acceptance:
            items.append({
                "code": "no_acceptance",
                "severity": "blocking",
                "task_id": tid,
                "detail": "任务 %s 没有验收标准" % tid,
            })

        days = _num(task.get("estimate_days") or task.get("estimate") or task.get("days"))
        if days is not None and days > max_days:
            items.append({
                "code": "oversize",
                "severity": "major",
                "task_id": tid,
                "detail": "任务 %s 预估 %.1f 人天 > %.0f 人天，粒度太粗" % (tid, days, max_days),
            })
    return items


# ---------------------------------------------------------------- 产物驱动的评审依据（P5）
#
# flow.yaml 给 P5 的输入只有 solution_comparison + conflict_register，dev_task_list 要到 P6
# 才产出。所以「等上游把分喂过来」这条路在真实链路上走不通：全仓库没有第二处生产
# review_input.scores，P5 拿不到分就必然 blocked —— 评审环节等于没干活。
#
# 这一节把评审依据从产物里算出来。原则只有一条：
#   能算的才算；算不出来的维度<b>不补</b>，记进 unavailable 并说明原因。
#   加权总分按实际参与维度的权重和归一（weighted_total 本来就这么做），
#   绝不为了把表填满而编一个数。

# 方案层人天估算超过「单卡上限」多少倍，才认为「这个功能本身就不可能装进一张卡」。
# 不到这个倍数时无法判断粒度（一个功能本来就该拆成几张卡），留给 P6 的任务卡去判。
ADVISORY_FACTOR = 3.0


def _task_id(task: dict[str, Any]) -> str:
    return str(task.get("task_id") or task.get("id") or "").strip()


def _deps_of(task: dict[str, Any]) -> list[str]:
    deps = task.get("depends_on") or task.get("dependencies") or []
    if isinstance(deps, str):
        deps = [deps]
    return [str(d).strip() for d in deps if str(d).strip()]


def _adopted_of(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """从一行对比里取出「采纳了哪套方案、那套方案长什么样」。"""
    adopted = row.get("adopted")
    if isinstance(adopted, str):
        adopted = {"solution_id": adopted}
    adopted = adopted if isinstance(adopted, dict) else {}
    sid = str(adopted.get("solution_id") or adopted.get("id") or "").strip()

    options = row.get("options")
    opt = options.get(sid) if isinstance(options, dict) else None
    if not isinstance(opt, dict):
        # 有的实现把采纳项直接摊在 adopted 里，没有再嵌一层 options
        keys = ("estimate_days", "cost_estimate", "cost", "days", "estimate")
        opt = adopted if any(k in adopted for k in keys) else {}
    return sid, opt


def _days_of(opt: dict[str, Any]) -> float | None:
    for key in ("estimate_days", "cost_estimate", "cost", "days", "estimate"):
        value = _num((opt or {}).get(key))
        if value is not None:
            return value
    return None


def _rows_of(comparison: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in ((comparison or {}).get("rows") or []) if isinstance(r, dict)]


def _features_of(comparison: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for f in ((comparison or {}).get("feature_items") or []) if isinstance(f, dict)]


def _is_mixed(row: dict[str, Any]) -> bool:
    """采纳项是不是「跨方案拼装」（交互取 B、实现取 A 这种）。"""
    adopted = row.get("adopted") if isinstance(row.get("adopted"), dict) else {}
    sid, _ = _adopted_of(row)
    impl = str(adopted.get("implementation_from") or "").strip()
    return bool(sid and impl and impl != sid)


def review_scope(
    comparison: dict[str, Any],
    tasks: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """清点这次评审到底覆盖了哪些上游条目。

    评审单必须能回答「你审了什么」：哪几个功能点、采纳了哪几套方案、有几张任务卡。
    否则从报告上看就只有一句「通过了」，跟没审一样。
    """
    rows = _rows_of(comparison)
    feature_ids = [str(f.get("feature_id")) for f in _features_of(comparison) if f.get("feature_id")]
    if not feature_ids:
        feature_ids = [str(r.get("feature_id")) for r in rows if r.get("feature_id")]

    pairs = [(str(r.get("feature_id") or ""), _adopted_of(r)[0]) for r in rows]
    covered = {fid for fid, sid in pairs if fid and sid}
    unadopted = sorted(({fid for fid, sid in pairs if fid and not sid}
                        | {fid for fid in feature_ids if fid not in covered}))

    tasks = [t for t in (tasks or []) if isinstance(t, dict)]
    return {
        "feature_count": len(feature_ids),
        "adopted_count": len(covered),
        "unadopted_features": unadopted,
        "solution_ids": sorted({sid for _, sid in pairs if sid}),
        "feature_ids": feature_ids,
        "task_count": len(tasks),
        "basis": "solution_comparison" + (" + 任务卡（随方案附带的 WBS 草案）" if tasks
                                          else "（P5 阶段尚无任务卡）"),
    }


def oversize_items_from_rows(
    comparison: dict[str, Any],
    max_days: float = MAX_TASK_DAYS,
    factor: float = ADVISORY_FACTOR,
) -> list[dict[str, Any]]:
    """没有任务卡时的粒度判定：用「被采纳方案对该功能的人天估算」代替单卡工时。

    这不是真正的任务分解，只能回答「这个功能本身是不是被估得太粗」，
    所以 code 仍然是 oversize，但 source 字段写明判据来自方案层，别让人误以为是任务卡。
    """
    limit = max_days * factor
    items: list[dict[str, Any]] = []
    for row in _rows_of(comparison):
        fid = str(row.get("feature_id") or "")
        sid, opt = _adopted_of(row)
        days = _days_of(opt)
        if days is None or days <= limit:
            continue
        items.append({
            "code": "oversize",
            "severity": "major",
            "feature_id": fid,
            "solution_id": sid,
            "value_days": days,
            "limit_days": limit,
            "card_limit_days": max_days,
            "source": "solution_comparison.adopted.cost_estimate",
            "detail": "功能 %s 的采纳方案（%s）估 %.1f 人天，超过单卡上限 %.0f 人天的 %.0f 倍，"
                      "说明方案层没打算把它拆开" % (fid, sid or "?", days, max_days, factor),
        })
    return items


def _graph_depth(tasks: list[dict[str, Any]]) -> int:
    """依赖图分成几层（最长依赖链上的节点数）；有环返回 0，环交给 dep_cycle 单独报。"""
    known = {_task_id(t) for t in tasks if _task_id(t)}
    graph = {_task_id(t): [d for d in _deps_of(t) if d in known] for t in tasks if _task_id(t)}
    if not graph:
        return 0

    level: dict[str, int] = {}

    def depth_of(tid: str, trail: frozenset[str]) -> int:
        if tid in level:
            return level[tid]
        if tid in trail:
            raise ValueError(tid)
        deps = graph[tid]
        trail = trail | {tid}
        level[tid] = 1 if not deps else 1 + max(depth_of(d, trail) for d in deps)
        return level[tid]

    try:
        return max(depth_of(tid, frozenset()) for tid in graph)
    except ValueError:
        return 0


def _dangling_refs(tasks: list[dict[str, Any]]) -> list[str]:
    """依赖指向不存在的任务号 —— 排期时会被悄悄忽略，波次就排少了。"""
    known = {_task_id(t) for t in tasks if _task_id(t)}
    out: list[str] = []
    for task in tasks:
        tid = _task_id(task) or "?"
        out.extend("%s→%s" % (tid, dep) for dep in _deps_of(task) if dep not in known)
    return out


def score_from_artifacts(
    comparison: dict[str, Any],
    tasks: Iterable[dict[str, Any]] | None = None,
    max_days: float = MAX_TASK_DAYS,
    suspended_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """四维分：从 P4 产物（有任务卡时一并）算出来，纯规则、可复算。

    suspended_ids 是 P3 里判不动、挂起等人工裁决的冲突编号：
    它们既压风险分，也直接决定「打不通过往哪儿退」（需求层没谈拢 → 退 P3）。

    返回 {scores, evidence, unavailable, scope, veto_items, suspended_ids}：
        scores      能算出几维就是几维
        evidence    每一维是怎么算出来的，原样进评审单（评审单不能只有分数没有理由）
        unavailable 算不出来的维度及原因（不补 0，不猜）
        veto_items  顺手扫出来的结构硬伤
    """
    comparison = comparison or {}
    tasks = [t for t in (tasks or []) if isinstance(t, dict)]
    rows = _rows_of(comparison)
    scope = review_scope(comparison, tasks)
    suspended = sorted({str(s) for s in (suspended_ids or []) if str(s).strip()})

    scores: dict[str, float] = {}
    evidence: dict[str, str] = {}
    unavailable: list[dict[str, str]] = []

    # ---- 完整性：每个功能点是不是都有被采纳的方案
    total = scope["feature_count"]
    if total:
        scores["completeness"] = round(100.0 * scope["adopted_count"] / total, 2)
        missing = scope["unadopted_features"]
        evidence["completeness"] = "已采纳方案的功能 %d / %d%s" % (
            scope["adopted_count"], total,
            ("；未采纳：" + "、".join(missing)) if missing else "")
    else:
        unavailable.append({
            "dimension": "completeness",
            "reason": "solution_comparison 里没有 feature_items / rows，无法清点功能覆盖",
        })

    # ---- 可行性：有任务卡看单卡工时，没任务卡只能按方案层估算粗判
    if tasks:
        over = [t for t in tasks if (_days_of(t) or 0) > max_days]
        scores["feasibility"] = max(0.0, round(100.0 - 25.0 * len(over), 2))
        evidence["feasibility"] = "任务卡 %d 张，单卡超 %.0f 人天 %d 张（%s）" % (
            len(tasks), max_days, len(over),
            "、".join("%s %.1fd" % (_task_id(t) or "?", _days_of(t) or 0) for t in over) or "无")
    else:
        judged = [(str(r.get("feature_id") or "?"), _days_of(_adopted_of(r)[1])) for r in rows]
        judged = [(fid, days) for fid, days in judged if days is not None]
        if judged:
            limit = max_days * ADVISORY_FACTOR
            over = [x for x in judged if x[1] > limit]
            scores["feasibility"] = max(0.0, round(100.0 - 20.0 * len(over), 2))
            evidence["feasibility"] = (
                "无任务卡，按方案层人天估算粗判：%d 个功能里有 %d 个的被采纳方案估算超过 %.0f 人天（单卡上限的 %.0f 倍）%s；"
                "逐卡粒度要等 P6 的任务卡才能判" % (
                    len(judged), len(over), limit, ADVISORY_FACTOR,
                    ("：" + "、".join("%s %.1fd" % x for x in over)) if over else ""))
        else:
            unavailable.append({
                "dimension": "feasibility",
                "reason": "方案里既没有任务卡，也没有 estimate_days / cost_estimate，无法判断粒度",
            })

    # ---- 风险：有任务卡看依赖结构；没任务卡看方案间估算差异、跨方案拼装与未收敛冲突
    if tasks:
        cycle = _dep_cycle_ids(tasks)
        dangling = _dangling_refs(tasks)
        depth = _graph_depth(tasks)
        scores["risk"] = max(0.0, round(
            100.0 - 10.0 * max(0, depth - 4) - 8.0 * len(dangling) - 25.0 * (1 if cycle else 0), 2))
        evidence["risk"] = "依赖 %d 层、悬空引用 %d 处、%s" % (
            depth, len(dangling),
            ("存在依赖环（" + "、".join(cycle) + "）") if cycle else "无环")
    else:
        totals: dict[str, float] = {}
        for row in rows:
            options = row.get("options") if isinstance(row.get("options"), dict) else {}
            for sid, opt in options.items():
                days = _days_of(opt if isinstance(opt, dict) else {})
                if days is not None:
                    totals[str(sid)] = totals.get(str(sid), 0.0) + days
        ratio = None
        if totals:
            low = min(totals.values())
            if low > 0:
                ratio = round(max(totals.values()) / low, 2)
        mixes = [str(r.get("feature_id") or "?") for r in rows if _is_mixed(r)]

        if ratio is None and not suspended:
            unavailable.append({
                "dimension": "risk",
                "reason": "方案里没有可用的人天估算，冲突也都已裁决，没有可判的风险信号",
            })
        else:
            penalty = 0.0 if ratio is None or ratio < 2 else (8.0 if ratio < 4 else 15.0)
            penalty += 6.0 * min(len(mixes), 2) + 12.0 * len(suspended)
            scores["risk"] = max(0.0, round(100.0 - penalty, 2))
            evidence["risk"] = "方案间总成本相差 %s 倍%s；跨方案拼装 %d 处%s；未收敛冲突 %d 条%s" % (
                "%.1f" % ratio if ratio is not None else "（无估算）",
                ("（" + "、".join("%s=%.0fd" % kv for kv in sorted(totals.items())) + "）") if totals else "",
                len(mixes), ("（" + "、".join(mixes) + "）") if mixes else "",
                len(suspended), ("（" + "、".join(suspended) + "）") if suspended else "")

    # ---- 可测试性：没有验收标准就无从谈起，方案层也根本没有验收标准
    if tasks:
        no_acc = [t for t in tasks
                  if not [a for a in (t.get("acceptance") or t.get("acceptance_criteria") or [])
                          if str(a).strip()]]
        scores["testability"] = max(0.0, round(100.0 * (len(tasks) - len(no_acc)) / len(tasks), 2))
        evidence["testability"] = "%d 张卡中缺验收标准 %d 张%s" % (
            len(tasks), len(no_acc),
            ("（" + "、".join(_task_id(t) or "?" for t in no_acc) + "）") if no_acc else "")
    else:
        unavailable.append({
            "dimension": "testability",
            "reason": "P5 阶段还没有任务卡（dev_task_list 由 P6 产出），验收标准无从判定，交 G_P6_TASKS 复核",
        })

    veto_items = list(find_veto_items(tasks, max_days=max_days))
    if not tasks:
        veto_items.extend(oversize_items_from_rows(comparison, max_days))

    return {
        "scores": scores,
        "evidence": evidence,
        "unavailable": unavailable,
        "scope": scope,
        "veto_items": veto_items,
        "suspended_ids": suspended,
    }


def challenges_from_findings(finding: dict[str, Any]) -> list[dict[str, Any]]:
    """把扫出来的硬伤翻成「四要素齐全」的质疑 —— 规则版评审员，不是模型。

    这样即使没有人手写质疑，评审单也不会只有分数没有意见。
    手写质疑仍然优先：由调用方按 impact_ref 去重后再合并。
    编号统一 CHG-R###（R = rule），一眼能看出是规则提的、不是人写的。

    归因决定回炉去向，所以每条都得指准了：
        方案层（oversize / 依赖成环）→ 退 P4 改切法；
        需求层（未收敛的冲突）      → 退 P3 补质询。
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(description: str, impact_ref: str, consequence: str,
            category: str, severity: str) -> None:
        key = (category, impact_ref)
        if key in seen or not impact_ref:
            return
        seen.add(key)
        out.append({
            "challenge_id": "CHG-R%03d" % (len(out) + 1),
            "description": description,
            "impact_ref": impact_ref,
            "consequence": consequence,
            "category": category,
            "severity": severity,
            "verdict": "accepted",
        })

    for item in finding.get("veto_items") or []:
        code = str(item.get("code") or "")
        ref = str(item.get("task_id") or item.get("feature_id")
                  or (item.get("task_ids") or [""])[0] or "")
        if code == "oversize":
            add(str(item.get("detail") or "单卡工时超限"), ref,
                "估时不可信，排期无法按周落地，波次也切不细", "solution", "major")
        elif code == "no_acceptance":
            add("%s 只写了做什么，没写怎么算做完，完成标准没谈拢" % (ref or "任务卡"), ref,
                "基线无法冻结，返工时无法判断改够了没有", "requirement", "blocking")
        elif code == "dep_cycle":
            add("任务依赖成环（%s），拓扑排序永远算不出波次" % (ref or "见 veto_items"), ref,
                "排期算法无法收敛，P7 拿不到可行波次", "solution", "blocking")

    for cid in finding.get("suspended_ids") or []:
        add("%s 的争议到 P5 仍未收敛，需求口径没有谈拢" % cid, cid,
            "带着未决口径去做分解，任务卡做完才发现方向错了", "requirement", "major")

    return out


# ---------------------------------------------------------------- 冲突扫描（P3）

def detect_conflicts(stances: Iterable[Any], existing: int = 0) -> list[dict[str, Any]]:
    """同一议题出现两种以上立场即登记一条冲突。

    中性/弃权不计入立场数：四个人里两个支持一个中立，仍然只有一种有效立场。
    """
    grouped: dict[str, list[Stance]] = {}
    for raw in stances or []:
        st = Stance.from_any(raw)
        key = st.topic_id or "unnamed"
        grouped.setdefault(key, []).append(st)

    conflicts: list[dict[str, Any]] = []
    seq = existing
    for topic_id, group in grouped.items():
        positions = {s.position for s in group if s.position != "neutral"}
        if len(positions) < 2:
            continue
        seq += 1
        issue_id = next(
            (s.quote_ref.split("-")[1] for s in group if s.quote_ref.count("-") >= 2), topic_id
        )
        conflicts.append(
            Conflict(
                conflict_id="CFL-%03d" % seq,
                topic_id=topic_id,
                issue_id=issue_id,
                stances=group,
            ).to_dict()
        )
    return conflicts


# ---------------------------------------------------------------- 三级裁决（P3）

def _verdict(conf: Conflict, level: str, status: str, resolution: dict[str, Any]) -> dict[str, Any]:
    return {
        "conflict_id": conf.conflict_id,
        "issue_id": conf.issue_id,
        "topic_id": conf.topic_id,
        "level": level,
        "status": status,
        "resolution": resolution,
    }


def adjudicate(conflict: Conflict | dict[str, Any]) -> dict[str, Any]:
    """三级裁决：证据优先 → 加权投票 → 挂起交人。

    一级只看「有没有可查依据」，不看谁嗓门大；
    二级按角色权重算票，边际 1.0 以下判不动；
    三级不猜，直接挂起进 HUMAN_GATE。
    """
    conf = conflict if isinstance(conflict, Conflict) else Conflict.from_dict(conflict)
    stances = [s for s in conf.stances if s.position != "neutral"]

    with_ev = [s for s in stances if s.has_evidence]
    without_ev = [s for s in stances if not s.has_evidence]

    # 一级：一方有依据、另一方只有主张
    if with_ev and without_ev:
        winner = with_ev[0]
        return _verdict(conf, "L1_evidence", "resolved", {
            "type": "evidence",
            "winner": winner.agent_id,
            "proposal": winner.claim,
            "decided_by": "rule",
            "reason": "对方无可查依据，直接采信有依据一方",
        })

    # 二级：按角色权重算票
    score = 0.0
    for s in stances:
        weight = VOTE_WEIGHT.get(s.agent_id, 1.0)
        score += weight if s.position == "for" else -weight
    score = round(score, 2)

    if abs(score) >= VOTE_MARGIN:
        side = "for" if score > 0 else "against"
        winner = next((s for s in stances if s.position == side), None)
        if winner:
            return _verdict(conf, "L2_weighted_vote", "resolved", {
                "type": "weighted_vote",
                "winner": winner.agent_id,
                "proposal": winner.claim,
                "score": score,
                "decided_by": "rule",
                "reason": "加权票差 %+.1f，达到判定线 %.1f" % (score, VOTE_MARGIN),
            })

    # 三级：判不动，挂起
    return _verdict(conf, "L3_escalate", "suspended", {
        "type": "human",
        "winner": None,
        "score": score,
        "decided_by": "human",
        "reason": "加权票差 %+.1f，未达判定线 %.1f，需人工裁决" % (score, VOTE_MARGIN),
    })


# ---------------------------------------------------------------- 回炉分流（P5）

def route_rework(
    challenges: Iterable[Any],
    revision_count: int = 0,
    limit: int = MAX_P5_REVISIONS,
) -> dict[str, Any]:
    """打分不通过时往哪儿退。

    优先级与 A 的 gate_rule.yaml 保持一致：方案层（G_P5_REWORK_SOL）先于需求层判定。
    累计修改次数到上限则不再回炉，直接交人。
    """
    challenges = [Challenge.from_any(c, i) for i, c in enumerate(challenges or [], start=1)]
    if revision_count >= limit:
        return {
            "target": "HUMAN",
            "reason": "累计修改 %d 次已达上限 %d，交人定夺" % (revision_count, limit),
        }

    solution = [c for c in challenges if c.category == "solution"]
    requirement = [c for c in challenges if c.category == "requirement"]
    if solution:
        return {"target": "P4", "reason": "方案层问题：改切法/改取舍，退 P4 重出三套"}
    if requirement:
        return {"target": "P3", "reason": "需求层问题：口径没谈拢，退 P3 补质询"}
    return {"target": "P4", "reason": "无明确归因，按方案层默认退 P4"}


# ---------------------------------------------------------------- 一步到位

def review(
    scores: dict[str, Any],
    veto: bool = False,
    veto_items: Iterable[dict[str, Any]] | None = None,
    challenges: Iterable[Any] | None = None,
    revision_count: int = 0,
    score_id: str = "SCR-001",
    evidence: dict[str, str] | None = None,
    unavailable: Iterable[dict[str, str]] | None = None,
    scope: dict[str, Any] | None = None,
    score_origin: str = "input",
    min_dims: int = MIN_SCORED_DIMS,
    triage: dict[str, Any] | None = None,
) -> ScoreSheet:
    """P5 一步到位：四要素校验 → 算分 → 门禁 → 回炉分流。

    evidence / unavailable / scope 三个参数是「评审依据」：
    分数怎么来的、哪一维没算出来、审了哪些上游条目，都要跟着评分单一起落盘。

    triage 可以由调用方传进来（它已经对「原始提交」做过一次分堆）。
    不传则对 challenges 现算 —— 但别把已受理的列表再喂回来，那样驳回数永远是 0。
    """
    dims = canon_scores(scores)
    triage = triage if isinstance(triage, dict) else triage_challenges(challenges or [])
    result = gate(scores, veto=veto, veto_items=veto_items, min_dims=min_dims)

    rework = None
    if not result["pass"]:
        rework = route_rework(triage["accepted"], revision_count)

    return ScoreSheet(
        score_id=score_id,
        scores=dims,
        weighted_total=result["weighted_total"] if result["weighted_total"] is not None else 0.0,
        stddev=result["stddev"] if result["stddev"] is not None else 0.0,
        veto=result["veto"],
        veto_items=result["veto_items"],
        challenges=triage["accepted"],
        passed=result["pass"],
        reasons=result["reasons"],
        rework_target=(rework or {}).get("target"),
        score_origin=score_origin,
        evidence=dict(evidence or {}),
        unavailable=[dict(u) for u in (unavailable or [])],
        reviewed_scope=dict(scope or {}),
        triage={
            "submit_count": triage["submit_count"],
            "reject_count": triage["reject_count"],
            "reject_rate": triage["reject_rate"],
            "guard": triage["guard"],
            "rejected": [{"challenge": r["challenge"], "errors": list(r["errors"])}
                         for r in triage["rejected"]],
        },
    )
