import copy
import json
import math
import unittest

from src.agents.examples.fixture import ScriptedModel, make_context
from src.agents import (AgentError, build_convocation, estimate_similarity,
                        evaluate_user_value, load_config, render_prompt,
                        run_discussion, validate_config)
from src.agents.core import artifact


def next_context(step="query"):
    ctx = make_context()
    result = run_discussion(ctx, ScriptedModel())
    ctx["artifacts"].update(result["artifacts"])
    ctx["budget_left"] -= result["usage"]
    stances = result["artifacts"]["discussion_minutes"]["data"]["statements"]
    register = {"items": [{"conflict_id": "CFL-001", "extensions": {"issue_id": "ISS-001"}, "topic_id": "granularity", "stances": [{"source_ref": "discussion_minutes:" + s["id"]} for s in stances]}]}
    ctx["artifacts"]["conflict_register"] = artifact(ctx["run_id"], "P3", "conflict_register", register)
    ctx["config"]["protocol_step"] = "query"
    if step == "vote":
        result = run_discussion(ctx, ScriptedModel())
        ctx["budget_left"] -= result["usage"]
        ctx["artifacts"].update(result["artifacts"])
        ctx["config"]["protocol_step"] = "vote"
    return ctx


class RolesTest(unittest.TestCase):
    def test_role_mapping_and_missing_challenge(self):
        cfg = load_config()
        self.assertEqual(cfg["roles"]["A6"]["family"], "processing")
        cfg["roles"]["A2"]["stance"]["must_challenge"] = ""
        with self.assertRaises(AgentError):
            validate_config(cfg)

    def test_conflicting_knowledge(self):
        cfg = load_config()
        cfg["roles"]["A2"]["knowledge"]["visible"].append("budget")
        with self.assertRaises(AgentError):
            validate_config(cfg)

    def test_prompt_seals_other_stances_and_private_materials(self):
        ctx = make_context()
        ctx["artifacts"]["discussion_minutes"] = artifact(ctx["run_id"], "P3", "discussion_minutes", {"statements": [{"claim": "SHOULD_NOT_APPEAR"}]})
        request = render_prompt("A2", "statement", ctx)
        body = json.loads(request["user"])
        self.assertNotIn("published_claims", body)
        self.assertNotIn("SHOULD_NOT_APPEAR", request["user"])
        self.assertEqual({m["category"] for m in body["visible_materials"]}, {"spec", "user_feedback"})
        with self.assertRaises(AgentError):
            render_prompt("A5", "statement", ctx)

    def test_material_provenance_must_resolve(self):
        ctx = make_context()
        ctx["artifacts"]["material_catalog"]["data"]["items"][0]["source_refs"] = ["design_spec:FEA-999"]
        with self.assertRaises(AgentError):
            render_prompt("A2", "statement", ctx)


class ConvocationTest(unittest.TestCase):
    def test_low_confidence_and_required_roles(self):
        ctx = make_context("P2")
        out = build_convocation(ctx)
        rows = out["artifacts"]["convocation_table"]["data"]["items"]
        self.assertEqual(rows[1]["invited"], ["A1", "A2", "A3", "A4", "A5", "A6"])
        for row in rows:
            self.assertTrue({"A3", "A4"} <= set(row["invited"]))
        self.assertEqual(out["usage"], 0)

    def test_missing_classification_is_not_guessed(self):
        ctx = make_context("P2")
        ctx["artifacts"]["classification"]["data"]["items"].pop()
        with self.assertRaises(AgentError) as error:
            build_convocation(ctx)
        self.assertEqual(error.exception.code, "E_ARTIFACT_MISSING")


