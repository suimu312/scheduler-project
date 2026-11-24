"""开发任务分解与排期（设计方案 §4 / §5.3；验收用例 A-14 / A-15 / A-16）。

这里做的是「产品侧」那套任务：设计规格 → PM Agent 分解出的开发任务（dev_task），
再由编排器排期。它和「流程侧」的执行卡（T-P1-01…T-P7-02，见 mocks.py /
architecture.md §4）是两套东西，不要混：
- dev_task 是被编排的**对象**（要做微信／要做小红书）；
- T-Px-yy 是编排器的**手段**（P1 到 P7 每个阶段派给谁）。

全部是纯函数：不调 LLM、不写盘、不读 runs/，所以排期天生不计费。
唯一的副作用函数是 write_json / render_html，只在你显式调用时写指定路径。

接口（A 侧唯一对外形状，B/C 改字段先改这里再改文档 §4.4）：
    validate_tasks(tasks, features) -> list[error]
    build_schedule(tasks, features, meta, max_wave_width) -> schedule dict
    write_json(schedule, path) -> Path
    render_html(schedule, path) -> Path
"""

from __future__ import annotations

import datetime
import json
from html import escape
from pathlib import Path
from typing import Any

TZ = datetime.timezone(datetime.timedelta(hours=8))

# dev_task 八项固定字段（设计方案 §4.4）：任务号、所属模块、标题、依赖、工时、负责人、验收、功能追溯
REQUIRED_FIELDS = (
    "task_id",
    "title",
    "module",
    "owner_role",
    "estimate_days",
    "depends_on",
    "acceptance",
    "feature_refs",
)

DEFAULT_MAX_WAVE_WIDTH = 4


# ---------------------------------------------------------------- 小工具

def _s(value: Any) -> str:
    return str(value or "").strip()


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [value]


def tasks_of(payload: Any) -> list[dict[str, Any]]:
    """dev_task_list 产物可能是 {tasks:[...]} 或直接 [...], 两种都吃。"""
    if isinstance(payload, dict):
        raw = payload.get("tasks") or payload.get("dev_tasks") or payload.get("items") or []
    else:
        raw = payload or []
    return [t for t in raw if isinstance(t, dict)]


def features_of(payload: Any) -> list[str]:
    """从 solution_comparison（或 requirement 清单）里取功能点 ID。"""
    if not isinstance(payload, dict):
        return []
    items = payload.get("feature_items") or payload.get("features") or []
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            fid = _s(item.get("feature_id") or item.get("id"))
            if fid:
                out.append(fid)
        elif _s(item):
            out.append(_s(item))
    return out


# ---------------------------------------------------------------- 校验（C1 / C2）

def validate_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """逐卡校验：八项必填、ID 唯一、依赖存在且指向自身以外、无环。

    返回 [{code, task_id, message}]；空列表即通过。code 用设计方案 §4 里的
    E_TASK_FIELD / E_TASK_DUP / E_TASK_DEP / E_DEP_CYCLE 一族。
    """
    errors: list[dict[str, Any]] = []
    if not tasks:
        return [{"code": "E_TASK_EMPTY", "task_id": "", "message": "dev_task_list 为空"}]

    seen: set[str] = set()
    ids: set[str] = set()
    for task in tasks:
        tid = _s(task.get("task_id") or task.get("id"))
        if tid:
            ids.add(tid)

    for task in tasks:
        tid = _s(task.get("task_id") or task.get("id"))
        if not tid:
            errors.append({"code": "E_TASK_FIELD", "task_id": "", "message": "缺 task_id"})
            continue
        if tid in seen:
            errors.append({"code": "E_TASK_DUP", "task_id": tid, "message": f"{tid} 重复"})
            continue
        seen.add(tid)

        for field in REQUIRED_FIELDS:
            value = task.get("task_id") if field == "task_id" else task.get(field)
            if field == "task_id":
                continue
            if field == "depends_on":
                # 空依赖是合法的：首波次的任务本来就没有前驱，只要求这个字段存在且是列表
                if not isinstance(value, (list, tuple)):
                    errors.append(
                        {"code": "E_TASK_FIELD", "task_id": tid, "message": f"{tid} 的 depends_on 必须是数组"}
                    )
                continue
            if field in ("acceptance", "feature_refs"):
                if not _as_list(value):
                    errors.append({"code": "E_TASK_FIELD", "task_id": tid, "message": f"{tid} 缺 {field}"})
                continue
            if field == "estimate_days":
                days = _num(value)
                if days is None or days <= 0:
                    errors.append(
                        {"code": "E_TASK_ESTIMATE", "task_id": tid, "message": f"{tid} 工时必须为大于 0 的数字"}
                    )
                continue
            if not _s(value):
                errors.append({"code": "E_TASK_FIELD", "task_id": tid, "message": f"{tid} 缺 {field}"})

    # 依赖合法性
    for task in tasks:
        tid = _s(task.get("task_id") or task.get("id"))
        for dep in _as_list(task.get("depends_on")):
            did = _s(dep)
            if not did:
                continue
            if did == tid:
                errors.append({"code": "E_TASK_DEP", "task_id": tid, "message": f"{tid} 依赖自己"})
            elif did not in ids:
                errors.append(
                    {"code": "E_TASK_DEP", "task_id": tid, "message": f"{tid} 依赖不存在的 {did}"}
                )

    cycle = detect_cycle(tasks)
    if cycle:
        errors.append(
            {"code": "E_DEP_CYCLE", "task_id": cycle[0], "message": "依赖成环：" + " → ".join(cycle)}
        )
    return errors


