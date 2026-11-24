import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from src.agents.agent_signals import build_signals, redact, validate_evidence
from src.agents.evidence import ObservedProvider, run_package, verify_package
from src.agents.tests.test_gateway import native, scripted_provider
from src.scheduler.agents_bridge import run
from src.scheduler.llm_gateway import LLMGateway


class EvidenceTest(unittest.TestCase):
    def complete(self):
        return run(native(), gateway=LLMGateway(scripted_provider))

    def test_conflict_has_exact_original_quotes(self):
        result = self.complete()
        self.assertEqual(result.status, "ok", result.errors)
        minutes = result.artifacts["discussion_minutes"]["data"]
        register = result.artifacts["conflict_register"]["data"]
        self.assertEqual(validate_evidence(minutes, register, True), [])
        signals = result.artifacts["agent_signals"]["data"]
        self.assertTrue(signals["conflict_evidence"])
        self.assertEqual(len(signals["records"]), 12)
        by_id = {"discussion_minutes:"+s["id"]: s for s in minutes["statements"]}
        for c in signals["conflict_evidence"]:
            self.assertTrue(c["traceable_to_opposing_quotes"])
            for q in c["quotes"]:
                self.assertEqual(q["quote"], by_id[q["statement_ref"]]["claim"])

    def test_independent_prompts_and_vote_matrix(self):
        minutes = self.complete().artifacts["discussion_minutes"]["data"]
        snapshots = minutes["extensions"]["prompt_snapshots"]
        initial = [s for s in snapshots if s["step"] == "statement"]
        self.assertEqual(len(initial), 4)
        self.assertTrue(all(not s["published_statement_ids"] and not s["published_qa_ids"] for s in initial))
        self.assertEqual(len({tuple(s["visible_source_refs"]) for s in initial}), 4)
        self.assertEqual(len(minutes["vote_matrix"]), 4)
        self.assertEqual(minutes["meeting_summary"]["tally"], {"FOR": 3, "AGAINST": 1, "ABSTAIN": 0})

    def test_missing_vote_and_legacy_isolation_fail(self):
        r = self.complete()
        m = copy.deepcopy(r.artifacts["discussion_minutes"]["data"])
        m["votes"].pop()
        del m["extensions"]["prompt_snapshots"]
        errors = validate_evidence(m, r.artifacts["conflict_register"]["data"])
        self.assertTrue(any("投票" in e for e in errors))
        self.assertTrue(any("快照" in e for e in errors))

    def test_redaction_preserves_refs_and_does_not_mutate(self):
        source = {"api_key": "sensitive-value", "nested": ["Bearer ABCDEFGHI", "联系13800138000 test@example.com", "ark-1234567890123456"], "ref": "discussion_minutes:ST-ISS-001-1-A1"}
        safe = redact(source)
        self.assertEqual(safe["ref"], source["ref"])
        for secret in ("sensitive-value", "ABCDEFGHI", "13800138000", "test@example.com", "ark-1234567890123456"):
            self.assertNotIn(secret, json.dumps(safe))
        self.assertEqual(source["api_key"], "sensitive-value")

    def test_observer_concurrency_and_real_event_semantics(self):
        emitted = []
        observed = ObservedProvider(lambda *a: '{}', 'run', 'offline', emit=emitted.append)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: observed('A1', '{"step":"statement"}', 'P3'), range(4)))
        self.assertEqual(len(observed.events), 4)
        self.assertEqual(len(emitted), 4)
        self.assertTrue(all(e["event_type"] == "fixture.call" for e in emitted))
        self.assertTrue(all(s["duration_ms"] >= 0 for s in observed.spans))
        self.assertNotIn('prompt', json.dumps(observed.spans))

    def test_package_artifacts_and_redacted_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_package(tmp)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["actual_model_attempts"], 0)
            for name in ('discussion_minutes.json','conflict_register.json','agent_signals.json','vote_matrix.json','acceptance.json','spans.jsonl','events.jsonl'):
                self.assertTrue((Path(tmp)/name).is_file())
            events = (Path(tmp)/'events.jsonl').read_text(encoding='utf-8')
            self.assertNotIn('"model.call"', events)
            self.assertEqual(verify_package(tmp), [])
            vote_path = Path(tmp)/'vote_matrix.json'
            vote_path.write_text('{"data": []}', encoding='utf-8')
            self.assertTrue(any('投票矩阵' in e for e in verify_package(tmp)))
            with self.assertRaises(ValueError):
                run_package(tmp)

    def test_interruption_and_degraded_are_distinct(self):
        minutes = {"guard_signals": [{"code": "SIMILARITY_DEGRADED", "action": "lexical_detection_active"},
                                    {"code": "E_GROUPTHINK", "agent_id": "A3", "action": "suspend"}]}
        signals = build_signals(minutes)["interruptions"]
        self.assertFalse(signals[0]["requires_scheduler_action"])
        self.assertTrue(signals[1]["requires_scheduler_action"])
        self.assertEqual(signals[1]["signal_ref"], "guard_signals:1")

    def test_authorized_attempt_limit_includes_failures(self):
        from src.agents.core import AgentError
        def fail(*args):
            raise TimeoutError('sensitive-provider-detail')
        observed = ObservedProvider(fail, 'run', 'real', max_calls=1)
        with self.assertRaises(TimeoutError):
            observed('A1', '{"step":"statement"}', 'P3')
        with self.assertRaises(AgentError):
            observed('A1', '{"step":"statement"}', 'P3')
        self.assertEqual(observed.attempts, 1)
        self.assertEqual(observed.events[0]['event_type'], 'model.call')
        self.assertEqual(observed.spans[0]['status'], 'error')
        self.assertNotIn('sensitive-provider-detail', json.dumps(observed.events))

    def test_recover_only_missing_timeout_vote_keeps_source(self):
        from src.agents.examples.fixture import ScriptedModel
        from src.agents.recover_vote import recover
        original = ScriptedModel.__call__
        def sometimes_timeout(model, request, timeout=120):
            if request['agent_id']=='A4' and request['step']=='vote':
                raise TimeoutError()
            return original(model,request,timeout)
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp)/'source', Path(tmp)/'recovered'
            with patch.object(ScriptedModel,'__call__',sometimes_timeout):
                result = run_package(source,sample='boundary')
            self.assertEqual(result['status'],'failed')
            before = (source/'discussion_minutes.json').read_bytes()
            self.assertEqual(recover(source,output,provider=scripted_provider,mode='offline'),[])
            self.assertEqual(before,(source/'discussion_minutes.json').read_bytes())
            m=json.loads((output/'discussion_minutes.json').read_text(encoding='utf-8'))['data']
            self.assertEqual(len(m['votes']),4)
            self.assertTrue(any(s['code']=='E_AGENT_UNAVAILABLE' for s in m['guard_signals']))


if __name__ == '__main__':
    unittest.main()
