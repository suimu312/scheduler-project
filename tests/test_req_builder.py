"""req_builder 单元测试：优先级、校验、覆盖、事件、渲染、CLI。

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from req_builder.contracts import StageContext, make_artifact
from req_builder.coverage import compute_coverage, gate_inputs
from req_builder.demo import DRAFTS, SPEC, _schedule, build_events
from req_builder.models import AcceptanceCriterion, DevTask, SourceRef
from req_builder.priority import compute_priorities
from req_builder.task_list import build_task_list, structure_tasks
from req_builder.validators import find_cycle, validate_task_list
from req_builder.viz.cli import main as viz_main
from req_builder.viz.events import ReplayWindow, load_events
from req_builder.viz.render import render_views


def task(task_id: str, deps: list[str] | None = None, days: float = 1.0, **kwargs) -> DevTask:
    defaults = dict(
        module_id="MOD-001",
        title=f"任务 {task_id}",
        depends_on=deps or [],
        estimate_days=days,
        owner_role="后端",
        acceptance=[AcceptanceCriterion(ac_id=f"AC-{task_id}-1", given="", when="", then="点击后页面出现结果")],
        source_refs=[SourceRef(kind="feature", ref_id="FEA-001")],
    )
    defaults.update(kwargs)
    return DevTask(task_id=task_id, **defaults)


class PriorityTest(unittest.TestCase):
    def test_dependency_precedence_becomes_must(self):
        tasks = [task("DEV-001"), task("DEV-002", ["DEV-001"])]
        compute_priorities(tasks, {"scope_in": ["FEA-001"]})
        self.assertEqual(tasks[0].priority, "must")
        self.assertIn("P1", tasks[0].priority_reason)

    def test_acceptance_reference_becomes_must(self):
        tasks = [
            task("DEV-001"),
            task("DEV-002", acceptance=[AcceptanceCriterion("AC-1", "", "", "DEV-001 返回后页面出现 10 条")]),
        ]
        compute_priorities(tasks, {})
        self.assertEqual(tasks[0].priority, "must")
        self.assertIn("P2", tasks[0].priority_reason)

    def test_scope_out_is_wont(self):
        tasks = [task("DEV-001", source_refs=[SourceRef(kind="feature", ref_id="FEA-009")])]
        compute_priorities(tasks, {"scope_out": ["FEA-009"]})
        self.assertEqual(tasks[0].priority, "wont")
        self.assertIn("P3", tasks[0].priority_reason)

    def test_human_lock_is_not_overwritten(self):
        tasks = [task("DEV-001", priority="wont", priority_source="human")]
        compute_priorities(tasks, {"scope_in": ["FEA-001"]})
        self.assertEqual(tasks[0].priority, "wont")
        self.assertIn("P4", tasks[0].priority_reason)

    def test_reason_always_written(self):
        tasks = [task("DEV-001"), task("DEV-002", ["DEV-001"])]
        compute_priorities(tasks, {})
        for item in tasks:
            self.assertTrue(item.priority_reason, "优先级必须带理由，否则复核只能靠吵")


class ValidatorTest(unittest.TestCase):
    def test_granularity_out_of_range(self):
        tasks = [task("DEV-001", days=5)]
        gaps, checks = validate_task_list(tasks)
        self.assertFalse(checks["T1_granularity"])
        self.assertTrue(any(g.kind == "bad_granularity" for g in gaps))

    def test_multi_action_title(self):
        tasks = [task("DEV-001", title="发布商品以及站内消息")]  # 一条卡两件事
        gaps, _ = validate_task_list(tasks)
        self.assertTrue(any(g.kind == "multi_action_card" for g in gaps))

    def test_owner_must_be_unique(self):
        tasks = [task("DEV-001", owner_role="前端/后端")]
        gaps, checks = validate_task_list(tasks)
        self.assertFalse(checks["T2_unique_owner"])
        self.assertTrue(any(g.kind == "owner_not_unique" for g in gaps))

    def test_dep_cycle_detected(self):
        tasks = [task("DEV-001", ["DEV-002"]), task("DEV-002", ["DEV-001"])]
        self.assertEqual(set(find_cycle(tasks)), {"DEV-001", "DEV-002"})
        gaps, checks = validate_task_list(tasks)
        self.assertFalse(checks["T4_no_cycle"])
        self.assertTrue(any(g.kind == "E_DEP_CYCLE" for g in gaps))

    def test_vague_task_rejected(self):
        tasks = [task("DEV-001", title="接口联调")]
        gaps, _ = validate_task_list(tasks)
        self.assertTrue(any(g.kind == "vague_task" for g in gaps))

    def test_missing_acceptance_rejected(self):
        tasks = [task("DEV-001", acceptance=[])]
        gaps, checks = validate_task_list(tasks)
        self.assertFalse(checks["T5_acceptance"])
        self.assertTrue(any(g.kind == "missing_acceptance" for g in gaps))

    def test_fuzzy_acceptance_rejected(self):
        ac = AcceptanceCriterion("AC-1", "", "", "响应尽量快速，体验良好")
        tasks = [task("DEV-001", acceptance=[ac])]
        gaps, _ = validate_task_list(tasks)
        self.assertTrue(any(g.kind == "unverifiable_ac" for g in gaps))

    def test_orphan_task_rejected(self):
        tasks = [task("DEV-001", source_refs=[])]
        gaps, checks = validate_task_list(tasks)
        self.assertFalse(checks["T6_traceable"])
        self.assertTrue(any(g.kind == "orphan_task" for g in gaps))

    def test_clean_list_passes(self):
        tasks = structure_tasks(DRAFTS)
        compute_priorities(tasks, SPEC)
        gaps, checks = validate_task_list(tasks, SPEC)
        self.assertFalse([g for g in gaps if g.severity == "ERROR"])
        self.assertTrue(checks["T4_no_cycle"])


class CoverageTest(unittest.TestCase):
    def test_uncovered_source_is_gap(self):
        spec = {"features": [{"feature_id": "FEA-001"}, {"feature_id": "FEA-002"}]}
        tasks = [task("DEV-001", source_refs=[SourceRef(kind="feature", ref_id="FEA-001")])]
        report = compute_coverage(tasks, spec)
        self.assertEqual(report.gap_count, 1)
        self.assertLess(report.coverage_rate, 1.0)
        gate = gate_inputs(tasks, report)
        self.assertFalse(gate["g_p6_p7_data"])

    def test_wont_not_in_denominator(self):
        spec = {"features": [{"feature_id": "FEA-001"}]}
        tasks = [task("DEV-001", source_refs=[SourceRef(kind="feature", ref_id="FEA-002")], priority="wont")]
        report = compute_coverage(tasks, spec)
        # FEA-001 未被覆盖 → 缺 1；Wont 任务不进列，也不进完备率分母
        self.assertEqual(report.columns, [])
        self.assertEqual(report.gap_count, 1)

    def test_full_coverage_of_demo(self):
        tasks = structure_tasks(DRAFTS)
        compute_priorities(tasks, SPEC)
        artifacts = {"conflict_register": {"conflicts": [{"conflict_id": "CFL-001", "topic_id": "task_granularity"}]}}
        report = compute_coverage(tasks, SPEC, artifacts)
        self.assertEqual(report.gap_count, 0)
        self.assertEqual(report.coverage_rate, 1.0)

    def test_coverage_formula_weights_source_and_task(self):
        spec = {"features": [{"feature_id": "FEA-001"}]}
        tasks = [task("DEV-001", source_refs=[SourceRef(kind="feature", ref_id="FEA-001")])]
        report = compute_coverage(tasks, spec)
        self.assertAlmostEqual(report.coverage_rate, 0.6 * 1.0 + 0.4 * 1.0, places=3)


class TaskListTest(unittest.TestCase):
    def test_missing_spec_blocks(self):
        result = build_task_list(StageContext(run_id="R1", artifacts={}))
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.errors[0]["code"], "E_ARTIFACT_MISSING")

    def test_missing_drafts_blocks(self):
        ctx = StageContext(
            run_id="R1",
            artifacts={"design_spec": make_artifact(SPEC, "R1", "P1")},
        )
        result = build_task_list(ctx)
        self.assertEqual(result.status, "blocked")

    def test_build_task_list_returns_two_artifacts(self):
        ctx = StageContext(
            run_id="R1",
            artifacts={
                "design_spec": make_artifact(SPEC, "R1", "P1"),
                "solution_comparison": make_artifact({"tasks": DRAFTS}, "R1", "P4"),
                "conflict_register": {"conflicts": [{"conflict_id": "CFL-001"}]},
            },
        )
        result = build_task_list(ctx)
        self.assertEqual(result.status, "ok")
        self.assertEqual(set(result.artifacts), {"dev_task_list", "task_coverage"})
        for artifact in result.artifacts.values():
            self.assertEqual(artifact["meta"]["schema_version"], "1.0")
            self.assertEqual(artifact["meta"]["produced_by"], "req_builder")
            self.assertEqual(artifact["meta"]["run_id"], "R1")

    def test_structure_tasks_renumbers_bad_ids(self):
        drafts = [{"task_id": "x-1", "title": "任务甲"}, {"task_id": "DEV-001", "title": "任务乙"}]
        tasks = structure_tasks(drafts)
        self.assertEqual([t.task_id for t in tasks], ["DEV-001", "DEV-002"])


class EventTest(unittest.TestCase):
    def test_replay_window_cuts_by_seq(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            raw = build_events()
            for index, event in enumerate(raw, start=1):
                event["seq"] = index
                event["run_id"] = "R0001"
            path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in raw), encoding="utf-8")
            events = load_events(path)
            self.assertEqual(len(events), len(raw))
            window = ReplayWindow.of(events, until_seq=10)
            self.assertTrue(all(e.seq <= 10 for e in window.events))
            self.assertEqual(window.max_seq, 10)
            full = ReplayWindow.of(events)
            self.assertEqual(full.until_seq, len(raw))

    def test_anomaly_pick(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text(
                "\n".join(
                    json.dumps(e, ensure_ascii=False)
                    for e in [
                        {"event_type": "run.start", "seq": 1},
                        {"event_type": "guard.signal", "seq": 2, "value": 0.9},
                        {"event_type": "gate.evaluated", "seq": 3, "payload": {"action": "rework"}},
                    ]
                ),
                encoding="utf-8",
            )
            events = load_events(path)
            anomalies = ReplayWindow.of(events).anomalies()
            self.assertEqual([e.seq for e in anomalies], [2, 3])


class RenderTest(unittest.TestCase):
    def _events(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            raw = build_events()
            for index, event in enumerate(raw, start=1):
                event["seq"] = index
                event["run_id"] = "R0001"
            path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in raw), encoding="utf-8")
            return load_events(path)

    def _artifacts(self):
        ctx = StageContext(
            run_id="R0001",
            artifacts={
                "design_spec": make_artifact(SPEC, "R0001", "P1"),
                "solution_comparison": make_artifact({"tasks": DRAFTS}, "R0001", "P4"),
                "conflict_register": {"conflicts": [{"conflict_id": "CFL-001"}]},
            },
        )
        result = build_task_list(ctx)
        return {
            "schedule": make_artifact(_schedule(), "R0001", "P7"),
            "task_coverage": result.artifacts["task_coverage"],
            "dev_task_list": result.artifacts["dev_task_list"],
        }

    def test_all_seven_views_present(self):
        html = render_views(self._events(), artifacts=self._artifacts())
        for view_id in ("V1", "V2", "V3", "V4", "V5", "V6", "V7"):
            self.assertIn(f'<span class="chip">{view_id}</span>', html)
        self.assertEqual(html.count("<svg"), 7)

    def test_pure_function_same_input_same_output(self):
        events = self._events()
        artifacts = self._artifacts()
        self.assertEqual(
            render_views(events, until_seq=40, artifacts=artifacts),
            render_views(events, until_seq=40, artifacts=artifacts),
        )

    def test_until_seq_limits_elements(self):
        html = render_views(self._events(), until_seq=5, artifacts=self._artifacts())
        self.assertIn('value="5"', html)
        self.assertIn("EVT-00005", html)

    def test_event_text_is_escaped(self):
        from req_builder.contracts import RunEvent

        events = [
            RunEvent(event_type="agent.statement", seq=1, run_id="R1", stage="P3", actor="A2",
                     payload={"stance": "<script>alert(1)</script>"}),
        ]
        html = render_views(events, artifacts={})
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_no_external_resources(self):
        html = render_views(self._events(), artifacts=self._artifacts())
        # SVG 的 xmlns 是命名空间声明，不是外链；真正要防的是 src/href 引外部资源
        self.assertNotIn('src="', html)
        self.assertNotIn('href="', html)
        self.assertNotIn("<link", html)
        self.assertIn("data-seq", html)


class CliTest(unittest.TestCase):
    def test_viz_cli_writes_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "R0001"
            (run_dir / "artifacts").mkdir(parents=True)
            raw = build_events()
            for index, event in enumerate(raw, start=1):
                event["seq"] = index
                event["run_id"] = "R0001"
            (run_dir / "events.jsonl").write_text(
                "\n".join(json.dumps(e, ensure_ascii=False) for e in raw), encoding="utf-8"
            )
            code = viz_main(["--run-dir", str(run_dir), "--until", "30"])
            self.assertEqual(code, 0)
            out = run_dir / "viz" / "index.html"
            self.assertTrue(out.exists())
            self.assertIn("EVT-00030", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