def detect_cycle(tasks: list[dict[str, Any]]) -> list[str]:
    """Kahn 拓扑剥点，剥不掉的剩余节点就是环；返回其中一条闭环（含首尾同名）。"""
    ids = [_s(t.get("task_id") or t.get("id")) for t in tasks if _s(t.get("task_id") or t.get("id"))]
    preds = {
        _s(t.get("task_id") or t.get("id")): [_s(d) for d in _as_list(t.get("depends_on"))]
        for t in tasks
        if _s(t.get("task_id") or t.get("id"))
    }
    indeg = {tid: 0 for tid in ids}
    succs: dict[str, list[str]] = {tid: [] for tid in ids}
    for tid, deps in preds.items():
        for dep in deps:
            if dep in indeg:
                indeg[tid] += 1
                succs[dep].append(tid)

    queue = [tid for tid in ids if indeg[tid] == 0]
    removed = 0
    while queue:
        cur = queue.pop(0)
        removed += 1
        for nxt in succs.get(cur, []):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)

    if removed == len(ids):
        return []

    remaining = [tid for tid in ids if indeg.get(tid, 0) > 0]
    # 从剩余节点里随便走一条回边，凑出一条可读的环
    start = remaining[0]
    path = [start]
    cur = start
    for _ in range(len(ids) + 1):
        deps = [d for d in preds.get(cur, []) if d in remaining]
        if not deps:
            break
        cur = deps[0]
        if cur == start:
            path.append(cur)
            break
        path.append(cur)
    return path


def uncovered_features(tasks: list[dict[str, Any]], features: list[str]) -> list[str]:
    """每个功能点至少要有一张 dev_task 承接，否则就是分解漏项（C 的覆盖口径）。"""
    covered: set[str] = set()
    for task in tasks:
        for ref in _as_list(task.get("feature_refs")):
            covered.add(_s(ref))
    return [f for f in features if f and f not in covered]


# ---------------------------------------------------------------- 排期五步算法（§5.3）

