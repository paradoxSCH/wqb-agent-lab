from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


class WQBEngineCLITests(unittest.TestCase):
    @staticmethod
    def policy_config() -> dict:
        return {
            "research_policy": {
                "version": 1,
                "budget": {
                    "daily_simulation_limit": 20,
                    "exploration_share_limit": 0.4,
                    "exploration_stages": ["direction_probe"],
                    "stage_allocations": {"direction_probe": 8, "scale_winners": 8, "holdout": 4},
                },
                "behavioral_boundaries": {
                    "block_unclassified_candidates": True,
                    "require_kill_conditions": True,
                    "forbid_pure_price_volume": True,
                    "mechanisms": [
                        {
                            "mechanism_id": "reference_point_disposition_drift",
                            "enabled": True,
                            "allowed_proxy_fields": ["anl*"],
                            "kill_conditions": ["SELF_CORRELATION"],
                        }
                    ],
                },
            }
        }

    def invoke(self, argv: list[str], stdin_text: str = "") -> tuple[int, dict, str]:
        from src.wqb_engine.cli import run

        stdout = io.StringIO()
        stderr = io.StringIO()
        exit_code = run(argv, stdin=io.StringIO(stdin_text), stdout=stdout, stderr=stderr)
        rendered = stdout.getvalue()
        self.assertTrue(rendered.strip().startswith("{"), rendered)
        self.assertEqual(rendered.strip(), rendered.strip().splitlines()[0])
        return exit_code, json.loads(rendered), stderr.getvalue()

    def test_lists_schemas_as_json(self) -> None:
        exit_code, payload, stderr = self.invoke(["schemas.list"])

        self.assertEqual(0, exit_code)
        self.assertEqual("", stderr)
        self.assertTrue(payload["ok"])
        self.assertEqual("schemas.list", payload["operation"])
        self.assertIn("submission_job", payload["data"]["schemas"])

    def test_returns_schema_digest(self) -> None:
        exit_code, payload, _stderr = self.invoke(["schemas.digest", "--schema", "submission_job"])

        self.assertEqual(0, exit_code)
        self.assertTrue(payload["ok"])
        self.assertEqual("submission_job", payload["data"]["schema"])
        self.assertRegex(payload["data"]["digest"], r"^[0-9a-f]{64}$")

    def test_validates_payload_from_stdin(self) -> None:
        stdin_text = json.dumps(
            {
                "job_id": "job-001",
                "alpha_id": "abc123",
                "state": "queued",
                "auto_submit": False,
            }
        )

        exit_code, payload, _stderr = self.invoke(
            ["contracts.validate", "--schema", "submission_job"],
            stdin_text=stdin_text,
        )

        self.assertEqual(0, exit_code)
        self.assertEqual({"valid": True, "errors": []}, payload["data"])

    def test_invalid_payload_returns_machine_readable_errors(self) -> None:
        exit_code, payload, _stderr = self.invoke(
            ["contracts.validate", "--schema", "submission_job"],
            stdin_text='{"job_id": "job-001", "auto_submit": "yes"}',
        )

        self.assertEqual(2, exit_code)
        self.assertFalse(payload["ok"])
        self.assertEqual("validation_failed", payload["error"]["code"])
        self.assertIn("$.alpha_id: missing required property", payload["error"]["details"])
        self.assertIn("$.auto_submit: expected boolean, got string", payload["error"]["details"])

    def test_malformed_json_returns_input_error(self) -> None:
        exit_code, payload, _stderr = self.invoke(
            ["contracts.validate", "--schema", "submission_job"],
            stdin_text="{not-json",
        )

        self.assertEqual(2, exit_code)
        self.assertFalse(payload["ok"])
        self.assertEqual("invalid_json", payload["error"]["code"])

    def test_unknown_operation_returns_usage_error_json(self) -> None:
        exit_code, payload, _stderr = self.invoke(["unknown.op"])

        self.assertEqual(2, exit_code)
        self.assertFalse(payload["ok"])
        self.assertEqual("unknown_operation", payload["error"]["code"])

    def test_top_level_help_is_machine_readable_and_successful(self) -> None:
        exit_code, payload, stderr = self.invoke(["--help"])

        self.assertEqual(0, exit_code)
        self.assertEqual("", stderr)
        self.assertTrue(payload["ok"])
        self.assertEqual("help", payload["operation"])
        self.assertIn("policy.validate", payload["data"]["operations"])

    def test_operation_help_is_machine_readable_and_successful(self) -> None:
        exit_code, payload, _stderr = self.invoke(["policy.validate", "--help"])

        self.assertEqual(0, exit_code)
        self.assertEqual("policy.validate", payload["operation"])
        self.assertEqual(["--config"], payload["data"]["required_options"])

    def test_policy_validate_and_show_read_workflow_config_without_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "workflow.json"
            config_path.write_text(json.dumps(self.policy_config()), encoding="utf-8")

            validate_code, validate_payload, _ = self.invoke(["policy.validate", "--config", str(config_path)])
            show_code, show_payload, _ = self.invoke(["policy.show", "--config", str(config_path)])

            self.assertEqual(0, validate_code)
            self.assertTrue(validate_payload["data"]["valid"])
            self.assertRegex(validate_payload["data"]["digest"], r"^[0-9a-f]{64}$")
            self.assertEqual(0, show_code)
            self.assertEqual(20, show_payload["data"]["policy"]["budget"]["daily_simulation_limit"])
            self.assertEqual(validate_payload["data"]["digest"], show_payload["data"]["digest"])

    def test_policy_validate_accepts_windows_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "workflow.json"
            config_path.write_text(json.dumps(self.policy_config()), encoding="utf-8-sig")

            exit_code, payload, _stderr = self.invoke(["policy.validate", "--config", str(config_path)])

            self.assertEqual(0, exit_code)
            self.assertTrue(payload["data"]["valid"])

    def test_policy_validation_preserves_stable_domain_error_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "workflow.json"
            config_path.write_text("{}", encoding="utf-8")

            exit_code, payload, _stderr = self.invoke(["policy.validate", "--config", str(config_path)])

            self.assertEqual(2, exit_code)
            self.assertEqual("missing_research_policy", payload["error"]["code"])
            self.assertEqual("$.research_policy", payload["error"]["details"][0]["path"])

    def test_llm_validate_and_show_are_offline_and_accept_utf8_bom(self) -> None:
        secret = "test-offline-secret-value"
        config = {
            "llm_provider": {
                "provider": "openai_compatible",
                "model": "local-test-model",
                "api_key_env": "TEST_LLM_KEY",
                "base_url": "http://127.0.0.1:9/v1",
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "workflow.json"
            config_path.write_text(json.dumps(config), encoding="utf-8-sig")
            with patch("src.llm_provider.create_llm_provider") as create_provider:
                with patch.dict("os.environ", {}, clear=True):
                    missing_code, missing_payload, _ = self.invoke(
                        ["llm.validate", "--config", str(config_path)]
                    )
                with patch.dict(
                    "os.environ", {"TEST_LLM_KEY": secret}, clear=True
                ):
                    validate_code, validate_payload, _ = self.invoke(
                        ["llm.validate", "--config", str(config_path)]
                    )
                    show_code, show_payload, _ = self.invoke(
                        ["llm.show", "--config", str(config_path)]
                    )
                with patch.dict(
                    "os.environ", {"TEST_LLM_KEY": "changed-secret"}, clear=True
                ):
                    changed_code, changed_payload, _ = self.invoke(
                        ["llm.validate", "--config", str(config_path)]
                    )

            self.assertEqual(0, missing_code)
            self.assertEqual(0, changed_code)
            self.assertEqual(
                {
                    missing_payload["data"]["config_digest"],
                    validate_payload["data"]["config_digest"],
                    changed_payload["data"]["config_digest"],
                },
                {validate_payload["data"]["config_digest"]},
            )

            self.assertEqual(0, validate_code)
            self.assertEqual("openai_compatible", validate_payload["data"]["provider"])
            self.assertEqual("local-test-model", validate_payload["data"]["model"])
            self.assertRegex(validate_payload["data"]["config_digest"], r"^[0-9a-f]{64}$")
            self.assertEqual([], validate_payload["data"]["warnings"])
            self.assertEqual(0, show_code)
            self.assertEqual(
                validate_payload["data"]["config_digest"],
                show_payload["data"]["config_digest"],
            )
            effective = show_payload["data"]["effective_config"]
            self.assertTrue(effective["credential_configured"])
            self.assertEqual("TEST_LLM_KEY", effective["config"]["api_key_env"])
            self.assertNotIn(secret, json.dumps(show_payload))
            create_provider.assert_not_called()

    def test_llm_validate_rejects_removed_legacy_config(self) -> None:
        config = {
            "llm_adapter": {
                "provider": "deepseek",
                "model": "legacy-model",
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "workflow.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")

            exit_code, payload, _ = self.invoke(
                ["llm.validate", "--config", str(config_path)]
            )

            self.assertNotEqual(0, exit_code)
            self.assertEqual("invalid_configuration", payload["error"]["code"])

    def test_llm_success_payload_redacts_model_warning_and_probe_echoes(self) -> None:
        from src.llm_provider import LLMResponse, LLMUsage, ResolvedLLMProvider

        secret = "test-success-payload-secret"
        config = {
            "llm_provider": {
                "provider": "openai_compatible",
                "model": secret,
                "api_key_env": "SUCCESS_API_KEY",
            }
        }
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"SUCCESS_API_KEY": secret}, clear=True
        ):
            config_path = Path(tmp) / "workflow.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            from src.llm_provider import resolve_llm_provider_config

            base = resolve_llm_provider_config(config, require_credentials=False)
            resolved = ResolvedLLMProvider(
                config=base.config,
                api_key=secret,
                base_url=base.base_url,
                warnings=(f"warning echoes {secret}",),
            )
            provider = unittest.mock.Mock()
            provider.complete.return_value = LLMResponse(
                content=f"content echoes {secret}",
                provider=f"provider-{secret}",
                model=f"model-{secret}",
                usage=LLMUsage(total_tokens=1),
            )
            with patch(
                "src.llm_provider.resolve_llm_provider_config",
                return_value=resolved,
            ), patch(
                "src.llm_provider.create_llm_provider",
                return_value=provider,
            ):
                results = [
                    self.invoke([operation, "--config", str(config_path)])
                    for operation in ("llm.validate", "llm.show", "llm.probe")
                ]

        for exit_code, payload, _ in results:
            self.assertEqual(0, exit_code)
            self.assertNotIn(secret, json.dumps(payload))
        self.assertEqual("<redacted>", results[0][1]["data"]["model"])
        self.assertEqual(
            ["warning echoes <redacted>"], results[0][1]["data"]["warnings"]
        )
        self.assertEqual("model-<redacted>", results[2][1]["data"]["model"])

    def test_llm_probe_constructs_one_provider_and_sends_one_minimal_request(self) -> None:
        from src.llm_provider import create_llm_provider

        requests: list[dict] = []

        class ProbeHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                requests.append(json.loads(body))
                response = json.dumps(
                    {
                        "message": {"content": "OK"},
                        "done_reason": "stop",
                        "prompt_eval_count": 3,
                        "eval_count": 2,
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), ProbeHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        config = {
            "llm_provider": {
                "provider": "ollama",
                "model": "probe-model",
                "base_url": f"http://127.0.0.1:{server.server_port}",
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "workflow.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with patch(
                "src.llm_provider.create_llm_provider",
                wraps=create_llm_provider,
            ) as create_provider:
                try:
                    exit_code, payload, _ = self.invoke(
                        ["llm.probe", "--config", str(config_path)]
                    )
                finally:
                    server.shutdown()
                    server.server_close()
                    server_thread.join(timeout=2)

        self.assertEqual(0, exit_code)
        create_provider.assert_called_once()
        self.assertEqual(1, len(requests))
        request_text = (
            requests[0]["messages"][0]["content"]
            + " "
            + requests[0]["messages"][1]["content"]
        ).lower()
        for forbidden in ("alpha", "worldquant", "account", "field", "memory"):
            self.assertNotIn(forbidden, request_text)
        self.assertEqual("ollama", payload["data"]["provider"])
        self.assertEqual("probe-model", payload["data"]["model"])
        self.assertGreaterEqual(payload["data"]["latency_ms"], 0)
        self.assertEqual(5, payload["data"]["usage"]["total_tokens"])
        self.assertTrue(payload["data"]["content_validation"]["valid"])

    def test_llm_commands_load_workspace_dotenv_in_real_engine_subprocess(self) -> None:
        requests: list[dict[str, object]] = []
        authorization_headers: list[str] = []

        class ProbeHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                requests.append(json.loads(body))
                authorization_headers.append(self.headers.get("Authorization", ""))
                response = json.dumps(
                    {
                        "id": "test-dotenv-probe",
                        "model": "dotenv-model",
                        "choices": [
                            {
                                "message": {"content": "OK"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 2,
                            "completion_tokens": 1,
                            "total_tokens": 3,
                        },
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), ProbeHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        executable_name = "wqb-engine.exe" if os.name == "nt" else "wqb-engine"
        executable = shutil.which(executable_name)
        self.assertIsNotNone(executable, f"{executable_name} is not installed on PATH")

        try:
            with tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                config_path = workspace / "workflow.json"
                dotenv_secret = "test-dotenv-subprocess-secret"
                config_path.write_text(
                    json.dumps(
                        {
                            "llm_provider": {
                                "provider": "openai_compatible",
                                "model": "dotenv-model",
                                "api_key_env": "TEST_DOTENV_LLM_KEY",
                                "base_url": "http://127.0.0.1:1/v1",
                                "base_url_env": "TEST_DOTENV_LLM_URL",
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                (workspace / ".env").write_text(
                    f"TEST_DOTENV_LLM_KEY={dotenv_secret}\n"
                    "TEST_DOTENV_LLM_URL=http://127.0.0.1:2/v1\n",
                    encoding="utf-8",
                )
                environment = os.environ.copy()
                environment.pop("TEST_DOTENV_LLM_KEY", None)
                environment["TEST_DOTENV_LLM_URL"] = (
                    f"http://127.0.0.1:{server.server_port}/v1"
                )

                results: dict[str, subprocess.CompletedProcess[str]] = {}
                for operation in ("llm.validate", "llm.show"):
                    results[operation] = subprocess.run(
                        [str(executable), operation, "--config", str(config_path)],
                        cwd=workspace,
                        env=environment,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        check=False,
                        timeout=15,
                    )

                self.assertEqual(0, len(requests), "validate/show must remain offline")
                results["llm.probe"] = subprocess.run(
                    [str(executable), "llm.probe", "--config", str(config_path)],
                    cwd=workspace,
                    env=environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=False,
                    timeout=15,
                )
                probe_payload = json.loads(results["llm.probe"].stdout)
                self.assertEqual(0, results["llm.validate"].returncode)
                self.assertEqual(0, results["llm.show"].returncode)
                self.assertEqual(0, results["llm.probe"].returncode, results["llm.probe"].stdout)
                self.assertTrue(probe_payload["ok"])
                self.assertNotIn(dotenv_secret, results["llm.show"].stdout)
                self.assertNotIn(dotenv_secret, results["llm.probe"].stdout)
                self.assertEqual(1, len(requests))
                self.assertEqual(f"Bearer {dotenv_secret}", authorization_headers[0])
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)

    def test_llm_probe_disabled_and_provider_errors_are_stable(self) -> None:
        from src.llm_provider import LLMProviderError

        with tempfile.TemporaryDirectory() as tmp:
            disabled_path = Path(tmp) / "disabled.json"
            disabled_path.write_text(
                json.dumps({"llm_provider": {"provider": "disabled"}}),
                encoding="utf-8",
            )
            disabled_code, disabled_payload, _ = self.invoke(
                ["llm.probe", "--config", str(disabled_path)]
            )
            self.assertEqual(2, disabled_code)
            self.assertEqual("usage_error", disabled_payload["error"]["code"])

            live_path = Path(tmp) / "live.json"
            live_path.write_text(
                json.dumps(
                    {"llm_provider": {"provider": "ollama", "model": "probe-model"}}
                ),
                encoding="utf-8",
            )
            secret = "test-probe-secret-value"
            provider_error = LLMProviderError(
                code="rate_limited",
                message=f"retry after token {secret}",
                retryable=True,
                secrets=(secret,),
            )
            with patch("src.llm_provider.create_llm_provider") as create_provider:
                create_provider.return_value.complete.side_effect = provider_error
                error_code, error_payload, _ = self.invoke(
                    ["llm.probe", "--config", str(live_path)]
                )
            rendered = json.dumps(error_payload)
            self.assertEqual(2, error_code)
            self.assertEqual("rate_limited", error_payload["error"]["code"])
            self.assertNotIn(secret, rendered)

    def test_llm_help_metadata_is_machine_readable(self) -> None:
        for operation in ("llm.validate", "llm.show", "llm.probe"):
            with self.subTest(operation=operation):
                exit_code, payload, _ = self.invoke([operation, "--help"])
                self.assertEqual(0, exit_code)
                self.assertEqual(["--config"], payload["data"]["required_options"])

    def test_llm_operations_do_not_import_wqb_or_loop_runtime_modules(self) -> None:
        forbidden_roots = (
            "src.loop_validation",
            "src.session",
            "src.workflow_daemon",
            "src.wqb",
        )
        script = """
import io
import json
import sys
from src.wqb_engine.cli import run

operation, config_path = sys.argv[1:]
stdout = io.StringIO()
run([operation, "--config", config_path], stdout=stdout, stderr=io.StringIO())
forbidden = (
    "src.loop_validation",
    "src.session",
    "src.workflow_daemon",
    "src.wqb",
)
loaded = sorted(
    name for name in sys.modules
    if any(name == root or name.startswith(root + ".") for root in forbidden)
)
print(json.dumps({"loaded": loaded}))
"""
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "disabled.json"
            config_path.write_text(
                json.dumps({"llm_provider": {"provider": "disabled"}}),
                encoding="utf-8-sig",
            )
            for operation in ("llm.validate", "llm.show", "llm.probe"):
                with self.subTest(operation=operation):
                    completed = subprocess.run(
                        [sys.executable, "-c", script, operation, str(config_path)],
                        cwd=Path(__file__).resolve().parents[1],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    payload = json.loads(completed.stdout)
                    self.assertEqual([], payload["loaded"], forbidden_roots)

    def test_non_llm_operations_do_not_import_llm_adapters_or_requests(self) -> None:
        script = """
import builtins
import io
import json
import sys

real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == "requests" or name.startswith("src.llm_provider"):
        raise ImportError("blocked LLM dependency: " + name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import

from src.wqb_engine.cli import run
operation, config_path = sys.argv[1:]
argv = [operation]
stdin = io.StringIO("")
if operation == "policy.validate":
    argv.extend(["--config", config_path])
elif operation == "submission.evaluate":
    stdin = io.StringIO(json.dumps({
        "decision_id": "isolated-decision",
        "alpha_id": "isolated-alpha",
        "requested_mode": "queue_only",
        "agent_id": "isolated-agent",
        "rationale": "isolated check",
    }))
stdout = io.StringIO()
code = run(argv, stdin=stdin, stdout=stdout, stderr=io.StringIO())
loaded = sorted(
    name for name in sys.modules
    if name == "requests" or name.startswith("requests.")
    or name == "src.llm_provider" or name.startswith("src.llm_provider.")
)
print(json.dumps({"code": code, "loaded": loaded, "result": json.loads(stdout.getvalue())}))
"""
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "policy.json"
            config_path.write_text(json.dumps(self.policy_config()), encoding="utf-8")
            for operation in (
                "--help",
                "schemas.list",
                "policy.validate",
                "submission.evaluate",
            ):
                with self.subTest(operation=operation):
                    completed = subprocess.run(
                        [sys.executable, "-c", script, operation, str(config_path)],
                        cwd=Path(__file__).resolve().parents[1],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    payload = json.loads(completed.stdout)
                    self.assertEqual(0, payload["code"])
                    self.assertEqual([], payload["loaded"])
                    self.assertTrue(payload["result"]["ok"])

    def test_submission_evaluate_returns_policy_evaluation(self) -> None:
        decision = {
            "decision_id": "dec-cli-001",
            "alpha_id": "alpha-cli-001",
            "requested_mode": "queue_only",
            "agent_id": "agent-main",
            "rationale": "Agent requests queue execution.",
        }

        exit_code, payload, _stderr = self.invoke(
            ["submission.evaluate"],
            stdin_text=json.dumps(decision),
        )

        self.assertEqual(0, exit_code)
        self.assertTrue(payload["data"]["evaluation"]["allowed"])
        self.assertEqual("allow", payload["data"]["evaluation"]["policy_action"])

    def test_submission_execute_live_requires_run_dir_and_records_capability_disabled(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            decision = {
                "decision_id": "dec-cli-002",
                "alpha_id": "alpha-cli-002",
                "requested_mode": "execute_live",
                "agent_id": "agent-main",
                "rationale": "Agent requests live execution.",
            }

            exit_code, payload, _stderr = self.invoke(
                ["submission.execute_live", "--run-dir", tmp],
                stdin_text=json.dumps({"decision": decision}),
            )

            self.assertEqual(2, exit_code)
            self.assertFalse(payload["ok"])
            self.assertEqual("capability_disabled", payload["error"]["code"])
            audit_path = Path(tmp) / "submission_governance_audit.jsonl"
            self.assertTrue(audit_path.is_file())

    def test_submission_submit_intent_queues_backlog(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            decision = {
                "decision_id": "dec-cli-003",
                "alpha_id": "alpha-cli-003",
                "requested_mode": "queue_only",
                "agent_id": "agent-main",
                "rationale": "Agent requests queue execution.",
            }

            exit_code, payload, _stderr = self.invoke(
                ["submission.submit_intent", "--run-dir", tmp],
                stdin_text=json.dumps({"decision": decision}),
            )

            self.assertEqual(0, exit_code)
            self.assertEqual("queued", payload["data"]["result"]["status"])
            backlog = json.loads((Path(tmp) / "submission_backlog.json").read_text(encoding="utf-8"))
            self.assertEqual("alpha-cli-003", backlog[0]["alpha_id"])

    def test_submission_audit_tail_returns_events(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            decision = {
                "decision_id": "dec-cli-004",
                "alpha_id": "alpha-cli-004",
                "requested_mode": "queue_only",
                "agent_id": "agent-main",
                "rationale": "Agent requests queue execution.",
            }
            self.invoke(
                ["submission.submit_intent", "--run-dir", tmp],
                stdin_text=json.dumps({"decision": decision}),
            )

            exit_code, payload, _stderr = self.invoke(["submission.audit_tail", "--run-dir", tmp, "--limit", "1"])

            self.assertEqual(0, exit_code)
            self.assertEqual("queued", payload["data"]["events"][0]["event_type"])

    def test_loop_dry_run_validate_writes_validation_report(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            exit_code, payload, _stderr = self.invoke(
                [
                    "loop.dry_run_validate",
                    "--workspace-root",
                    tmp,
                    "--run-tag",
                    "cli-dry-run-validation",
                ]
            )

            self.assertEqual(0, exit_code)
            self.assertTrue(payload["ok"])
            self.assertEqual("complete", payload["data"]["status"])
            report_path = (
                Path(tmp)
                / ".local"
                / "data"
                / "runs"
                / "continuous-alpha"
                / "cli-dry-run-validation"
                / "dry_run_loop_validation_report.json"
            )
            self.assertTrue(report_path.exists())

    def test_demo_runs_closed_loop_without_live_calls(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            exit_code, payload, _stderr = self.invoke(
                [
                    "demo",
                    "--workspace-root",
                    tmp,
                    "--run-tag",
                    "cli-product-demo",
                ]
            )

            self.assertEqual(0, exit_code)
            self.assertTrue(payload["ok"])
            self.assertEqual("demo", payload["operation"])
            self.assertEqual("complete", payload["data"]["status"])
            self.assertEqual(0, payload["data"]["checks"]["live_wqb_calls"])
            self.assertEqual(0, payload["data"]["checks"]["submission_attempts"])


if __name__ == "__main__":
    unittest.main()
