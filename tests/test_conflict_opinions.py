"""P5 意见处理表单测（D｜本周补考项）。

测四件事，都是"评审能不能被扛住"的关键：
1. 三态判定跟产物对得上（ACCEPTED / REJECTED / PENDING 各有触发条件，不是随机分布）；
2. 每条理由都含具体 id 或数字，空话理由必须被 validate_opinions 拦下；
3. P5 手上没有的证据（任务卡）一律 PENDING，不猜、不补；
4. 说不清"不改会怎样"的意见按 §3.1 丢弃，不进制表。

字段与枚举照 `docs/review-conflict.md §2.1 ReviewOpinion` 的设计定稿，
不另起 adopted / deferred 这类同义词。

运行：python -m unittest tests.test_conflict_opinions -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from conflict_review import opinions as op  # noqa: E402


def challenge(ref: str, cid: str = "CHG-R001", category: str = "solution", fixable: bool = True) -> dict:
    return {
        "challenge_id": cid,
        "description": "这个功能项没人接",
        "impact_ref": ref,
        "consequence": "进 P6 会漏功能" if fixable else "",
        "severity": "major",
        "category": category,
    }


def comparison(rows: list[dict]) -> dict:
    return {"rows": rows}


def row(feature_id: str, adopted: str = "", options: int = 2) -> dict:
    return {
        "feature_id": feature_id,
        "adopted": {"solution_id": adopted} if adopted else None,
        "options": [{"solution_id": f"SOL_{i}"} for i in range(options)],
    }


def register(conflicts: list[dict]) -> dict:
    return {
        "conflicts": conflicts,
        "open_count": sum(1 for c in conflicts if c.get("status") == "open"),
    }


class TestFeatureJudgement(unittest.TestCase):
    """FEA-xxx：看这一行有没有被采纳的方案。"""

    def test_no_adopted_solution_is_accepted(self):
        """候选方案都在、但没有一个被采纳 → 意见成立。"""
        out = op.build_opinions([challenge("FEA-001")], comparison([row("FEA-001")]), register([]))
        self.assertEqual(out[0]["verdict"], op.ACCEPTED)
        self.assertIn("FEA-001", out[0]["reason"])
        self.assertIn("2 个候选", out[0]["reason"])

    def test_adopted_solution_rejects_opinion(self):
        """已经采纳了方案 → 意见与产物不符，不采纳。"""
        out = op.build_opinions(
            [challenge("FEA-001")], comparison([row("FEA-001", adopted="SOL_B")]), register([])
        )
        self.assertEqual(out[0]["verdict"], op.REJECTED)
        self.assertIn("SOL_B", out[0]["reason"])

    def test_missing_feature_is_pending(self):
        """方案对比里压根没这个功能项 → 指错对象，待议。"""
        out = op.build_opinions([challenge("FEA-999")], comparison([row("FEA-001")]), register([]))
        self.assertEqual(out[0]["verdict"], op.PENDING)
        self.assertIn("FEA-999", out[0]["reason"])


class TestConflictJudgement(unittest.TestCase):
    """CFL-xxx：看这条冲突是不是已经裁决掉了。"""

    def test_open_conflict_is_accepted(self):
        out = op.build_opinions(
            [challenge("CFL-001")],
            comparison([]),
            register([{"conflict_id": "CFL-001", "status": "open", "level": "L1_evidence"}]),
        )
        self.assertEqual(out[0]["verdict"], op.ACCEPTED)
        self.assertIn("open_count=1", out[0]["reason"])

    def test_resolved_conflict_is_rejected(self):
        out = op.build_opinions(
            [challenge("CFL-001")],
            comparison([]),
            register([{"conflict_id": "CFL-001", "status": "resolved", "level": "L2_weighted_vote"}]),
        )
        self.assertEqual(out[0]["verdict"], op.REJECTED)
        self.assertIn("L2_weighted_vote", out[0]["reason"])

    def test_suspended_conflict_is_pending(self):
        """升级到 L3 还没裁决 → 定不了性，待议，不许硬判。"""
        out = op.build_opinions(
            [challenge("CFL-001")],
            comparison([]),
            register([{"conflict_id": "CFL-001", "status": "suspended", "level": "L3_escalate"}]),
        )
        self.assertEqual(out[0]["verdict"], op.PENDING)
        self.assertIn("suspended", out[0]["reason"])


class TestPendingWithoutEvidence(unittest.TestCase):
    """P5 拿不到的证据一律待议——这是"算不出来不补"的具体落地。"""

    def test_task_ref_is_pending_at_p5(self):
        out = op.build_opinions([challenge("DEV-001")], comparison([]), register([]))
        self.assertEqual(out[0]["verdict"], op.PENDING)
        self.assertIn("DEV-001", out[0]["reason"])
        self.assertIn("P6", out[0]["action"])

    def test_empty_target_is_dropped(self):
        """指不到具体条目的意见按 §3.1 丢弃，不许以「待议」的名义混进表里。"""
        out = op.build_opinions([challenge("")], comparison([]), register([]))
        self.assertEqual(out, [])
        self.assertEqual(op.count_dropped([challenge("")]), 1)

    def test_vague_target_is_dropped(self):
        """target 指向"整体方案"也算指不到具体条目。"""
        out = op.build_opinions([challenge("整体方案")], comparison([]), register([]))
        self.assertEqual(out, [])


class TestAcceptanceGate(unittest.TestCase):
    """§3.1：说不清"不改会怎样"的意见直接丢弃。"""

    def test_opinion_without_if_unfixed_is_dropped(self):
        out = op.build_opinions(
            [challenge("FEA-001", cid="C1", fixable=False), challenge("FEA-002", cid="C2")],
            comparison([row("FEA-001"), row("FEA-002")]),
            register([]),
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["target"], "FEA-002")
        self.assertEqual(op.count_dropped([challenge("A", fixable=False)]), 1)

    def test_design_fields_are_present(self):
        """八个设计字段一个都不能少，扩展字段排在后面。"""
        out = op.build_opinions([challenge("FEA-001")], comparison([row("FEA-001")]), register([]))
        for field in ("id", "author", "target", "problem", "impact", "if_unfixed", "severity", "verdict"):
            self.assertIn(field, out[0])
        self.assertEqual(out[0]["severity"], "MAJOR")  # 对外用设计的大写枚举
        self.assertEqual(out[0]["author"], "A4")


class TestValidation(unittest.TestCase):
    """理由不含可核对内容的，必须被拦下来。"""

    def test_generated_reasons_all_pass(self):
        rows = [row("FEA-001", adopted="SOL_A"), row("FEA-002")]
        confs = [
            {"conflict_id": "CFL-001", "status": "open", "level": "L1_evidence"},
            {"conflict_id": "CFL-002", "status": "suspended", "level": "L3_escalate"},
        ]
        targets = ["FEA-001", "FEA-002", "FEA-999", "CFL-001", "CFL-002", "DEV-001"]
        out = op.build_opinions(
            [challenge(t, cid=f"CHG-{i}") for i, t in enumerate(targets)],
            comparison(rows),
            register(confs),
        )
        self.assertEqual(op.validate_opinions(out), [])
        self.assertEqual(len(out), len(targets))

    def test_vague_reason_is_rejected(self):
        bad = [
            {
                "id": "R-001",
                "target": "FEA-001",
                "if_unfixed": "会漏功能",
                "verdict": "ACCEPTED",
                "reason": "整体不太好",  # 没有 id、没有数字
            }
        ]
        errors = op.validate_opinions(bad)
        self.assertTrue(any("等于没写理由" in e for e in errors))

    def test_lowercase_verdict_is_rejected(self):
        bad = [
            {
                "id": "R-001",
                "target": "FEA-001",
                "if_unfixed": "会漏功能",
                "verdict": "adopted",  # 不是设计里的枚举
                "reason": "FEA-001 有 2 个方案但都没被采纳",
            }
        ]
        errors = op.validate_opinions(bad)
        self.assertTrue(any("处置必须是" in e for e in errors))

    def test_duplicate_target_is_rejected(self):
        out = op.build_opinions(
            [challenge("FEA-001", cid="C1"), challenge("FEA-001", cid="C2")],
            comparison([row("FEA-001")]),
            register([]),
        )
        errors = op.validate_opinions(out)
        self.assertTrue(any("处理了两次" in e for e in errors))


class TestSummary(unittest.TestCase):
    def test_three_states_are_counted(self):
        rows = [row("FEA-001", adopted="SOL_A"), row("FEA-002")]
        out = op.build_opinions(
            [challenge("FEA-001", cid="C1"), challenge("FEA-002", cid="C2"), challenge("DEV-001", cid="C3")],
            comparison(rows),
            register([]),
        )
        summary = op.summarize(out)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary[op.REJECTED], 1)  # FEA-001 已采纳
        self.assertEqual(summary[op.ACCEPTED], 1)  # FEA-002 无人采纳
        self.assertEqual(summary[op.PENDING], 1)  # DEV-001 无任务卡
        self.assertFalse(summary["unhandled"])

    def test_empty_table_is_not_unhandled(self):
        """一条意见都没有时，空表是正常结果，不该被标成"漏处理"。"""
        self.assertFalse(op.summarize([])["unhandled"])

    def test_dropped_count_is_reported(self):
        """被丢弃的条数要出现在统计里，不能悄悄消失。"""
        summary = op.summarize([], dropped=2)
        self.assertEqual(summary["dropped_unfixable"], 2)


if __name__ == "__main__":
    unittest.main()