def build_graph(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    ids: list[str] = []
    preds: dict[str, list[str]] = {}
    succs: dict[str, list[str]] = {}
    days: dict[str, float] = {}
    for task in tasks:
        tid = _s(task.get("task_id") or task.get("id"))
        if not tid:
            continue
        ids.append(tid)
        preds[tid] = [d for d in (_s(x) for x in _as_list(task.get("depends_on"))) if d]
        succs.setdefault(tid, [])
        days[tid] = _num(task.get("estimate_days")) or 0.0
    for tid, deps in preds.items():
        for dep in deps:
            if dep in succs:
                succs[dep].append(tid)
    return {"ids": ids, "preds": preds, "succs": succs, "days": days}


def topological_waves(tasks: list[dict[str, Any]]) -> tuple[list[list[str]], dict[str, int]]:
    """第 3 步：按拓扑分层定波次。同一波次内部无依赖，可并行。"""
    g = build_graph(tasks)
    ids = g["ids"]
    level: dict[str, int] = {}
    order: list[str] = []
    indeg = {tid: len(g["preds"].get(tid, [])) for tid in ids}
    queue = [tid for tid in ids if indeg[tid] == 0]
    while queue:
        cur = queue.pop(0)
        order.append(cur)
        level[cur] = 1 + max([level.get(p, 0) for p in g["preds"].get(cur, [])] or [0])
        for nxt in g["succs"].get(cur, []):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    if len(order) != len(ids):  # 有环：波次无法定义
        return [], {}

    waves: list[list[str]] = []
    for tid in order:
        idx = level[tid] - 1
        while len(waves) <= idx:
            waves.append([])
        waves[idx].append(tid)
    return waves, level


def critical_path(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """第 4 步：CPM 正推求 ES/EF，逆推求 LS/LF 与松弛；再回溯取一条最长链。"""
    g = build_graph(tasks)
    ids = g["ids"]
    days = g["days"]
    if not ids:
        return {"task_ids": [], "total_days": 0.0, "es": {}, "ef": {}, "slack": {}, "critical_tasks": []}

    es = {tid: 0.0 for tid in ids}
    for tid in _topo_order(g):
        es[tid] = max([es.get(p, 0.0) + days.get(p, 0.0) for p in g["preds"].get(tid, [])] or [0.0])
    ef = {tid: es[tid] + days.get(tid, 0.0) for tid in ids}
    total = max(ef.values()) if ef else 0.0

    lf = {tid: total for tid in ids}
    ls: dict[str, float] = {}
    for tid in reversed(_topo_order(g)):
        succ = g["succs"].get(tid, [])
        lf[tid] = min([ls.get(s, total) for s in succ] or [total])
        ls[tid] = lf[tid] - days.get(tid, 0.0)
    slack = {tid: round(ls.get(tid, 0.0) - es.get(tid, 0.0), 2) for tid in ids}

    # 回溯一条最长链：从 EF 最大的卡出发，每步选 EF 最大的前驱
    end = max(ids, key=lambda t: (ef[t], -ids.index(t)))
    chain = [end]
    cur = end
    guard = 0
    while g["preds"].get(cur) and guard < len(ids) + 1:
        preds = g["preds"][cur]
        cur = max(preds, key=lambda p: (ef.get(p, 0.0), -ids.index(p)))
        chain.append(cur)
        guard += 1
    chain.reverse()

    return {
        "task_ids": chain,
        "total_days": round(total, 2),
        "es": {k: round(v, 2) for k, v in es.items()},
        "ef": {k: round(v, 2) for k, v in ef.items()},
        "slack": slack,
        "critical_tasks": [t for t in ids if slack.get(t, 1) == 0],
    }


def _topo_order(g: dict[str, Any]) -> list[str]:
    ids = g["ids"]
    indeg = {tid: len(g["preds"].get(tid, [])) for tid in ids}
    queue = [tid for tid in ids if indeg[tid] == 0]
    order: list[str] = []
    while queue:
        cur = queue.pop(0)
        order.append(cur)
        for nxt in g["succs"].get(cur, []):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    return order if len(order) == len(ids) else ids


def resource_conflicts(
    tasks: list[dict[str, Any]],
    waves: list[list[str]],
    leveled: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """第 5 步：同一波次里同一个负责人接了多张卡 → 资源冲突。

    冲突条目除「谁和谁撞了」之外，还必须写清顺延口径（W5 验收）：
    delayed_task 顺延哪张卡、blocked_by 被谁挡住、reason 为什么顺延、
    original_start_day / delayed_start_day / shift_days 顺延前后的开工日与天数。
    leveled 为 None 时只做识别，不产出顺延字段（兼容旧调用）。
    """
    role_of: dict[str, str] = {}
    days_of: dict[str, float] = {}
    for task in tasks:
        tid = _s(task.get("task_id") or task.get("id"))
        if tid:
            role_of[tid] = _s(task.get("owner_role")) or "unassigned"
            days_of[tid] = _num(task.get("estimate_days")) or 0.0
    out: list[dict[str, Any]] = []
    for i, wave in enumerate(waves, start=1):
        by_role: dict[str, list[str]] = {}
        for tid in wave:
            by_role.setdefault(role_of.get(tid, "unassigned"), []).append(tid)
        for role, tids in by_role.items():
            if len(tids) <= 1:
                continue
            entry: dict[str, Any] = {"wave": i, "owner_role": role, "tasks": tids, "count": len(tids)}
            if leveled:
                delayed = tids[-1]
                blocker = tids[0]
                lv = leveled.get(delayed) or {}
                entry.update(
                    {
                        "delayed_task": delayed,
                        "blocked_by": blocker,
                        "reason": f"同一负责人 {role} 在第 {i} 波并行承担 {len(tids)} 张卡，按串行顺延 {delayed}",
                        "original_start_day": round(float(lv.get("original_start") or 0.0) + 1, 2),
                        "delayed_start_day": round(float(lv.get("start") or 0.0) + 1, 2),
                        "shift_days": round(float(lv.get("shift") or 0.0), 2),
                    }
                )
            out.append(entry)
    return out


def _level_resources(
    tasks: list[dict[str, Any]],
    cp: dict[str, Any],
    level: dict[str, int],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """资源 leveling：同一负责人的卡串行排，撞车的顺延到前一张收工之后。

    返回 leveled（tid → {start, end, original_start, shift}）与冲突条目清单。
    只挪开工/收工日，不改拓扑波次与关键路径判定。
    """
    role_of: dict[str, str] = {}
    days_of: dict[str, float] = {}
    es = cp.get("es") or {}
    for task in tasks:
        tid = _s(task.get("task_id") or task.get("id"))
        if tid:
            role_of[tid] = _s(task.get("owner_role")) or "unassigned"
            days_of[tid] = _num(task.get("estimate_days")) or 0.0

    leveled: dict[str, dict[str, Any]] = {}
    by_role: dict[str, list[str]] = {}
    for tid in sorted(es.keys(), key=lambda t: (float(es.get(t, 0.0)), t)):
        role = role_of.get(tid, "unassigned")
        start = float(es.get(tid, 0.0))
        for prev in by_role.get(role, []):
            prev_end = float(leveled[prev]["end"])
            if start < prev_end:
                start = prev_end
        end = start + days_of.get(tid, 0.0)
        leveled[tid] = {
            "start": round(start, 2),
            "end": round(end, 2),
            "original_start": round(float(es.get(tid, 0.0)), 2),
            "shift": round(start - float(es.get(tid, 0.0)), 2),
        }
        by_role.setdefault(role, []).append(tid)

    # 按波次聚合冲突条目（与 resource_conflicts 同口径）
    waves: list[list[str]] = []
    for tid in leveled:
        idx = int(level.get(tid, 1)) - 1
        while len(waves) <= idx:
            waves.append([])
        waves[idx].append(tid)
    conflicts: list[dict[str, Any]] = []
    for i, wave in enumerate(waves, start=1):
        grouped: dict[str, list[str]] = {}
        for tid in wave:
            grouped.setdefault(role_of.get(tid, "unassigned"), []).append(tid)
        for role, tids in grouped.items():
            if len(tids) <= 1:
                continue
            delayed = tids[-1]
            lv = leveled[delayed]
            conflicts.append(
                {
                    "wave": i,
                    "owner_role": role,
                    "tasks": tids,
                    "count": len(tids),
                    "delayed_task": delayed,
                    "blocked_by": tids[0],
                    "reason": f"同一负责人 {role} 在第 {i} 波并行承担 {len(tids)} 张卡，按串行顺延 {delayed}",
                    "original_start_day": round(lv["original_start"] + 1, 2),
                    "delayed_start_day": round(lv["start"] + 1, 2),
                    "shift_days": lv["shift"],
                }
            )
    return leveled, conflicts


def build_schedule(
    tasks: list[dict[str, Any]],
    features: list[str] | None = None,
    meta: dict[str, Any] | None = None,
    max_wave_width: int = DEFAULT_MAX_WAVE_WIDTH,
) -> dict[str, Any]:
    """把 dev_task_list 排成 schedule：波次、起止日、关键路径、并行度、资源冲突。

    返回的 dict 直接当 artifact.data 用（外层信封由 make_artifact 套）。
    valid=False 时不带排期结果，只带错误清单，供闸门拒绝。
    """
    tasks = tasks_of(tasks) if isinstance(tasks, (list, dict)) else []
    errors = validate_tasks(tasks)
    warnings: list[dict[str, Any]] = []
    if features:
        missing = uncovered_features(tasks, features)
        if missing:
            warnings.append(
                {"code": "E_FEATURE_UNCOVERED", "feature_ids": missing, "message": "有功能点没有承接任务"}
            )

    base = {
        "generated_at": datetime.datetime.now(TZ).replace(microsecond=0).isoformat(),
        "max_wave_width": int(max_wave_width),
        "meta": dict(meta or {}),
        "errors": errors,
        "warnings": warnings,
        "valid": not errors,
        "tasks": [],
        "waves": [],
        "critical_path": {"task_ids": [], "total_days": 0.0},
        "critical_tasks": [],
        "resource_conflicts": [],
        "total_days": 0.0,
        "summary": {"tasks": len(tasks), "waves": 0, "person_days": 0.0},
    }
    if errors:
        return base

    waves, level = topological_waves(tasks)
    cp = critical_path(tasks)
    leveled, conflicts = _level_resources(tasks, cp, level)
    width = max([len(w) for w in waves] or [0])
    if width > int(max_wave_width):
        warnings.append(
            {
                "code": "E_WAVE_TOO_WIDE",
                "wave_width": width,
                "limit": int(max_wave_width),
                "message": f"某波次并行 {width} 张卡，超过上限 {max_wave_width}，建议拆卡或调整负责人",
            }
        )

    tasks_out: list[dict[str, Any]] = []
    for task in tasks:
        tid = _s(task.get("task_id") or task.get("id"))
        days = _num(task.get("estimate_days")) or 0.0
        lv = leveled.get(tid, {"start": 0.0, "end": days, "original_start": 0.0, "shift": 0.0})
        start = float(lv["start"])
        end = float(lv["end"])
        card: dict[str, Any] = {
            **task,
            "task_id": tid,
            "wave": int(level.get(tid, 0)),
            "start_day": round(start + 1, 2),      # 第几天开工（1 起）
            "end_day": round(end, 2),                # 第几天收工
            "slack": cp["slack"].get(tid, 0.0),
            "critical": tid in cp["critical_tasks"],
        }
        if float(lv["shift"]) > 0:
            card["original_start_day"] = round(float(lv["original_start"]) + 1, 2)
            card["shift_days"] = lv["shift"]
        tasks_out.append(card)

    waves_out = [
        {
            "wave": i,
            "task_ids": w,
            "width": len(w),
            "start_day": round(min((float(leveled.get(t, {}).get("start", 0.0)) for t in w), default=0.0) + 1, 2),
            "end_day": round(
                max((float(leveled.get(t, {}).get("end", 0.0)) for t in w), default=0.0),
                2,
            ),
        }
        for i, w in enumerate(waves, start=1)
    ]

    person_days = round(sum(_num(t.get("estimate_days")) or 0.0 for t in tasks), 2)
    leveled_total = round(max((float(lv["end"]) for lv in leveled.values()), default=0.0), 2)
    total_days = max(cp["total_days"], leveled_total)
    base.update(
        {
            "tasks": tasks_out,
            "waves": waves_out,
            "critical_path": {"task_ids": cp["task_ids"], "total_days": cp["total_days"]},
            "critical_tasks": cp["critical_tasks"],
            "slack": cp["slack"],
            "resource_conflicts": conflicts,
            "max_wave_width_actual": width,
            "total_days": total_days,
            "summary": {"tasks": len(tasks), "waves": len(waves), "person_days": person_days},
        }
    )
    return base


# ---------------------------------------------------------------- 落盘与可视化

def write_json(payload: dict[str, Any], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _fmt(v: Any) -> str:
    n = _num(v)
    if n is None:
        return escape(_s(v))
    return f"{n:g}" if abs(n - round(n)) < 1e-9 else f"{n:.2f}"


def render_html(schedule: dict[str, Any], path: str | Path) -> Path:
    """一张自包含 HTML：左依赖图（SVG）、右甘特图（按波次分行的横条）、下方明细表。

    A 侧只保证「数据到图」这一层；配色、交互细化是 E 的活（§6 可视化）。
    """
    tasks = schedule.get("tasks") or []
    waves = schedule.get("waves") or []
    total = float(schedule.get("total_days") or 0) or 1.0
    cp_ids = set((schedule.get("critical_path") or {}).get("task_ids") or [])

    # 依赖图坐标：横轴波次、纵轴波内序号
    node_pos: dict[str, tuple[float, float]] = {}
    max_wave = max([len(w.get("task_ids") or []) for w in waves] or [1])
    for w in waves:
        for idx, tid in enumerate(w.get("task_ids") or []):
            x = 60 + (int(w.get("wave") or 1) - 1) * 190
            y = 50 + idx * 80
            node_pos[tid] = (x, y)
    width = 60 + max(len(waves), 1) * 190
    height = 80 + max_wave * 80

    edges: list[str] = []
    for task in tasks:
        tid = _s(task.get("task_id"))
        if tid not in node_pos:
            continue
        for dep in _as_list(task.get("depends_on")):
            did = _s(dep)
            if did not in node_pos:
                continue
            x1, y1 = node_pos[did]
            x2, y2 = node_pos[tid]
            on_cp = did in cp_ids and tid in cp_ids
            edges.append(
                f'<path d="M{x1 + 120:.0f},{y1 + 16:.0f} C{x1 + 150:.0f},{y1 + 16:.0f} '
                f'{x2 - 30:.0f},{y2 + 16:.0f} {x2:.0f},{y2 + 16:.0f}" class="edge{(" cp" if on_cp else "")}"/>'
            )
    nodes: list[str] = []
    for task in tasks:
        tid = _s(task.get("task_id"))
        if tid not in node_pos:
            continue
        x, y = node_pos[tid]
        cls = "node cp" if tid in cp_ids else "node"
        label = escape(_s(task.get("title"))[:14])
        nodes.append(
            f'<g class="{cls}" transform="translate({x:.0f},{y:.0f})">'
            f'<rect width="120" height="34" rx="4"/>'
            f'<text x="8" y="14">{escape(tid)}</text>'
            f'<text x="8" y="27" class="sub">{label}</text></g>'
        )

    # 甘特：每张卡一行，横条按起止日定位
    bars: list[str] = []
    for task in sorted(tasks, key=lambda t: (float(t.get("start_day") or 0), _s(t.get("task_id")))):
        tid = _s(task.get("task_id"))
        start = float(task.get("start_day") or 1)
        end = float(task.get("end_day") or start)
        left = (start - 1) / total * 100
        w_pct = max((end - start + 1) / total * 100, 1.5)
        cls = "bar cp" if tid in cp_ids else "bar"
        bars.append(
            f'<div class="grow"><span class="glabel">{escape(tid)} · {escape(_s(task.get("title"))[:16])}</span>'
            f'<span class="gtrack"><span class="{cls}" style="left:{left:.2f}%;width:{w_pct:.2f}%">'
            f'{_fmt(task.get("start_day"))}–{_fmt(task.get("end_day"))}d</span></span></div>'
        )

    rows: list[str] = []
    for task in tasks:
        rows.append(
            "<tr class=\"{}\"><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                "cp" if _s(task.get("task_id")) in cp_ids else "",
                escape(_s(task.get("task_id"))),
                escape(_s(task.get("title"))),
                escape(_s(task.get("owner_role"))),
                _fmt(task.get("estimate_days")),
                escape("、".join(_s(x) for x in _as_list(task.get("depends_on"))) or "—"),
                escape("、".join(_s(x) for x in _as_list(task.get("feature_refs")))),
                f"{_fmt(task.get('wave'))} 波 · {_fmt(task.get('start_day'))}–{_fmt(task.get('end_day'))}d",
            )
        )

    warn_rows = "".join(
        f"<li>{escape(_s(w.get('code')))}：{escape(_s(w.get('message')))}</li>"
        for w in (schedule.get("warnings") or [])
    )
    conflict_rows = "".join(
        f"<li>第 {_fmt(c.get('wave'))} 波 · {escape(_s(c.get('owner_role')))} 同时接 "
        f"{escape('、'.join(_s(x) for x in c.get('tasks') or []))}</li>"
        for c in (schedule.get("resource_conflicts") or [])
    )
    cp_ids_text = escape(" → ".join((schedule.get("critical_path") or {}).get("task_ids") or []) or "（无）")

    title = escape(_s((schedule.get("meta") or {}).get("title")) or "开发任务排期")
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{title} · 排期与依赖</title>
<style>
body{{font-family:"Microsoft YaHei","PingFang SC",sans-serif;margin:0;padding:24px;background:#f7f7f5;color:#222}}
h1{{font-size:20px;margin:0 0 4px}} .meta{{color:#666;font-size:12px;margin-bottom:18px}}
section{{background:#fff;border:1px solid #dcdcd7;border-radius:6px;padding:16px;margin-bottom:18px}}
h2{{font-size:15px;margin:0 0 12px;font-weight:600}}
svg{{max-width:100%;height:auto}}
.node rect{{fill:#fff;stroke:#9aa4b2;stroke-width:1}}
.node.cp rect{{fill:#fdf1e3;stroke:#c2703a}}
.node text{{font-size:12px;fill:#111}}
.node text.sub{{font-size:10px;fill:#666}}
.edge{{fill:none;stroke:#b9c0ca;stroke-width:1.2}}
.edge.cp{{stroke:#c2703a;stroke-width:2}}
.grow{{display:flex;align-items:center;margin:5px 0;font-size:12px}}
.glabel{{width:230px;flex:none;color:#333;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.gtrack{{position:relative;flex:1;height:18px;background:#f0f0ec;border-radius:3px}}
.bar{{position:absolute;top:2px;height:14px;background:#8fa9c7;border-radius:3px;color:#fff;
     font-size:10px;line-height:14px;text-align:center;white-space:nowrap;overflow:hidden}}
.bar.cp{{background:#c2703a}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th,td{{border:1px solid #e2e2dd;padding:5px 7px;text-align:left}}
th{{background:#f2f2ef;font-weight:600}}
tr.cp td{{background:#fdf1e3}}
ul{{margin:0;padding-left:18px;font-size:12px;color:#444}}
.note{{font-size:12px;color:#666;margin-top:8px}}
</style></head><body>
<h1>{title} · 任务编排与可视化</h1>
<div class="meta">生成于 {escape(_s(schedule.get('generated_at')))} · 任务 {_fmt(schedule.get('summary', {}).get('tasks'))} 张 ·
波次 {_fmt(schedule.get('summary', {}).get('waves'))} 个 · 人天 {_fmt(schedule.get('summary', {}).get('person_days'))} ·
总工期 {_fmt(schedule.get('total_days'))} 天 · 关键路径 {cp_ids_text}</div>

<section><h2>1 依赖图（横轴为波次，橙色为关键路径）</h2>
<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}">
{''.join(edges)}
{''.join(nodes)}
</svg></section>

<section><h2>2 甘特图（每行一张卡，按天定位）</h2>
{''.join(bars)}
<div class="note">同波次的卡互不依赖，可并行；橙色为关键路径上的卡，任何一张延期都会推迟总工期。</div>
</section>

<section><h2>3 任务明细</h2>
<table><thead><tr><th>任务号</th><th>标题</th><th>负责人</th><th>工时(人天)</th>
<th>依赖</th><th>功能追溯</th><th>波次与起止</th></tr></thead><tbody>
{''.join(rows)}
</tbody></table></section>

<section><h2>4 排期提示</h2>
<ul>{warn_rows or '<li>无</li>'}</ul>
<h2>资源冲突</h2>
<ul>{conflict_rows or '<li>无</li>'}</ul>
</section>
</body></html>"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
    return p