class SimilarityTest(unittest.TestCase):
    def test_duplicate_flags_without_embedding(self):
        out = estimate_similarity("核心场景先交付", "核心场景先交付")
        self.assertTrue(out["flagged"])
        self.assertTrue(out["degraded"])
        self.assertIsNone(out["semantic_score"])

    def test_semantic_or_lexical(self):
        out = estimate_similarity("发布商品", "身份接口", lambda _: [[1, 0], [1, 0]])
        self.assertTrue(out["flagged"])
        self.assertEqual(out["lexical_score"], 0)
        self.assertFalse(out["degraded"])

    def test_broken_embedding_degrades(self):
        def broken(_):
            raise RuntimeError("offline")
        self.assertTrue(estimate_similarity("相同文本", "相同文本", broken)["flagged"])
        for vectors in ([[0, 0], [0, 0]], [[1], [1, 2]], [[math.nan], [1]]):
            self.assertTrue(estimate_similarity("a", "b", lambda _, v=vectors: v)["degraded"])


class DiscussionTest(unittest.TestCase):
    def test_full_protocol_and_immutability(self):
        ctx = make_context()
        snapshot = copy.deepcopy(ctx)
        first = run_discussion(ctx, ScriptedModel())
        self.assertEqual(ctx, snapshot)
        self.assertEqual(first["usage"], 4)
        ctx = next_context("vote")
        out = run_discussion(ctx, ScriptedModel())
        data = out["artifacts"]["discussion_minutes"]["data"]
        self.assertEqual([len(data[key]) for key in ("statements", "qa", "votes")], [4, 4, 4])
        self.assertEqual(data["tally"], {"FOR": 3, "AGAINST": 1, "ABSTAIN": 0})
        self.assertNotIn("winner", data)
        self.assertEqual(ctx["budget_left"] - out["usage"], 0)
        self.assertTrue(all(e["type"] in ("agent.vote", "guard.signal") for e in out["events"]))

    def test_query_requires_D_register(self):
        ctx = next_context()
        del ctx["artifacts"]["conflict_register"]
        with self.assertRaises(AgentError) as error:
            run_discussion(ctx, ScriptedModel())
        self.assertEqual(error.exception.code, "E_ARTIFACT_MISSING")

    def test_vote_cannot_skip_query(self):
        ctx = next_context()
        ctx["config"]["protocol_step"] = "vote"
        with self.assertRaises(AgentError):
            run_discussion(ctx, ScriptedModel())

    def test_budget_is_hard_limit(self):
        ctx = make_context()
        ctx["budget_left"] = 2
        out = run_discussion(ctx, ScriptedModel())
        self.assertEqual(out["usage"], 2)
        self.assertTrue(out["artifacts"]["discussion_minutes"]["data"]["suspended"])
        ctx["budget_left"] = 0
        out = run_discussion(ctx, ScriptedModel())
        self.assertEqual(out["usage"], 0)

    def test_forbidden_evidence_retried_then_absent(self):
        def bad(request, timeout):
            out = ScriptedModel()(request, timeout)
            if request["agent_id"] == "A2":
                out["source_refs"] = ["material_catalog:MAT-004"]
            return out
        out = run_discussion(make_context(), bad)
        data = out["artifacts"]["discussion_minutes"]["data"]
        self.assertEqual(out["usage"], 5)
        self.assertNotIn("A2", [s["agent_id"] for s in data["statements"]])
        self.assertIn("E_EVIDENCE_SCOPE", [s["code"] for s in data["guard_signals"]])

    def test_invalid_output_gets_one_retry(self):
        seen = set()
        def flaky(request, timeout):
            aid = request["agent_id"]
            if aid == "A2" and aid not in seen:
                seen.add(aid)
                return {"position": "FOR"}
            return ScriptedModel()(request, timeout)
        out = run_discussion(make_context(), flaky)
        self.assertEqual(out["usage"], 5)
        self.assertEqual(len(out["artifacts"]["discussion_minutes"]["data"]["statements"]), 4)

    def test_duplicate_rewrites_exhausted_suspends(self):
        def duplicate(request, timeout):
            out = ScriptedModel()(request, timeout)
            out["claim"] = "所有任务必须先完成完整交易闭环才能交付"
            return out
        out = run_discussion(make_context(), duplicate)
        data = out["artifacts"]["discussion_minutes"]["data"]
        self.assertTrue(data["suspended"])
        self.assertIn("E_GROUPTHINK", [s["code"] for s in data["guard_signals"]])
        self.assertLessEqual(out["usage"], 12)

    def test_blank_abstain_is_invalid(self):
        def blank(request, timeout):
            out = ScriptedModel()(request, timeout)
            if request["agent_id"] in ("A1", "A2"):
                out.update(ballot="ABSTAIN", reason=" ")
            return out
        ctx = next_context("vote")
        out = run_discussion(ctx, blank)
        data = out["artifacts"]["discussion_minutes"]["data"]
        self.assertEqual(data["valid_vote_count"], 2)
        self.assertTrue(data["suspended"])

    def test_tie_records_without_adjudicating(self):
        def tie(request, timeout):
            out = ScriptedModel()(request, timeout)
            out["ballot"] = "FOR" if request["agent_id"] in ("A1", "A2") else "AGAINST"
            return out
        data = run_discussion(next_context("vote"), tie)["artifacts"]["discussion_minutes"]["data"]
        self.assertIn("CONTROVERSIAL", [s["code"] for s in data["guard_signals"]])
        self.assertNotIn("winner", data)

    def test_timeout_no_charge_and_other_roles_continue(self):
        def timeout(request, timeout):
            if request["agent_id"] == "A2":
                raise TimeoutError()
            return ScriptedModel()(request)
        out = run_discussion(make_context(), timeout)
        self.assertEqual(out["usage"], 3)
        self.assertEqual(len(out["artifacts"]["discussion_minutes"]["data"]["statements"]), 3)

    def test_no_cross_run_or_fourth_round(self):
        ctx = make_context()
        ctx["artifacts"]["design_spec"]["meta"]["run_id"] = "OTHER"
        with self.assertRaises(AgentError):
            run_discussion(ctx, ScriptedModel())
        ctx = make_context()
        ctx["config"]["round"] = 4
        with self.assertRaises(AgentError):
            run_discussion(ctx, ScriptedModel())

    def test_changed_template_fingerprint_rejected(self):
        ctx = next_context()
        ctx["artifacts"]["discussion_minutes"]["data"]["extensions"]["config_fingerprint"] = "outdated"
        with self.assertRaises(AgentError) as error:
            run_discussion(ctx, ScriptedModel())
        self.assertEqual(error.exception.code, "E_ROLE_INVALID")

    def test_nonopposing_conflict_rejected(self):
        ctx = next_context()
        ctx["artifacts"]["conflict_register"]["data"]["items"][0]["stances"] = ctx["artifacts"]["conflict_register"]["data"]["items"][0]["stances"][:2]
        with self.assertRaises(AgentError):
            run_discussion(ctx, ScriptedModel())


