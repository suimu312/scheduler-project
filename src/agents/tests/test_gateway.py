import copy
import io
import json
import tempfile
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from src.agents.core import AgentError
from src.agents.examples.fixture import ScriptedModel, make_context
from src.agents.providers import ChatCompletionsProvider, SchedulerGatewayModel, parse_object
from src.agents.stage import run, remaining
from src.scheduler.agents_bridge import run as run_bridge
from src.scheduler.context import BudgetLedger, RunConfig, StageContext, StageResult, make_artifact, validate_stage_result
from src.scheduler.llm_gateway import LLMGateway
from src.scheduler.modules import resolve


def native(stage="P3"):
    sample = make_context(stage)
    ledger = BudgetLedger(global_limit=30, per_stage_limit={"P2": 4, "P3": 12})
    return StageContext(run_id=sample["run_id"], stage=stage, round_index=1, dispatch_plan=None,
                        artifacts=sample["artifacts"], config={"agents": sample["config"]}, ledger=ledger)


def scripted_provider(agent_id, prompt, stage):
    request = json.loads(prompt)
    if agent_id == "classifier":
        return json.dumps(make_context("P2")["artifacts"]["classification"]["data"], ensure_ascii=False)
    return json.dumps(ScriptedModel()(request), ensure_ascii=False)


class ProviderTest(unittest.TestCase):
    def test_chat_request_and_response(self):
        response = io.BytesIO(json.dumps({"choices": [{"message": {"content": '{"ok": true}'}}]}).encode())
        provider = ChatCompletionsProvider("https://ark.cn-beijing.volces.com/api/v3", "test-key-not-real", "test-model")
        with patch("src.agents.providers.urllib.request.urlopen", return_value=response) as transport:
            returned = provider("A1", json.dumps({"system": "JSON only", "user": "test", "timeout_seconds": 10}), "P3")
        request = transport.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "https://ark.cn-beijing.volces.com/api/v3/chat/completions")
        self.assertEqual(body["model"], "test-model")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(transport.call_args.kwargs["timeout"], 10)
        self.assertEqual(json.loads(returned), {"ok": True})

    def test_secrets_not_in_http_error(self):
        provider = ChatCompletionsProvider("https://example.com/v1", "test-secret", "model")
        with patch("src.agents.providers.urllib.request.urlopen", side_effect=urllib.error.HTTPError("https://example.com", 401, "test-secret", {}, None)):
            with self.assertRaises(AgentError) as error:
                provider("A1", '{"system":"json","user":"test"}', "P3")
        self.assertIn("401", str(error.exception))
        self.assertNotIn("test-secret", str(error.exception))

    def test_missing_config_and_http_remote_rejected(self):
        with patch("src.agents.providers.setting", return_value=""):
            with self.assertRaises(AgentError):
                ChatCompletionsProvider.from_env()
        with self.assertRaises(AgentError):
            ChatCompletionsProvider("http://remote.example/v1", "key", "model")

    def test_empty_content_and_fences(self):
        self.assertEqual(parse_object('```json\n{"ok":true}\n```'), {"ok": True})
        for value in ("", "[]", "[offline:A1:P3:123]", '{"ok":'):
            with self.assertRaises(AgentError):
                parse_object(value)

    def test_offline_gateway_cannot_masquerade_as_model(self):
        model = SchedulerGatewayModel(LLMGateway())
        with self.assertRaises(AgentError):
            model({"agent_id": "A1", "system": "json", "user": "test", "step": "statement"})

    def test_gateway_concurrent_accounting(self):
        gateway = LLMGateway(lambda aid, prompt, stage: '{}')
        with ThreadPoolExecutor(max_workers=3) as workers:
            list(workers.map(lambda i: gateway.call("A1", str(i), "P3"), range(100)))
        self.assertEqual(gateway.usage(), {"calls": 100, "by_stage": {"P3": 100}, "by_agent": {"A1": 100}})

    def test_gateway_timeout_does_not_charge(self):
        def timeout(*args):
            raise TimeoutError()
        gateway = LLMGateway(timeout)
        with self.assertRaises(TimeoutError):
            gateway.call("A1", "", "P3")
        self.assertEqual(gateway.calls, 0)


