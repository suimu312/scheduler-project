"""判据层单测（D / O3）。

只测三件判据层的承诺，不测业务：
1. 缺证据时返回 None，不补 0、不假装判过；
2. 阈值边界对不对（warn/block 的分界、retry 的 >3、相似度的 0.85）；
3. 同一份输入重复判，结论必须一致（纯函数，无副作用）。

运行：python -m unittest tests.test_observability_signals -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from observability import signals as sig  # noqa: E402


def charged(stage: str, used: int) -> dict:
    return {
        "event_type": "budget.charged",
        "stage": stage,
        "budget_used": used,
        "event_id": f"EVT-{stage}-{used}",
    }


def task_event(kind: str, task_id: str) -> dict:
    return {"event_type": kind, "task_id": task_id, "stage": "P6"}


def statement(agent_id: str, quote: str) -> dict:
    return {"agent_id": agent_id, "quote": quote}


class TestBudgetHeadroom(unittest.TestCase):
    """OBS-01：余量 < 20% 报 warn，<= 0 报 block，P7 免 warn。"""

    def test_missing_events_returns_none(self):
        """没有预算事件就是判不了，不能返回"余量充足"。"""
        self.assertIsNone(sig.budget_headroom([], {"global_limit": 30}))

    def test_healthy_budget_not_flagged(self):
        """20/30 余量 33%，不该报。"""
        self.assertIsNone(sig.budget_headroom([charged("P5", 20)], {"global_limit": 30}))

    def test_exhausted_budget_blocks(self):
        hit = sig.budget_headroom([charged("P5", 17)], {"global_limit": 17})
        self.assertIsNotNone(hit)
        self.assertEqual(hit["level"], sig.LEVEL_BLOCK)
        self.assertEqual(hit["headroom"], 0.0)

    def test_low_headroom_warns_below_threshold(self):
        hit = sig.budget_headroom([charged("P5", 16)], {"global_limit": 17})
        self.assertEqual(hit["level"], sig.LEVEL_WARN)

    def test_p7_is_exempt_from_warn(self):
        """P7 收尾阶段余量低是常态，只报 block、不报 warn。"""
        self.assertIsNone(sig.budget_headroom([charged("P7", 16)], {"global_limit": 17}))
        blocked = sig.budget_headroom([charged("P7", 17)], {"global_limit": 17})
        self.assertEqual(blocked["level"], sig.LEVEL_BLOCK)

    def test_zero_limit_is_block_not_division_error(self):
        hit = sig.budget_headroom([charged("P5", 0)], {"global_limit": 0})
        self.assertEqual(hit["level"], sig.LEVEL_BLOCK)


class TestTaskRetry(unittest.TestCase):
    """OBS-03：retry = 调用次数 - 1，> 3 才超限；要指得准，不连坐。"""

    def test_no_task_events_returns_empty(self):
        self.assertEqual(sig.task_retry([charged("P5", 3)]), [])

    def test_within_limit_not_flagged(self):
        events = [task_event("task.dispatched", "DEV-001")]
        events += [task_event("model.call", "DEV-001")] * 4  # retry = 3，刚好不超
        events.append(task_event("task.completed", "DEV-001"))
        self.assertEqual(sig.task_retry(events), [])

    def test_over_limit_flags_exact_task(self):
        events = [task_event("task.dispatched", "DEV-001")]
        events += [task_event("model.call", "DEV-001")] * 5  # retry = 4
        events += [task_event("task.dispatched", "DEV-002")]
        events += [task_event("model.call", "DEV-002")] * 3
        events.append(task_event("task.completed", "DEV-002"))
        hits = sig.task_retry(events)
        self.assertEqual([h["task_id"] for h in hits], ["DEV-001"])
        self.assertEqual(hits[0]["retry"], 4)

    def test_unfinished_task_still_settled(self):
        """校验器恒失败时卡永远不 completed，收尾也要结算，不能漏判。"""
        events = [task_event("task.dispatched", "DEV-009")]
        events += [task_event("model.call", "DEV-009")] * 5
        hits = sig.task_retry(events)
        self.assertEqual(hits[0]["state"], "unfinished")

    def test_calls_outside_dispatch_window_ignored(self):
        """没有 dispatched 打头的调用归不到卡上，跳过而不是硬算。"""
        events = [task_event("model.call", "DEV-003")] * 6
        self.assertEqual(sig.task_retry(events), [])


class TestStatementSimilarity(unittest.TestCase):
    """OBS-06：相似度超阈值判无效；无效 ≥ 2 才 warn，1 条只 info。"""

    def test_single_statement_cannot_be_similar(self):
        self.assertIsNone(sig.statement_similarity([statement("A1", "先做最小闭环")]))

    def test_distinct_statements_not_flagged(self):
        rows = [statement("A1", "先做最小闭环"), statement("A3", "复用开源消息组件更稳")]
        self.assertIsNone(sig.statement_similarity(rows))

    def test_one_duplicate_pair_already_warns(self):
        """一对发言雷同 = 两条发言无效，已经够 warn 门槛（yaml：单轮无效发言 ≥ 2）。

        也就是说 `info` 分支在「两两比较」口径下不可达：只要有一对命中就必然 ≥ 2 条。
        这是设计使然，不是漏测；若以后改成按「对数」计数，info 才会用得上。
        """
        rows = [statement("A1", "先做最小闭环"), statement("A2", "先做最小闭环")]
        hit = sig.statement_similarity(rows)
        self.assertEqual(hit["level"], sig.LEVEL_WARN)
        self.assertEqual(hit["invalid_count"], 2)

    def test_two_duplicate_pairs_warns(self):
        rows = [
            statement("A1", "先做最小闭环"),
            statement("A2", "复用开源组件"),
            statement("A3", "复用开源组件"),
            statement("A4", "先做最小闭环"),
        ]
        hit = sig.statement_similarity(rows)
        self.assertEqual(hit["level"], sig.LEVEL_WARN)
        self.assertEqual(len(hit["pairs"]), 2)

    def test_degraded_mode_is_reported(self):
        """没有 embedding 时要在结论里标明是降级口径，不能让人以为算的是向量余弦。"""
        hit = sig.statement_similarity(
            [statement("A1", "同一句话"), statement("A2", "同一句话")]
        )
        self.assertEqual(hit["similarity_mode"], "lexical_degraded")


class TestSnapshot(unittest.TestCase):
    """三条判据一起跑：干净的输入必须一条都不报。"""

    def test_clean_run_yields_no_signal(self):
        self.assertEqual(
            sig.snapshot([charged("P5", 5)], {"global_limit": 30}, [statement("A1", "甲")]), {}
        )

    def test_pure_function_is_repeatable(self):
        events = [charged("P5", 16)]
        ledger = {"global_limit": 17}
        first = sig.budget_headroom(events, ledger)
        for _ in range(5):
            self.assertEqual(sig.budget_headroom(events, ledger)["headroom"], first["headroom"])


if __name__ == "__main__":
    unittest.main()
