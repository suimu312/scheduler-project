"""故障注入 · FI-01 依赖成环 / FI-04 覆盖空缺

用途：验证判据层能不能把人为造出来的坏情况抓出来。两条用例都只在内存里改产物，
不碰 runs/ 下的真实文件，也不碰业务代码。

用法：
    python tests/fault_injection/inject.py --run runs/MOCKD
    python tests/fault_injection/inject.py --run runs/MOCKD --case FI-01

输出：每例一行结果（是否检出 / 命中信号 / 耗时 / 有无误报），末行写 JSON 存档，
      同目录 last_run.json 可直接当《检出记录表》的证据。
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from req_builder.coverage import compute_coverage  # noqa: E402
from req_builder.models import SourceRef  # noqa: E402
from req_builder.task_list import load_task_list  # noqa: E402
from req_builder.validators import find_cycle  # noqa: E402


def _patch_refs(tasks, raw_tasks):
    """兼容层（临时）：dev_task_list.json 落盘时写的是 feature_refs / requirement_refs，
    而 DevTask.from_dict 只认 source_refs，导致二次读取后来源全丢、覆盖矩阵恒为 0%。
    这里从原始 JSON 把来源补回去，等 E 修好 models.py 的 from_dict 后可直接删掉本函数。
    """
    by_id = {str(r.get("task_id")): r for r in raw_tasks}
    for task in tasks:
        if task.source_refs:
            continue
        raw = by_id.get(task.task_id, {})
        refs = [SourceRef(kind="feature", ref_id=str(x)) for x in raw.get("feature_refs") or []]
        refs += [SourceRef(kind="requirement", ref_id=str(x)) for x in raw.get("requirement_refs") or []]
        task.source_refs = refs
    return tasks


def _load(run_dir: Path):
    cfg = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    spec = cfg.get("design_spec") or {}
    artifact = json.loads(
        (run_dir / "artifacts" / "dev_task_list.json").read_text(encoding="utf-8")
    )
    raw_tasks = (artifact.get("data") or artifact).get("tasks", [])
    tasks = _patch_refs(load_task_list(artifact), raw_tasks)
    return spec, tasks


def _baseline(tasks, spec):
    report = compute_coverage(tasks, spec)
    return {
        "gap_count": len(report.gaps),
        "uncovered_count": sum(1 for g in report.gaps if g.kind == "uncovered_source"),
        "coverage_rate": report.coverage_rate,
        "cycle": find_cycle(tasks),
    }


def _precise_cycle(tasks) -> list[str]:
    """把真正的环取出来。

    validators.find_cycle 用的是 Kahn 残余法，返回的是"入度仍大于 0 的全部节点"，
    也就是环 + 环下游的整条链。判定"有没有环"够用，但报"环上任务"会夸大范围。
    这里用 DFS 回溯边取一个最小环，仅用于报告，不改 E 的实现。
    """
    graph = {t.task_id: list(t.depends_on) for t in tasks}
    state: dict[str, int] = {}  # 1=在栈上 2=已完成
    stack: list[str] = []

    def dfs(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for nxt in graph.get(node, []):
            if nxt not in graph:
                continue
            if state.get(nxt) == 1:
                return stack[stack.index(nxt):] + [nxt]
            if state.get(nxt) is None:
                found = dfs(nxt)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for tid in graph:
        if state.get(tid) is None:
            found = dfs(tid)
            if found:
                return found
    return []


def inject_cycle(tasks):
    """FI-01：把一条已有依赖反向补一条，制造二元环。"""
    mutated = copy.deepcopy(tasks)
    by_id = {t.task_id: t for t in mutated}

    for task in mutated:
        if task.depends_on:
            upstream = by_id.get(task.depends_on[0])
            if upstream is not None and task.task_id not in upstream.depends_on:
                upstream.depends_on.append(task.task_id)
                return mutated, f"{upstream.task_id} → {task.task_id}（原方向 {task.task_id} → {upstream.task_id}）"

    # 兜底：没有现成依赖就自己造一对
    if len(mutated) >= 2:
        a, b = mutated[0], mutated[1]
        a.depends_on.append(b.task_id)
        b.depends_on.append(a.task_id)
        return mutated, f"{a.task_id} ↔ {b.task_id}（新造对称依赖）"

    raise RuntimeError("任务卡少于 2 张，无法注入依赖成环")


def inject_gap(tasks, spec):
    """FI-04：抽掉承接某个来源的全部任务卡，制造覆盖空缺。"""
    base = compute_coverage(tasks, spec)
    victim_row = None

    for row in base.rows:
        if row.status == "covered" and row.covered_by:
            victim_row = row
            break

    if victim_row is None:
        raise RuntimeError("没有任何被覆盖的来源，无法注入空缺（先看基线是否已经全空）")

    removed = list(victim_row.covered_by)
    mutated = [t for t in copy.deepcopy(tasks) if t.task_id not in removed]
    return mutated, f"删除 {', '.join(removed)}，使 {victim_row.source_ref} 无人承接"


def evaluate(case: str, tasks, spec, baseline):
    """用现有的纯函数判定，不引入任何新逻辑。"""
    t0 = time.perf_counter()
    uncovered = sum(
        1 for g in compute_coverage(tasks, spec).gaps if g.kind == "uncovered_source"
    )

    if case == "FI-01":
        blocked = find_cycle(tasks)
        detected = bool(blocked)
        ring = _precise_cycle(tasks)
        signal = "OBS-04 依赖成环"
        if detected:
            detail = f"环 {len(ring) - 1} 张：{' → '.join(ring)}"
            if len(blocked) > len(ring) - 1:
                detail += f"（find_cycle 另把下游 {len(blocked) - (len(ring) - 1)} 张也算进环里）"
        else:
            detail = "未检出"
        false_positive = uncovered > baseline["uncovered_count"]
    else:
        detected = uncovered > baseline["uncovered_count"]
        signal = "OBS-08 覆盖空缺"
        gap_ids = [
            g.target
            for g in compute_coverage(tasks, spec).gaps
            if g.kind == "uncovered_source"
        ]
        detail = f"缺失来源 {', '.join(gap_ids)}" if detected else "未检出"
        false_positive = bool(find_cycle(tasks))

    cost_ms = (time.perf_counter() - t0) * 1000
    return {
        "case": case,
        "detected": detected,
        "signal": signal,
        "detail": detail,
        "cost_ms": round(cost_ms, 1),
        "false_positive": false_positive,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="runs/MOCKD", help="运行目录（含 config.json 与 artifacts/）")
    ap.add_argument("--case", choices=["FI-01", "FI-04", "all"], default="all")
    args = ap.parse_args()

    run_dir = (ROOT / args.run).resolve()
    spec, tasks = _load(run_dir)
    base = _baseline(tasks, spec)

    print(f"基线：任务 {len(tasks)} 张｜缺口 {base['gap_count']}｜覆盖率 {base['coverage_rate']:.2%}｜成环 {base['cycle'] or '无'}")
    print()
    print("| 用例 | 是否检出 | 命中信号 | 依据 | 耗时 | 误报 |")
    print("|---|---|---|---|---|---|")

    cases = ["FI-01", "FI-04"] if args.case == "all" else [args.case]
    results = []

    for case in cases:
        try:
            if case == "FI-01":
                mutated, how = inject_cycle(tasks)
            else:
                mutated, how = inject_gap(tasks, spec)
        except RuntimeError as exc:
            print(f"| {case} | 无法注入 | — | {exc} | — | — |")
            continue

        result = evaluate(case, mutated, spec, base)
        result["injection"] = how
        results.append(result)
        print(
            f"| {case} | {'是' if result['detected'] else '**否**'} | {result['signal']} "
            f"| {result['detail']} | {result['cost_ms']} ms | {'有' if result['false_positive'] else '无'} |"
        )
        print(f"    注入方式：{how}")

    out = ROOT / "tests" / "fault_injection" / "last_run.json"
    out.write_text(
        json.dumps(
            {"run": str(run_dir), "baseline": base, "results": results},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print()
    print(f"存档：{out.relative_to(ROOT)}")
    return 0 if all(r["detected"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