class IntegrationTest(unittest.TestCase):
    def test_B_gateway_and_D_full_cycle(self):
        ctx = native()
        snapshot = copy.deepcopy(ctx)
        gateway = LLMGateway(scripted_provider)
        result = run_bridge(ctx, gateway=gateway)
        self.assertEqual(result.status, "ok", result.errors)
        self.assertEqual(validate_stage_result(result, ["discussion_minutes", "conflict_register"], ctx.run_id, "P3"), [])
        self.assertEqual(result.budget_used, 12)
        self.assertEqual(gateway.calls, 12)
        minutes = result.artifacts["discussion_minutes"]["data"]
        self.assertEqual([len(minutes[k]) for k in ("statements", "qa", "votes")], [4, 4, 4])
        self.assertEqual(result.artifacts["conflict_register"]["meta"]["produced_by"], "conflict_review")
        self.assertEqual(ctx, snapshot)

    def test_P2_real_contract(self):
        ctx = native("P2")
        sample = make_context("P2")
        spec = copy.deepcopy(sample["artifacts"]["design_spec"]["data"])
        spec["role_materials"] = sample["artifacts"]["material_catalog"]["data"]["items"]
        ctx.artifacts = {"idea_card": make_artifact({"design_spec": spec}, ctx.run_id, "P1", "scheduler")}
        result = run(ctx, gateway=LLMGateway(scripted_provider))
        self.assertEqual(result.status, "ok", result.errors)
        self.assertEqual(result.budget_used, 1)
        self.assertEqual(validate_stage_result(result, ["problem_list", "convocation_table"], ctx.run_id, "P2"), [])
        self.assertEqual(len(result.artifacts["problem_list"]["data"]["problems"]), 4)
        # A 的实际 P3 快照只有 idea_card/problem_list/convocation_table，私有材料随召集表传递。
        p3 = native()
        p3.artifacts = {"idea_card": ctx.artifacts["idea_card"], **result.artifacts}
        full = run_bridge(p3, gateway=LLMGateway(scripted_provider))
        self.assertEqual(full.status, "ok", full.errors)

    def test_missing_material_no_model_call(self):
        ctx = native()
        del ctx.artifacts["material_catalog"]
        gateway = LLMGateway(scripted_provider)
        result = run_bridge(ctx, gateway=gateway)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(gateway.calls, 0)
        self.assertEqual(result.budget_used, 0)

    def test_remaining_accounts_stage_and_global(self):
        ctx = native()
        ctx.ledger.global_used = 28
        self.assertEqual(remaining(ctx), 2)
        ctx.ledger.global_used = 0
        ctx.ledger.per_stage_used["P3"] = 11
        self.assertEqual(remaining(ctx), 1)

    def test_real_registry_routes_to_A_composition(self):
        self.assertEqual(resolve("agents", "real").__module__, "src.scheduler.agents_bridge")
        self.assertEqual(resolve("agents", "mock").__module__, "src.scheduler.mocks")

    def test_runtime_charges_blocked_result(self):
        from src.scheduler.statemachine import Runtime
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmp:
            cfg = RunConfig(run_id="BLOCKED-CALLS", idea_text="这是一个用于验证真实模型挂起计费的设计规格测试。", run_root=tmp,
                            flow_ref=str(root / "scheduler" / "flow.yaml"), gate_ref=str(root / "scheduler" / "gate_rule.yaml"),
                            agent_ref=str(root / "agents" / "agent_config.yaml"))
            runtime = Runtime.boot(cfg)
            state = runtime.init_state()
            runtime.step(state)
            self.assertEqual(state.phase, "P2")
            with patch("src.scheduler.statemachine.resolve_module", return_value=lambda ctx: StageResult(stage="P2", status="blocked", budget_used=2)):
                runtime.step(state)
            self.assertEqual(state.ledger.global_used, 2)
            self.assertEqual(state.phase, "HUMAN_GATE")


if __name__ == "__main__":
    unittest.main()
