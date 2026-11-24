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

    def test_critical_path_dict_shape_from_scheduler(self):
        """A 的 schedule.json 里 critical_path 是 {"task_ids": [...], "total_days": n}。

        之前 V5 直接把它当列表遍历，页面上画出了 "task_ids → total_days"。
        这里锁住：字典形状必须被认成任务号序列，且不得出现键名。
        """
        artifacts = self._artifacts()
        schedule = dict(artifacts["schedule"]["data"])
        schedule["critical_path"] = {"task_ids": ["DEV-001", "DEV-002", "DEV-004"],
                                     "total_days": 7.0}
        schedule["total_days"] = 7.0
        artifacts["schedule"] = make_artifact(schedule, "R0001", "P7")
        html = render_views(self._events(), artifacts=artifacts)

        self.assertIn("DEV-001 → DEV-002 → DEV-004", html)
        self.assertNotIn("task_ids →", html)
        self.assertNotIn("→ total_days", html)

    def test_critical_path_list_shape_still_works(self):
        """E 自己的 demo 用的是纯列表，两种形状都要认。"""
        artifacts = self._artifacts()
        schedule = dict(artifacts["schedule"]["data"])
        schedule["critical_path"] = ["DEV-006", "DEV-002"]
        artifacts["schedule"] = make_artifact(schedule, "R0001", "P7")
        html = render_views(self._events(), artifacts=artifacts)
        self.assertIn("DEV-006 → DEV-002", html)

    def test_header_shows_model_calls_and_rule_checks(self):
        """W7 顶栏：必须同时给出「N 次模型调用 / M 次规则判定」，且口径可解释。"""
        html = render_views(self._events(), artifacts=self._artifacts())
        self.assertIn("次模型调用", html)
        self.assertIn("次规则判定", html)
        self.assertIn("闸门不计费", html)
        self.assertIn("规则判定=gate.evaluated 条数", html)

    def test_stage_drawer_lists_all_stages(self):
        """W7 产物抽屉：每个阶段节点都要有一个可点开的入口。"""
        html = render_views(self._events(), artifacts=self._artifacts())
        self.assertIn('id="stage-drawer"', html)
        for stage_id in ("P1", "P2", "P3", "P4", "P5", "P6", "P7"):
            self.assertIn(f'data-stage="{stage_id}"', html)
        self.assertIn("var STAGE_DATA =", html)

    def test_stage_drawer_carries_stage_outputs_for_download(self):
        """抽屉里要能拿到该阶段落盘的产物，否则点开是空的。"""
        html = render_views(self._events(), artifacts=self._artifacts())
        marker = html.split("var STAGE_DATA =", 1)[1]
        # dev_task_list / task_coverage 是 P6 产物，必须出现在抽屉数据里
        self.assertIn('"dev_task_list"', marker)
        self.assertIn('"task_coverage"', marker)
        self.assertIn('"schedule"', marker)
        self.assertIn('"inputs"', marker)
        self.assertIn('"outputs"', marker)
        self.assertIn('"gates"', marker)


class HydrateTest(unittest.TestCase):
    """跨模块联调：A 的事件只给 payload_ref，渲染前必须解引用成 payload。"""

    def test_payload_ref_is_resolved_into_payload(self):
        from req_builder.contracts import RunEvent
        from req_builder.viz.hydrate import hydrate_events

        events = [
            RunEvent(event_type="agent.statement", seq=1, run_id="R1", stage="P3", actor="A2",
                     payload_ref="discussion_minutes:ST-001"),
            RunEvent(event_type="agent.statement", seq=2, run_id="R1", stage="P3", actor="A3",
                     payload={"quote": "内联的保持原样"}),
        ]
        artifacts = {
            "discussion_minutes": {
                "data": {"statements": [{"statement_id": "ST-001", "agent_id": "A2", "quote": "按项目归档能搜到"}]}
            }
        }
        hydrate_events(events, artifacts)
        self.assertEqual(events[0].payload.get("quote"), "按项目归档能搜到")
        self.assertEqual(events[1].payload.get("quote"), "内联的保持原样")

    def test_gate_verdict_inferred_from_next_stage(self):
        from req_builder.contracts import RunEvent
        from req_builder.viz.hydrate import infer_gate_verdicts

        events = [
            RunEvent(event_type="gate.evaluated", seq=1, run_id="R1", stage="P1",
                     payload_ref="G_P1_P2"),
            RunEvent(event_type="stage.enter", seq=2, run_id="R1", stage="P2"),
        ]
        infer_gate_verdicts(events)
        self.assertEqual(events[0].payload.get("action"), "advance")

    def test_v6_reads_coverage_matrix_alias(self):
        """A 早期落的是 coverage_matrix，data 形状与 task_coverage 一致。"""
        from req_builder.viz.views import v6_coverage

        artifacts = {
            "coverage_matrix": {
                "data": {
                    "rows": [{"source_type": "feature", "source_id": "FEA-001",
                              "status": "covered", "covered_by": ["REQ-001"]}],
                    "columns": ["REQ-001"],
                    "gap_count": 0,
                    "coverage_rate": 1.0,
                }
            }
        }
        coverage = v6_coverage._coverage_of(artifacts)
        self.assertEqual(coverage["columns"], ["REQ-001"])
        self.assertEqual(coverage["gap_count"], 0)


class CliTest(unittest.TestCase):
    def test_load_artifacts_reads_whole_directory(self):
        """抽屉要列出每个阶段的产物，所以不能只认 5 个固定文件名。"""
        import json as _json

        from req_builder.viz.cli import load_artifacts

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "artifacts"
            directory.mkdir(parents=True)
            for name in ("idea_card", "discussion_minutes", "dev_task_list"):
                (directory / f"{name}.json").write_text(
                    _json.dumps({"meta": {"stage": "P6"}, "data": {"k": name}}), encoding="utf-8"
                )
            bundle = load_artifacts(directory)
            self.assertEqual(sorted(bundle), ["dev_task_list", "discussion_minutes", "idea_card"])

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


class DiscussionFallbackTest(unittest.TestCase):
    def test_v2_falls_back_to_discussion_artifact(self):
        """A 的运行 P3 只落 1 条 agent.statement，讨论内容要从产物补齐。"""
        from req_builder.viz.cli import load_artifacts
        from req_builder.viz.views import v2_discussion

        root = Path(__file__).resolve().parents[1] / "examples" / "results" / "DEMO-WX"
        events = load_events(root / "events.jsonl")
        bundle = load_artifacts(root / "artifacts")
        markup, height = v2_discussion.render(ReplayWindow.of(events), bundle)
        self.assertEqual(markup.count("条<"), 3)
        self.assertIn("4 条", markup)   # statements
        self.assertIn("2 条", markup)   # votes
        self.assertIn("discussion_minutes.json", markup)
        self.assertGreater(height, 40)


if __name__ == "__main__":
    unittest.main()
