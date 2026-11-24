"""F：跨 run 对比页的回归用例（只读 examples/results/，不改产物）。"""

from __future__ import annotations

from pathlib import Path
import unittest

from src.baseline_mgr.compare import collect_run, discover_runs, render_html

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = REPO_ROOT / "examples" / "results"


class CompareTest(unittest.TestCase):
    def test_collect_real_run(self) -> None:
        run = collect_run(RUN_ROOT / "REAL")
        self.assertEqual(run["run_id"], "REAL")
        self.assertEqual(run["status"], "frozen")
        self.assertGreater(run["artifact_count"], 0)
        self.assertGreater(run["task_count"], 0)
        self.assertEqual(run["model_calls"], 0)
        self.assertIn("baseline", run["present_artifacts"])
        self.assertIn("confirmation_sheet", run["present_artifacts"])

    def test_discover_explicit_runs(self) -> None:
        runs = discover_runs(RUN_ROOT, ["DEMO", "REAL"])
        self.assertEqual([r["run_id"] for r in runs], ["DEMO", "REAL"])

    def test_render_html_has_metrics_and_links(self) -> None:
        runs = discover_runs(RUN_ROOT, ["DEMO", "REAL"])
        html = render_html(runs, "测试对比页", "2026-11-24T00:00:00+08:00")
        self.assertIn("model.call 事件", html)
        self.assertIn("REAL/artifacts/baseline.json", html)
        self.assertIn("测试对比页", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)


if __name__ == "__main__":
    unittest.main()
