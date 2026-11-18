"""Unit tests for the Stage 1 scaffold.

Only the standard library plus PyYAML (a declared dependency) is required, so
the suite runs on a workstation without Docker, a model or network access.
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from swarm import tools
from swarm.config import ConfigError, Settings
from swarm.delegation import DelegationError, TaskRouter, parse_plan
from swarm.llm import LLMClient, LLMError, ScriptedLLM
from swarm.orchestrator import Orchestrator
from swarm.registry import RegistryError, Task, load_personas, load_workflows, render_prompt
from swarm.worker import SwarmHTTPServer, is_authorized

CONFIGS = ROOT / "configs"
OBJECTIVE = "Compare local inference servers for a small agent swarm"


def load_registry() -> tuple[dict, dict]:
    personas = load_personas(CONFIGS / "agents.yaml")
    workflows = load_workflows(CONFIGS / "workflows.yaml", personas)
    return personas, workflows


def make_task(**overrides):
    personas, workflows = load_registry()
    payload = {"objective": OBJECTIVE, "workflow": "research_brief"}
    payload.update(overrides)
    return Task.from_dict(payload, workflows), personas, workflows


class SettingsTests(unittest.TestCase):
    def test_defaults_from_empty_environment(self) -> None:
        settings = Settings.from_env({})
        self.assertEqual(settings.llm_base_url, "http://127.0.0.1:11434")
        self.assertEqual(settings.llm_model, "qwen2.5:3b-instruct")
        self.assertEqual(settings.max_steps, 12)
        self.assertEqual(settings.max_agents, 4)
        self.assertEqual(settings.server_port, 8080)

    def test_rejects_non_http_base_url(self) -> None:
        with self.assertRaises(ConfigError):
            Settings.from_env({"SWARM_LLM_BASE_URL": "llm-runtime:11434"})

    def test_rejects_out_of_range_values(self) -> None:
        with self.assertRaises(ConfigError):
            Settings.from_env({"SWARM_MAX_STEPS": "0"})
        with self.assertRaises(ConfigError):
            Settings.from_env({"SWARM_TEMPERATURE": "3.5"})

    def test_token_is_required_to_serve(self) -> None:
        with self.assertRaises(ConfigError):
            Settings.from_env({}).require_token()
        self.assertEqual(Settings.from_env({"SWARM_ORCHESTRATOR_TOKEN": " local-token "}).require_token(), "local-token")


class RegistryTests(unittest.TestCase):
    def test_personas_and_workflows_load(self) -> None:
        personas, workflows = load_registry()
        self.assertEqual(sorted(personas), ["coder", "critic", "planner", "researcher"])
        self.assertEqual(sorted(workflows), ["code_review", "reasoning_chain", "research_brief"])
        self.assertEqual(workflows["research_brief"].entry, "planner")
        self.assertEqual(personas["researcher"].tools, ("text_stats",))

    def test_prompt_render_reports_unbound_placeholder(self) -> None:
        with self.assertRaises(RegistryError):
            render_prompt("hello {missing_placeholder}", objective="x")

    def test_task_requires_known_workflow(self) -> None:
        _, workflows = load_registry()
        with self.assertRaises(RegistryError):
            Task.from_dict({"objective": OBJECTIVE, "workflow": "does_not_exist"}, workflows)

    def test_task_requires_a_substantive_objective(self) -> None:
        _, workflows = load_registry()
        with self.assertRaises(RegistryError):
            Task.from_dict({"objective": "   ", "workflow": "research_brief"}, workflows)

    def test_task_rejects_unknown_tool(self) -> None:
        _, workflows = load_registry()
        with self.assertRaises(RegistryError):
            Task.from_dict(
                {"objective": OBJECTIVE, "workflow": "research_brief", "constraints": {"allowed_tools": ["shell"]}},
                workflows,
            )

    def test_example_tasks_match_the_registry(self) -> None:
        _, workflows = load_registry()
        examples = sorted(path for path in (CONFIGS / "tasks").glob("*.json") if path.name != "schema.json")
        self.assertEqual(len(examples), 3)
        for path in examples:
            task = Task.from_dict(json.loads(path.read_text(encoding="utf-8")), workflows)
            self.assertIn(task.workflow, workflows)


class ToolTests(unittest.TestCase):
    def test_calculator_evaluates_arithmetic(self) -> None:
        self.assertEqual(tools.run_tool("calculator", {"expression": "(12 * 4) / 3"})["value"], 16)
        self.assertEqual(tools.run_tool("calculator", {"expression": "7 % 4"})["value"], 3)
        self.assertEqual(tools.run_tool("calculator", {"expression": "-3 + 2"})["value"], -1)

    def test_calculator_rejects_names_calls_and_huge_exponents(self) -> None:
        for expression in ("__import__('os').system('id')", "abs(-1)", "2 ** 64", "1 / 0"):
            with self.subTest(expression=expression):
                with self.assertRaises(tools.ToolError):
                    tools.run_tool("calculator", {"expression": expression})

    def test_text_stats_counts_words_and_sentences(self) -> None:
        stats = tools.run_tool("text_stats", {"text": "Local models are private. They are also cheap."})
        self.assertEqual(stats["words"], 8)
        self.assertEqual(stats["sentences"], 2)

    def test_run_tool_rejects_undeclared_arguments(self) -> None:
        with self.assertRaises(tools.ToolError):
            tools.run_tool("calculator", {"expression": "1 + 1", "extra": True})

    def test_tool_request_is_extracted_from_a_fenced_block(self) -> None:
        request = tools.extract_tool_request('done\n```json\n{"tool": "text_stats", "arguments": {"text": "a b"}}\n```')
        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual(request[0], "text_stats")
        self.assertEqual(request[1], {"text": "a b"})

    def test_unparseable_tool_request_is_ignored(self) -> None:
        self.assertIsNone(tools.extract_tool_request("no tool here"))


class DelegationTests(unittest.TestCase):
    def test_parse_plan_reads_a_json_array_and_deduplicates(self) -> None:
        self.assertEqual(parse_plan('```json\n["a", "b", "a", "c"]\n```', 2), ["a", "b"])

    def test_parse_plan_falls_back_to_list_lines(self) -> None:
        self.assertEqual(parse_plan("1. one\n- two\n", 5), ["one", "two"])
        self.assertEqual(parse_plan("", 5), [])

    def test_router_routes_by_capability(self) -> None:
        personas, _ = load_registry()
        self.assertEqual(TaskRouter(personas).by_capability("planning").id, "planner")
        self.assertEqual(TaskRouter(personas).by_capability("research").id, "researcher")

    def test_router_rejects_unknown_capability(self) -> None:
        personas, _ = load_registry()
        with self.assertRaises(DelegationError):
            TaskRouter(personas).by_capability("gpu-tuning")


class OrchestratorTests(unittest.TestCase):
    def test_dry_run_makes_no_model_call(self) -> None:
        task, personas, workflows = make_task()
        result = Orchestrator(Settings.from_env({}), personas, workflows, llm=None).run(task, dry_run=True)
        self.assertEqual(result.status, "planned")
        self.assertEqual(result.steps, 0)
        self.assertEqual(len(result.plan), 2)
        self.assertEqual(result.turns[0].persona_id, "planner")

    def test_live_run_delegates_per_subtask_and_synthesises(self) -> None:
        task, personas, workflows = make_task()
        llm = ScriptedLLM(['["memory footprint", "cold start"]', "finding one", "finding two", "final brief"])
        result = Orchestrator(Settings.from_env({}), personas, workflows, llm).run(task)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.steps, 4)
        self.assertEqual([turn.stage for turn in result.turns], ["plan", "gather", "gather", "synthesise"])
        self.assertEqual(result.plan, ["memory footprint", "cold start"])
        self.assertEqual(llm.calls, 4)

    def test_run_records_a_tool_observation(self) -> None:
        task, personas, workflows = make_task()
        reply = '```json\n{"tool": "text_stats", "arguments": {"text": "alpha beta gamma"}}\n```'
        llm = ScriptedLLM(['["measure the text"]', reply, "final"])
        result = Orchestrator(Settings.from_env({}), personas, workflows, llm).run(task)
        observations = result.turns[1].tool_observations
        self.assertEqual(len(observations), 1)
        self.assertTrue(observations[0]["ok"])
        self.assertEqual(observations[0]["result"]["words"], 3)

    def test_tool_not_granted_to_the_persona_is_refused(self) -> None:
        task, personas, workflows = make_task()
        reply = '```json\n{"tool": "calculator", "arguments": {"expression": "1 + 1"}}\n```'
        llm = ScriptedLLM(['["measure the text"]', reply, "final"])
        result = Orchestrator(Settings.from_env({}), personas, workflows, llm).run(task)
        observation = result.turns[1].tool_observations[0]
        self.assertFalse(observation["ok"])
        self.assertIn("not enabled", observation["error"])

    def test_failed_model_call_marks_the_run_failed(self) -> None:
        task, personas, workflows = make_task()

        class BrokenLLM:
            def chat(self, *args, **kwargs):
                raise LLMError("inference server is unreachable")

        result = Orchestrator(Settings.from_env({}), personas, workflows, BrokenLLM()).run(task)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.steps, 0)
        self.assertTrue(result.errors)

    def test_task_step_budget_is_enforced(self) -> None:
        task, personas, workflows = make_task(constraints={"max_steps": 2})
        llm = ScriptedLLM(['["a", "b", "c"]'] + ["reply"] * 10)
        result = Orchestrator(Settings.from_env({}), personas, workflows, llm).run(task)
        self.assertEqual(result.status, "budget_exhausted")
        self.assertEqual(result.steps, 2)


class LLMClientTests(unittest.TestCase):
    def test_base_url_normalisation(self) -> None:
        plain = LLMClient("http://llm-runtime:11434", "qwen2.5:3b-instruct")
        self.assertEqual(plain.chat_endpoint, "http://llm-runtime:11434/v1/chat/completions")
        explicit = LLMClient("http://127.0.0.1:8000/v1/", "model")
        self.assertEqual(explicit.chat_endpoint, "http://127.0.0.1:8000/v1/chat/completions")


class WorkerAuthTests(unittest.TestCase):
    def test_bearer_token_is_required(self) -> None:
        self.assertTrue(is_authorized("Bearer local-token", "local-token"))
        self.assertFalse(is_authorized(None, "local-token"))
        self.assertFalse(is_authorized("Bearer wrong-token", "local-token"))
        self.assertFalse(is_authorized("Basic local-token", "local-token"))
        self.assertFalse(is_authorized("Bearer anything", ""))


class StubService:
    token = "local-token"

    def health(self):
        return {"status": "ok", "service": "stub"}

    def readiness(self):
        return 200, {"status": "ready", "llm": "stub"}

    def run_task(self, payload):
        return {"status": "ok", "objective": payload.get("objective")}


class TaskApiIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = SwarmHTTPServer(("127.0.0.1", 0), StubService())
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_health_and_ready_are_served(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("GET", "/health")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["status"], "ok")
            connection.request("GET", "/ready")
            self.assertEqual(connection.getresponse().status, 200)
        finally:
            connection.close()

    def test_auth_failure_does_not_corrupt_the_next_keepalive_request(self) -> None:
        body = json.dumps({"objective": "Validate the scaffold", "workflow": "research_brief"})
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("POST", "/v1/tasks", body=body, headers={"Content-Type": "application/json"})
            first = connection.getresponse()
            first.read()
            self.assertEqual(first.status, 401)

            connection.request(
                "POST",
                "/v1/tasks",
                body=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer local-token"},
            )
            second = connection.getresponse()
            payload = json.loads(second.read())
            self.assertEqual(second.status, 200)
            self.assertEqual(payload["result"]["status"], "ok")
        finally:
            connection.close()


class ContainerContractTests(unittest.TestCase):
    def test_root_compose_exposes_discoverable_services(self) -> None:
        document = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
        self.assertEqual(sorted(document["services"]), ["llm-runtime", "swarm-orchestrator"])
        for service in document["services"].values():
            self.assertIn("image", service)
            self.assertIn("extends", service)

    def test_canonical_compose_hardens_every_service(self) -> None:
        document = yaml.safe_load((ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8"))
        for name, service in document["services"].items():
            with self.subTest(service=name):
                self.assertIn("no-new-privileges:true", service["security_opt"])
                self.assertEqual(service["cap_drop"], ["ALL"])
                self.assertEqual(service["logging"]["options"]["max-size"], "20m")
                self.assertIn("healthcheck", service)
                self.assertIn("mem_limit", service)

    def test_orchestrator_runs_read_only_with_persistent_state(self) -> None:
        document = yaml.safe_load((ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8"))
        worker = document["services"]["swarm-orchestrator"]
        self.assertTrue(worker["read_only"])
        self.assertIn("swarm-state:/app/state", worker["volumes"])
        self.assertEqual(document["volumes"]["model-weights"]["name"], "cloudguard-llm-swarm-weights")


if __name__ == "__main__":
    unittest.main()
