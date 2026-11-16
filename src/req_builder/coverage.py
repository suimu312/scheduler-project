"""覆盖矩阵：代码算，不调模型。

行 = 设计规格的来源单元（功能点 / 约束 / 冲突 / 质疑），
列 = 非 Wont 的任务卡；
covered_by 为空即 gap，直接喂给 A 判 G_P6_P7。

覆盖率两个口径写死：
- Wont 不进任何分母（主动排除不是遗漏，否则越诚实覆盖率越低）；
- 综合覆盖率 = 0.6 * 来源加权覆盖率 + 0.4 * 任务完备率。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .models import CoverageReport, CoverageRow, DevTask, Gap, SourceRef
from .validators import validate_task_list

_ID_RE = re.compile(r"(FEA|CON|CFL|CHG)-\d{3,}")
W_SOURCE_MUST = 3
W_SOURCE_NORMAL = 2


def _iter_entries(items: Any) -> Iterable[tuple[str, str]]:
    """把规格里各种写法的条目归一成 (id, 标题)。"""
    if not items:
        return
    if isinstance(items, (str, dict)):
        items = [items]
    for index, item in enumerate(items, start=1):
        if isinstance(item, dict):
            raw_id = item.get("feature_id") or item.get("constraint_id") or item.get("id") or ""
            title = str(item.get("title") or item.get("name") or item.get("summary") or "")
            yield str(raw_id), title
        else:
            text = str(item)
            match = _ID_RE.search(text)
            yield (match.group(0) if match else ""), text.strip()


def _scope_ids(spec: dict[str, Any], key: str) -> set[str]:
    ids: set[str] = set()
    for item in spec.get(key) or []:
        if isinstance(item, dict):
            value = item.get("feature_id") or item.get("id") or item.get("name")
            if value:
                ids.add(str(value))
        else:
            ids.add(str(item))
    return ids


def collect_sources(spec: dict[str, Any], artifacts: dict[str, Any] | None = None) -> list[CoverageRow]:
    """四类来源全集都要进矩阵：漏任何一类都是选择性覆盖。"""
    artifacts = artifacts or {}
    scope_in = _scope_ids(spec, "scope_in")
    scope_out = _scope_ids(spec, "scope_out")
    rows: list[CoverageRow] = []

    for seq, (raw_id, title) in enumerate(_iter_entries(spec.get("features")), start=1):
        source_id = raw_id or f"FEA-{seq:03d}"
        if source_id in scope_out:
            continue  # 本期明确不做，不进分母
        weight = W_SOURCE_MUST if (source_id in scope_in or not scope_in) else W_SOURCE_NORMAL
        rows.append(
            CoverageRow(
                source_type="feature",
                source_id=source_id,
                source_ref=f"solution_comparison:{source_id}",
                weight=weight,
                title=title,
            )
        )

    for seq, (raw_id, title) in enumerate(_iter_entries(spec.get("constraints")), start=1):
        source_id = raw_id or f"CON-{seq:03d}"
        rows.append(
            CoverageRow(
                source_type="constraint",
                source_id=source_id,
                source_ref=f"design_spec:{source_id}",
                weight=W_SOURCE_MUST,  # 合规/技术约束漏一条就要返工
                title=title,
            )
        )

    conflicts = artifacts.get("conflict_register")
    conflicts_data = conflicts.get("data", conflicts) if isinstance(conflicts, dict) else {}
    for item in (conflicts_data or {}).get("conflicts", []) or []:
        cid = str(item.get("conflict_id") or "")
        if not cid:
            continue
        rows.append(
            CoverageRow(
                source_type="conflict",
                source_id=cid,
                source_ref=f"conflict_register:{cid}",
                weight=W_SOURCE_NORMAL,
                title=str(item.get("topic_id") or ""),
            )
        )

    review = artifacts.get("review_sheet")
    review_data = review.get("data", review) if isinstance(review, dict) else {}
    challenges = (review_data or {}).get("challenges", []) or []
    for item in challenges:
        if isinstance(item, dict):
            chg_id = str(item.get("challenge_id") or item.get("id") or "")
            title = str(item.get("title") or item.get("summary") or "")
        else:
            chg_id, title = str(item), ""
        if not chg_id:
            continue
        rows.append(
            CoverageRow(
                source_type="challenge",
                source_id=chg_id,
                source_ref=f"review_sheet:{chg_id}",
                weight=W_SOURCE_NORMAL,
                title=title,
            )
        )

    return rows


def compute_coverage(
    tasks: list[DevTask],
    spec: dict[str, Any],
    artifacts: dict[str, Any] | None = None,
) -> CoverageReport:
    """组装覆盖矩阵 + 缺口 + 覆盖率。"""
    active_tasks = [t for t in tasks if t.priority != "wont"]
    report = CoverageReport(columns=[t.task_id for t in active_tasks])
    report.rows = collect_sources(spec, artifacts)

    ref_by_task: dict[str, set[str]] = {}
    for task in active_tasks:
        ref_by_task[task.task_id] = {ref.ref_id for ref in task.source_refs}

    total_weight = 0
    covered_weight = 0
    for row in report.rows:
        hits = [tid for tid, refs in ref_by_task.items() if row.source_id in refs]
        row.covered_by = sorted(hits)
        row.status = "covered" if hits else "gap"
        total_weight += row.weight
        if hits:
            covered_weight += row.weight
        else:
            report.gaps.append(
                Gap(
                    kind="uncovered_source",
                    target=row.source_id,
                    severity="ERROR" if row.weight >= W_SOURCE_MUST else "WARN",
                    detail=f"{row.source_ref}（{row.title or '无标题'}）没有任何任务承接",
                    weight=row.weight,
                    rule="C1",
                )
            )

    report.r_source = (covered_weight / total_weight) if total_weight else 0.0

    if active_tasks:
        complete = sum(
            1 for t in active_tasks if t.source_refs and t.acceptance
        )
        report.r_task = complete / len(active_tasks)
    else:
        report.r_task = 0.0

    report.coverage_rate = 0.6 * report.r_source + 0.4 * report.r_task

    # 任务侧缺口（八条规则）并进同一份 gaps，F 的确认单只读一处
    task_gaps, _checks = validate_task_list(tasks, spec)
    report.gaps.extend(task_gaps)
    report.gaps.sort(key=lambda g: (-g.weight, g.target))
    return report


def gate_inputs(tasks: list[DevTask], report: CoverageReport) -> dict[str, Any]:
    """给 A 判 G_P6_P7 的两项数据 + E 自检结论（E 不自己宣布通过）。"""
    active = [t for t in tasks if t.priority != "wont"]
    all_acceptance = all(bool(t.acceptance) for t in active)
    must_uncovered = report.gap_count > 0
    self_check_pass = (
        report.r_source >= 0.85
        and report.error_count == 0
        and report.gap_count == 0
        and all_acceptance
    )
    return {
        "gap_count": report.gap_count,
        "coverage_rate": round(report.coverage_rate, 3),
        "all_have_acceptance": all_acceptance,
        "must_sources_covered": not must_uncovered,
        "self_check_pass": self_check_pass,
        "g_p6_p7_data": report.gap_count == 0 and all_acceptance,
    }


def coverage_by_module(tasks: list[DevTask], report: CoverageReport) -> dict[str, dict[str, int]]:
    """按模块聚合，给 V6 热图做分组表头。"""
    buckets: dict[str, dict[str, int]] = {}
    for task in tasks:
        bucket = buckets.setdefault(task.module_id, {"total": 0, "wont": 0})
        bucket["total"] += 1
        if task.priority == "wont":
            bucket["wont"] += 1
    return buckets


def refs_to_rows(rows: list[CoverageRow]) -> list[dict[str, Any]]:
    return [row.to_dict() for row in rows]


def sources_of_kind(rows: list[CoverageRow], kind: str) -> list[CoverageRow]:
    return [row for row in rows if row.source_type == kind]


def normalize_source_ref(raw: str) -> SourceRef:
    return SourceRef.from_string(raw)