class ValueTest(unittest.TestCase):
    def test_priority_is_advisory_and_missing_value_not_guessed(self):
        ctx = make_context("P6")
        snapshot = copy.deepcopy(ctx)
        out = evaluate_user_value(ctx)
        rows = out["artifacts"]["user_value_assessment"]["data"]["items"]
        self.assertEqual([r["suggested_priority"] for r in rows], ["M", "M", "S", "C"])
        self.assertEqual(ctx, snapshot)
        del ctx["artifacts"]["design_spec"]["data"]["features"][3]["user_value"]
        rows = evaluate_user_value(ctx)["artifacts"]["user_value_assessment"]["data"]["items"]
        self.assertIsNone(rows[3]["suggested_priority"])
        self.assertTrue(rows[3]["needs_confirmation"])

    def test_constraint_task_and_unknown_source(self):
        ctx = make_context("P6")
        task = ctx["artifacts"]["dev_task_list"]["data"]["items"][0]
        task["source_refs"] = ["design_spec:CON-001"]
        rows = evaluate_user_value(ctx)["artifacts"]["user_value_assessment"]["data"]["items"]
        self.assertEqual(rows[0]["suggested_priority"], "M")
        task["source_refs"] = ["design_spec:FEA-999"]
        with self.assertRaises(AgentError):
            evaluate_user_value(ctx)


if __name__ == "__main__":
    unittest.main()
