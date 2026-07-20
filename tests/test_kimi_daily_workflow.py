from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from src import kimi_daily_workflow as kimi_daily_workflow_module
from src.kimi_daily_workflow import KimiDailyWorkflow, LLMPlanAdapter, choose_budgeted_candidates
from src.llm_provider import LLMProviderError, LLMResponse, LLMUsage
from src.research_policy import load_research_policy, policy_digest


os.environ.setdefault("WQB_DISABLE_LLM_TEMPLATE_BACKEND", "1")


class RecordingProvider:
    provider_id = "ollama"
    model = "qwen-test"

    def __init__(self, content: str = '{"directions": []}') -> None:
        self.content = content
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return LLMResponse(
            content=self.content,
            provider=self.provider_id,
            model=self.model,
            usage=LLMUsage(input_tokens=7, output_tokens=3, total_tokens=10),
            finish_reason="stop",
            raw_response={"id": "local-test"},
        )


class LLMPlanningIdentityTests(unittest.TestCase):
    def test_planning_digest_ignores_credential_availability_and_value(self) -> None:
        config = {
            "llm_provider": {
                "provider": "openai_compatible",
                "model": "planning-model",
                "api_key_env": "PLANNING_API_KEY",
            }
        }
        digests: list[str] = []
        for env in ({}, {"PLANNING_API_KEY": "one"}, {"PLANNING_API_KEY": "two"}):
            with patch.dict(os.environ, env, clear=True):
                digests.append(LLMPlanAdapter.from_config(config).metadata()["config_digest"])

        self.assertEqual([digests[0], digests[0], digests[0]], digests)


class FailingProvider:
    provider_id = "ollama"
    model = "qwen-test"

    def complete(self, request):
        raise LLMProviderError(
            code="rate_limited",
            message="Provider rate limit exceeded.",
            provider=self.provider_id,
            model=self.model,
            retryable=True,
            status_code=429,
        )


class LeakyFailingProvider:
    provider_id = "openai_compatible"
    model = "deepseek-test"

    def complete(self, request):
        raise LLMProviderError(
            code="provider_error",
            message="upstream echoed do-not-serialize",
            provider=self.provider_id,
            model=self.model,
            details={"response": {"debug": "do-not-serialize"}},
        )


class FailThenSucceedProvider(RecordingProvider):
    def complete(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            raise LLMProviderError(
                code="timeout",
                message="Provider timed out.",
                provider=self.provider_id,
                model=self.model,
                retryable=True,
            )
        return LLMResponse(
            content=self.content,
            provider=self.provider_id,
            model=self.model,
            usage=LLMUsage(),
        )


class ConfigurableFailingProvider:
    def __init__(
        self,
        *,
        code: str,
        retryable: bool,
        provider_id: str = "openai_compatible",
        model: str = "gpt-test",
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.provider_id = provider_id
        self.model = model
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        raise LLMProviderError(
            code=self.code,
            message="provider fixture failure",
            provider=self.provider_id,
            model=self.model,
            retryable=self.retryable,
        )


class KimiDailyWorkflowTests(unittest.TestCase):
    def test_canonical_provider_drives_llm_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "configs" / "workflow.json"
            self._write_json(
                config_path,
                {
                    "capacity_estimate": {"recommended_mode": "standard"},
                    "daily_budget_modes": {"standard": {"daily_budget": 10, "stage_budgets": {}}},
                    "stage_order": [],
                    "llm_provider": {"provider": "ollama", "model": "qwen-test"},
                },
            )
            provider = RecordingProvider()

            with (
                patch.dict(os.environ, {"WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0"}),
                patch("requests.post", side_effect=AssertionError("direct HTTP transport used")),
                patch("subprocess.run", side_effect=AssertionError("direct CLI transport used")),
            ):
                workflow = KimiDailyWorkflow(
                    root,
                    workflow_config=config_path,
                    run_date=date(2026, 5, 5),
                    llm_provider=provider,
                )
                ledger = workflow.load_or_create_ledger()
                output_path = workflow.run_llm_plan(ledger)

            self.assertIsNotNone(output_path)
            assert output_path is not None
            self.assertEqual(1, len(provider.requests))
            request = provider.requests[0]
            self.assertEqual("json", request.response_format)
            self.assertEqual(workflow.llm_adapter.stage, request.metadata["workflow_stage"])
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual([], payload["directions"])
            self.assertEqual("ollama", payload["provider"])
            self.assertEqual("qwen-test", payload["model"])
            self.assertTrue(workflow.llm_adapter.prompt_path(root, workflow.run_dir, workflow.run_tag).exists())

    def test_llm_plan_writes_structured_provider_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "configs" / "workflow.json"
            self._write_json(
                config_path,
                {
                    "capacity_estimate": {"recommended_mode": "standard"},
                    "daily_budget_modes": {"standard": {"daily_budget": 10, "stage_budgets": {}}},
                    "stage_order": [],
                    "llm_provider": {"provider": "ollama", "model": "qwen-test"},
                },
            )

            with patch.dict(os.environ, {"WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0"}):
                workflow = KimiDailyWorkflow(
                    root,
                    workflow_config=config_path,
                    run_date=date(2026, 5, 5),
                    llm_provider=FailingProvider(),
                )
                output_path = workflow.run_llm_plan(workflow.load_or_create_ledger())

            assert output_path is not None
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(
                {
                    "code": "rate_limited",
                    "message": "Provider rate limit exceeded.",
                    "retryable": True,
                    "provider": "ollama",
                    "model": "qwen-test",
                    "status_code": 429,
                },
                payload["error"],
            )

    def test_legacy_provider_metadata_is_redacted_and_records_migration_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "configs" / "workflow.json"
            self._write_json(
                config_path,
                {
                    "capacity_estimate": {"recommended_mode": "standard"},
                    "daily_budget_modes": {"standard": {"daily_budget": 10, "stage_budgets": {}}},
                    "stage_order": [],
                    "llm_adapter": {
                        "provider": "deepseek",
                        "model": "deepseek-test",
                        "api_key_env": "DEEPSEEK_API_KEY",
                        "base_url": "https://api.deepseek.com",
                    },
                },
            )
            provider = RecordingProvider()
            provider.provider_id = "openai_compatible"
            provider.model = "deepseek-test"

            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "do-not-serialize"}):
                workflow = KimiDailyWorkflow(
                    root,
                    workflow_config=config_path,
                    run_date=date(2026, 5, 5),
                    llm_provider=provider,
                )
                ledger = workflow.load_or_create_ledger()

            metadata = ledger["llm_provider"]
            self.assertEqual("openai_compatible", metadata["provider"])
            self.assertEqual("deepseek-test", metadata["model"])
            self.assertRegex(metadata["config_digest"], r"^[0-9a-f]{64}$")
            self.assertTrue(any("llm_adapter" in warning for warning in metadata["migration_warnings"]))
            self.assertNotIn("do-not-serialize", json.dumps(metadata))
            self.assertNotIn("do-not-serialize", repr(workflow.llm_adapter))

    def test_workflow_redacts_configured_secret_from_provider_error_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "configs" / "workflow.json"
            self._write_json(
                config_path,
                {
                    "capacity_estimate": {"recommended_mode": "standard"},
                    "daily_budget_modes": {"standard": {"daily_budget": 10, "stage_budgets": {}}},
                    "stage_order": [],
                    "llm_provider": {
                        "provider": "openai_compatible",
                        "model": "deepseek-test",
                        "api_key_env": "DEEPSEEK_API_KEY",
                        "base_url": "https://api.deepseek.com",
                    },
                },
            )

            with patch.dict(
                os.environ,
                {
                    "DEEPSEEK_API_KEY": "do-not-serialize",
                    "WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0",
                },
            ):
                workflow = KimiDailyWorkflow(
                    root,
                    workflow_config=config_path,
                    run_date=date(2026, 5, 5),
                    llm_provider=LeakyFailingProvider(),
                )
                output_path = workflow.run_llm_plan(workflow.load_or_create_ledger())

            assert output_path is not None
            serialized = output_path.read_text(encoding="utf-8")
            self.assertNotIn("do-not-serialize", serialized)
            payload = json.loads(serialized)
            self.assertEqual("Provider request failed.", payload["error"]["message"])
            self.assertNotIn("details", payload["error"])

    def test_disabled_provider_does_not_create_plan_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "configs" / "workflow.json"
            self._write_json(
                config_path,
                {
                    "capacity_estimate": {"recommended_mode": "standard"},
                    "daily_budget_modes": {"standard": {"daily_budget": 10, "stage_budgets": {}}},
                    "stage_order": [],
                    "llm_provider": {"provider": "disabled"},
                },
            )

            workflow = KimiDailyWorkflow(
                root,
                workflow_config=config_path,
                run_date=date(2026, 5, 5),
            )

            self.assertIsNone(workflow.run_llm_plan(workflow.load_or_create_ledger()))
            self.assertFalse(any(workflow.run_dir.glob("*_outputs/*.json")))

    def test_stale_success_artifact_is_replaced_and_matching_success_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "configs" / "workflow.json"
            self._write_json(
                config_path,
                {
                    "capacity_estimate": {"recommended_mode": "standard"},
                    "daily_budget_modes": {"standard": {"daily_budget": 10, "stage_budgets": {}}},
                    "stage_order": [],
                    "llm_provider": {"provider": "ollama", "model": "qwen-test"},
                },
            )
            provider = RecordingProvider()
            workflow = KimiDailyWorkflow(
                root,
                workflow_config=config_path,
                run_date=date(2026, 5, 5),
                llm_provider=provider,
            )
            ledger = workflow.load_or_create_ledger()
            output_path = workflow.llm_adapter.output_path(root, workflow.run_dir, workflow.run_tag)
            self._write_json(
                output_path,
                {
                    "directions": ["stale"],
                    "llm_plan": {
                        "status": "success",
                        "config_digest": ledger["llm_provider"]["config_digest"],
                    },
                },
            )

            with patch.dict(os.environ, {"WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0"}):
                first_path = workflow.run_llm_plan(ledger)
                ledger["llm_provider"] = {"config_digest": "corrupted-ledger"}
                self._write_json(workflow.ledger_path, ledger)
                second_path = workflow.run_llm_plan(ledger)

            self.assertEqual(output_path, first_path)
            self.assertEqual(output_path, second_path)
            self.assertEqual(1, len(provider.requests))
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual("success", payload["llm_plan"]["status"])
            self.assertEqual(ledger["llm_provider"]["config_digest"], payload["llm_plan"]["config_digest"])
            self.assertEqual(
                {
                    "status",
                    "pause_reason",
                    "code",
                    "retryable",
                    "attempt_count",
                    "last_attempt_at",
                    "next_retry_at",
                    "config_digest",
                    "process_instance_id",
                },
                set(payload["llm_plan"]),
            )
            self.assertIsNone(payload["llm_plan"]["pause_reason"])
            persisted_ledger = json.loads(workflow.ledger_path.read_text(encoding="utf-8"))
            self.assertEqual(
                persisted_ledger["llm_provider"]["config_digest"],
                payload["llm_plan"]["config_digest"],
            )

    def test_error_artifact_is_retried_and_replaced_by_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "configs" / "workflow.json"
            self._write_json(
                config_path,
                {
                    "capacity_estimate": {"recommended_mode": "standard"},
                    "daily_budget_modes": {"standard": {"daily_budget": 10, "stage_budgets": {}}},
                    "stage_order": [],
                    "llm_provider": {"provider": "ollama", "model": "qwen-test"},
                },
            )
            provider = FailThenSucceedProvider()
            workflow = KimiDailyWorkflow(
                root,
                workflow_config=config_path,
                run_date=date(2026, 5, 5),
                llm_provider=provider,
            )
            ledger = workflow.load_or_create_ledger()

            first_attempt_at = datetime(2026, 5, 5, 9, 0, 0)
            with patch.dict(os.environ, {"WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0"}):
                output_path = workflow.run_llm_plan(ledger, now=first_attempt_at)
                assert output_path is not None
                first = json.loads(output_path.read_text(encoding="utf-8"))
                workflow.run_llm_plan(
                    ledger,
                    now=first_attempt_at + timedelta(seconds=30),
                )

            final = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual("error", first["llm_plan"]["status"])
            self.assertEqual("success", final["llm_plan"]["status"])
            self.assertEqual(2, len(provider.requests))
            self.assertEqual(ledger["llm_provider"]["config_digest"], final["llm_plan"]["config_digest"])

    def test_retryable_error_honors_persisted_exponential_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "configs" / "workflow.json"
            self._write_json(
                config_path,
                {
                    "capacity_estimate": {"recommended_mode": "standard"},
                    "daily_budget_modes": {"standard": {"daily_budget": 10, "stage_budgets": {}}},
                    "stage_order": [],
                    "llm_provider": {"provider": "ollama", "model": "qwen-test"},
                },
            )
            provider = ConfigurableFailingProvider(
                code="rate_limited",
                retryable=True,
                provider_id="ollama",
                model="qwen-test",
            )
            workflow = KimiDailyWorkflow(
                root,
                workflow_config=config_path,
                run_date=date(2026, 5, 5),
                llm_provider=provider,
                process_instance_id="process-a",
            )
            ledger = workflow.load_or_create_ledger()
            started = datetime(2026, 5, 5, 9, 0, 0)

            with patch.dict(os.environ, {"WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0"}):
                output_path = workflow.run_llm_plan(ledger, now=started)
                assert output_path is not None
                first = json.loads(output_path.read_text(encoding="utf-8"))["llm_plan"]
                workflow.run_llm_plan(ledger, now=started + timedelta(seconds=29))
                blocked = json.loads(output_path.read_text(encoding="utf-8"))["llm_plan"]
                workflow.run_llm_plan(ledger, now=started + timedelta(seconds=30))

            second = json.loads(output_path.read_text(encoding="utf-8"))["llm_plan"]
            self.assertEqual(1, first["attempt_count"])
            self.assertEqual("2026-05-05T09:00:30", first["next_retry_at"])
            self.assertEqual("retry_backoff", first["pause_reason"])
            self.assertEqual(first, blocked)
            self.assertEqual(2, len(provider.requests))
            self.assertEqual(2, second["attempt_count"])
            self.assertEqual("2026-05-05T09:01:30", second["next_retry_at"])

    def test_terminal_error_is_not_repeated_in_same_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "configs" / "workflow.json"
            self._write_json(
                config_path,
                {
                    "capacity_estimate": {"recommended_mode": "standard"},
                    "daily_budget_modes": {"standard": {"daily_budget": 10, "stage_budgets": {}}},
                    "stage_order": [],
                    "llm_provider": {"provider": "ollama", "model": "qwen-test"},
                },
            )
            provider = ConfigurableFailingProvider(
                code="invalid_structured_output",
                retryable=False,
                provider_id="ollama",
                model="qwen-test",
            )
            workflow = KimiDailyWorkflow(
                root,
                workflow_config=config_path,
                run_date=date(2026, 5, 5),
                llm_provider=provider,
                process_instance_id="process-a",
            )
            ledger = workflow.load_or_create_ledger()
            started = datetime(2026, 5, 5, 9, 0, 0)

            with patch.dict(os.environ, {"WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0"}):
                output_path = workflow.run_llm_plan(ledger, now=started)
                workflow.run_llm_plan(ledger, now=started + timedelta(days=1))

            assert output_path is not None
            plan = json.loads(output_path.read_text(encoding="utf-8"))["llm_plan"]
            self.assertEqual(1, len(provider.requests))
            self.assertEqual("terminal_error", plan["pause_reason"])
            self.assertIsNone(plan["next_retry_at"])
            self.assertEqual("process-a", plan["process_instance_id"])

    def test_env_credential_change_reconstructs_non_injected_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "configs" / "workflow.json"
            self._write_json(
                config_path,
                {
                    "capacity_estimate": {"recommended_mode": "standard"},
                    "daily_budget_modes": {"standard": {"daily_budget": 10, "stage_budgets": {}}},
                    "stage_order": [],
                    "llm_provider": {
                        "provider": "openai_compatible",
                        "model": "gpt-test",
                        "api_key_env": "OPENAI_API_KEY",
                    },
                },
            )
            failed_provider = ConfigurableFailingProvider(
                code="authentication_error",
                retryable=False,
            )
            repaired_provider = RecordingProvider()
            repaired_provider.provider_id = "openai_compatible"
            repaired_provider.model = "gpt-test"
            providers = iter((failed_provider, repaired_provider))
            started = datetime(2026, 5, 5, 9, 0, 0)

            with (
                patch.dict(
                    os.environ,
                    {
                        "OPENAI_API_KEY": "old-secret",
                        "WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0",
                    },
                ),
                patch(
                    "src.llm_planning.create_llm_provider",
                    side_effect=lambda *args, **kwargs: next(providers),
                ) as create_provider,
            ):
                workflow = KimiDailyWorkflow(
                    root,
                    workflow_config=config_path,
                    run_date=date(2026, 5, 5),
                    process_instance_id="process-a",
                )
                ledger = workflow.load_or_create_ledger()
                output_path = workflow.run_llm_plan(ledger, now=started)
                os.environ["OPENAI_API_KEY"] = "new-secret"
                workflow.run_llm_plan(ledger, now=started + timedelta(seconds=1))

            assert output_path is not None
            plan = json.loads(output_path.read_text(encoding="utf-8"))["llm_plan"]
            self.assertEqual(2, create_provider.call_count)
            self.assertEqual(1, len(failed_provider.requests))
            self.assertEqual(1, len(repaired_provider.requests))
            self.assertEqual("success", plan["status"])
            self.assertNotIn("old-secret", output_path.read_text(encoding="utf-8"))
            self.assertNotIn("new-secret", output_path.read_text(encoding="utf-8"))

    def test_removed_credential_is_recorded_once_and_terminal_artifact_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "configs" / "workflow.json"
            self._write_json(
                config_path,
                {
                    "capacity_estimate": {"recommended_mode": "standard"},
                    "daily_budget_modes": {"standard": {"daily_budget": 10, "stage_budgets": {}}},
                    "stage_order": [],
                    "llm_provider": {
                        "provider": "openai_compatible",
                        "model": "gpt-test",
                        "api_key_env": "OPENAI_API_KEY",
                    },
                },
            )
            provider = RecordingProvider()
            provider.provider_id = "openai_compatible"
            provider.model = "gpt-test"
            started = datetime(2026, 5, 5, 9, 0, 0)

            with (
                patch.dict(
                    os.environ,
                    {
                        "OPENAI_API_KEY": "working-secret",
                        "WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0",
                    },
                ),
                patch(
                    "src.llm_planning.create_llm_provider",
                    return_value=provider,
                ) as create_provider,
            ):
                workflow = KimiDailyWorkflow(
                    root,
                    workflow_config=config_path,
                    run_date=date(2026, 5, 5),
                    process_instance_id="process-a",
                )
                ledger = workflow.load_or_create_ledger()
                output_path = workflow.run_llm_plan(ledger, now=started)
                os.environ["OPENAI_API_KEY"] = ""
                workflow.run_llm_plan(
                    ledger,
                    now=started + timedelta(seconds=1),
                )
                assert output_path is not None
                first_terminal = json.loads(
                    output_path.read_text(encoding="utf-8")
                )["llm_plan"]
                workflow.run_llm_plan(
                    ledger,
                    now=started + timedelta(seconds=2),
                )

            repeated = json.loads(output_path.read_text(encoding="utf-8"))["llm_plan"]
            self.assertEqual("invalid_configuration", first_terminal["code"])
            self.assertEqual("terminal_error", first_terminal["pause_reason"])
            self.assertEqual(2, first_terminal["attempt_count"])
            self.assertEqual(first_terminal, repeated)
            self.assertEqual(1, len(provider.requests))
            self.assertEqual(1, create_provider.call_count)

    def test_injected_provider_is_never_reconstructed_after_env_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "configs" / "workflow.json"
            self._write_json(
                config_path,
                {
                    "capacity_estimate": {"recommended_mode": "standard"},
                    "daily_budget_modes": {"standard": {"daily_budget": 10, "stage_budgets": {}}},
                    "stage_order": [],
                    "llm_provider": {
                        "provider": "openai_compatible",
                        "model": "gpt-test",
                        "api_key_env": "OPENAI_API_KEY",
                    },
                },
            )
            injected = ConfigurableFailingProvider(
                code="authentication_error",
                retryable=False,
            )
            started = datetime(2026, 5, 5, 9, 0, 0)
            with patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "old-secret",
                    "WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0",
                },
            ):
                workflow = KimiDailyWorkflow(
                    root,
                    workflow_config=config_path,
                    run_date=date(2026, 5, 5),
                    llm_provider=injected,
                    process_instance_id="process-a",
                )
                ledger = workflow.load_or_create_ledger()
                workflow.run_llm_plan(ledger, now=started)
                os.environ["OPENAI_API_KEY"] = "new-secret"
                with patch("src.llm_planning.create_llm_provider") as create_provider:
                    workflow.run_llm_plan(
                        ledger,
                        now=started + timedelta(days=1),
                    )

            self.assertEqual(1, len(injected.requests))
            create_provider.assert_not_called()

    def test_process_restart_allows_one_fresh_authentication_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "configs" / "workflow.json"
            self._write_json(
                config_path,
                {
                    "capacity_estimate": {"recommended_mode": "standard"},
                    "daily_budget_modes": {"standard": {"daily_budget": 10, "stage_budgets": {}}},
                    "stage_order": [],
                    "llm_provider": {"provider": "ollama", "model": "qwen-test"},
                },
            )
            first_provider = ConfigurableFailingProvider(
                code="authentication_error",
                retryable=True,
                provider_id="ollama",
                model="qwen-test",
            )
            first_workflow = KimiDailyWorkflow(
                root,
                workflow_config=config_path,
                run_date=date(2026, 5, 5),
                llm_provider=first_provider,
                process_instance_id="process-a",
            )
            first_ledger = first_workflow.load_or_create_ledger()
            started = datetime(2026, 5, 5, 9, 0, 0)
            with patch.dict(os.environ, {"WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0"}):
                output_path = first_workflow.run_llm_plan(first_ledger, now=started)

            second_provider = ConfigurableFailingProvider(
                code="authentication_error",
                retryable=True,
                provider_id="ollama",
                model="qwen-test",
            )
            second_workflow = KimiDailyWorkflow(
                root,
                workflow_config=config_path,
                run_date=date(2026, 5, 5),
                llm_provider=second_provider,
                process_instance_id="process-b",
            )
            second_ledger = second_workflow.load_or_create_ledger()
            with patch.dict(os.environ, {"WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0"}):
                second_workflow.run_llm_plan(
                    second_ledger,
                    now=started + timedelta(seconds=1),
                )
                after_restart = json.loads(
                    output_path.read_text(encoding="utf-8")
                )["llm_plan"]
                self.assertEqual(1, len(second_provider.requests))
                self.assertEqual("process-b", after_restart["process_instance_id"])
                second_workflow.run_llm_plan(
                    second_ledger,
                    now=started + timedelta(days=1),
                )

            assert output_path is not None
            plan = json.loads(output_path.read_text(encoding="utf-8"))["llm_plan"]
            self.assertEqual(1, len(first_provider.requests))
            self.assertEqual(1, len(second_provider.requests))
            self.assertEqual(2, plan["attempt_count"])
            self.assertEqual("process-b", plan["process_instance_id"])

    def test_model_change_invalidates_previous_success_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "configs" / "workflow.json"
            base_config = {
                "capacity_estimate": {"recommended_mode": "standard"},
                "daily_budget_modes": {"standard": {"daily_budget": 10, "stage_budgets": {}}},
                "stage_order": [],
                "llm_provider": {"provider": "ollama", "model": "qwen-one"},
            }
            self._write_json(config_path, base_config)
            first_provider = RecordingProvider()
            first_provider.model = "qwen-one"
            first_workflow = KimiDailyWorkflow(
                root,
                workflow_config=config_path,
                run_date=date(2026, 5, 5),
                llm_provider=first_provider,
            )
            first_ledger = first_workflow.load_or_create_ledger()
            with patch.dict(os.environ, {"WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0"}):
                output_path = first_workflow.run_llm_plan(first_ledger)
            assert output_path is not None
            first_digest = json.loads(output_path.read_text(encoding="utf-8"))["llm_plan"]["config_digest"]

            changed_config = dict(base_config)
            changed_config["llm_provider"] = {"provider": "ollama", "model": "qwen-two"}
            self._write_json(config_path, changed_config)
            second_provider = RecordingProvider()
            second_provider.model = "qwen-two"
            second_workflow = KimiDailyWorkflow(
                root,
                workflow_config=config_path,
                run_date=date(2026, 5, 5),
                llm_provider=second_provider,
            )
            second_ledger = second_workflow.load_or_create_ledger()
            with patch.dict(os.environ, {"WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0"}):
                second_workflow.run_llm_plan(second_ledger)

            final = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(1, len(second_provider.requests))
            self.assertNotEqual(first_digest, final["llm_plan"]["config_digest"])
            self.assertEqual(second_ledger["llm_provider"]["config_digest"], final["llm_plan"]["config_digest"])
            self.assertEqual("qwen-two", final["model"])

    def test_injected_provider_needs_no_real_credential_even_for_network_or_disabled_config(self) -> None:
        cases = (
            ("openai_compatible", "gpt-test", "OPENAI_API_KEY"),
            ("anthropic", "claude-test", "ANTHROPIC_API_KEY"),
            ("gemini", "gemini-test", "GEMINI_API_KEY"),
            ("disabled", "", ""),
        )
        for configured_provider, model, key_env in cases:
            with self.subTest(provider=configured_provider), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                config_path = root / "configs" / "workflow.json"
                provider_config = {"provider": configured_provider}
                if model:
                    provider_config.update({"model": model, "api_key_env": key_env})
                self._write_json(
                    config_path,
                    {
                        "capacity_estimate": {"recommended_mode": "standard"},
                        "daily_budget_modes": {"standard": {"daily_budget": 10, "stage_budgets": {}}},
                        "stage_order": [],
                        "llm_provider": provider_config,
                    },
                )
                injected = RecordingProvider()
                injected.provider_id = configured_provider if configured_provider != "disabled" else "ollama"
                injected.model = model or "injected-model"
                env_patch = {"WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0"}
                if key_env:
                    env_patch[key_env] = ""

                with patch.dict(os.environ, env_patch):
                    workflow = KimiDailyWorkflow(
                        root,
                        workflow_config=config_path,
                        run_date=date(2026, 5, 5),
                        llm_provider=injected,
                    )
                    output_path = workflow.run_llm_plan(workflow.load_or_create_ledger())

                self.assertIsNotNone(output_path)
                self.assertEqual(1, len(injected.requests))

    def test_credential_repair_retries_error_artifact_in_same_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "configs" / "workflow.json"
            self._write_json(
                config_path,
                {
                    "capacity_estimate": {"recommended_mode": "standard"},
                    "daily_budget_modes": {"standard": {"daily_budget": 10, "stage_budgets": {}}},
                    "stage_order": [],
                    "llm_provider": {
                        "provider": "openai_compatible",
                        "model": "gpt-test",
                        "api_key_env": "OPENAI_API_KEY",
                    },
                },
            )
            with patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "",
                    "WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0",
                },
            ):
                workflow = KimiDailyWorkflow(
                    root,
                    workflow_config=config_path,
                    run_date=date(2026, 5, 5),
                )
                ledger = workflow.load_or_create_ledger()
                output_path = workflow.run_llm_plan(ledger)
            assert output_path is not None
            first = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual("error", first["llm_plan"]["status"])

            repaired_provider = RecordingProvider()
            repaired_provider.provider_id = "openai_compatible"
            repaired_provider.model = "gpt-test"
            with (
                patch.dict(
                    os.environ,
                    {
                        "OPENAI_API_KEY": "repaired-secret",
                        "WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0",
                    },
                ),
                patch(
                    "src.llm_planning.create_llm_provider",
                    return_value=repaired_provider,
                ) as create_provider,
            ):
                workflow.run_llm_plan(ledger)

            final = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual("success", final["llm_plan"]["status"])
            self.assertEqual(1, len(repaired_provider.requests))
            self.assertEqual(root.resolve(), create_provider.call_args.kwargs["workspace_root"])
            self.assertEqual(ledger["llm_provider"]["config_digest"], final["llm_plan"]["config_digest"])
            self.assertNotIn("repaired-secret", output_path.read_text(encoding="utf-8"))

    def test_facade_without_workspace_defers_cli_provider_until_call_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter = LLMPlanAdapter.from_config(
                {
                    "llm_provider": {
                        "provider": "cli",
                        "model": "cli-test",
                        "command": [
                            os.sys.executable,
                            "-c",
                            "import json,sys; print(json.dumps(dict(directions=[], workspace=sys.argv[1])))",
                            "{workspace_root}",
                            "{prompt}",
                        ],
                        "prompt_transport": "argument",
                    }
                }
            )

            with patch.dict(os.environ, {"WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0"}):
                payload = adapter.call(root, "plan")

            self.assertEqual(str(root.resolve()), payload["workspace"])

    def test_kimi_workflow_reexports_extracted_llm_plan_adapter(self) -> None:
        from src.llm_planning import LLMPlanAdapter as ExtractedLLMPlanAdapter

        self.assertIs(ExtractedLLMPlanAdapter, LLMPlanAdapter)

    def test_missing_workflow_config_fails_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaisesRegex(FileNotFoundError, "Workflow config does not exist"):
                KimiDailyWorkflow(
                    root,
                    workflow_config=Path("configs/missing-workflow.json"),
                    run_date=date(2026, 5, 5),
                )

    def test_research_policy_budget_overrides_legacy_ledger_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "configs" / "policy-workflow.json"
            policy = self._research_policy(
                daily_limit=20,
                stage_allocations={"direction_probe": 8, "scale_winners": 8, "holdout": 4},
            )
            self._write_json(
                config_path,
                {
                    "capacity_estimate": {"recommended_mode": "standard", "max_scan_concurrency": 3},
                    "daily_budget_modes": {
                        "standard": {
                            "daily_budget": 999,
                            "stage_budgets": {"legacy": 999},
                        }
                    },
                    "stage_order": ["legacy"],
                    "research_policy": policy,
                },
            )

            workflow = KimiDailyWorkflow(
                root,
                workflow_config=config_path,
                run_date=date(2026, 5, 5),
                dry_run=False,
            )
            ledger = workflow.load_or_create_ledger()
            expected_digest = policy_digest(load_research_policy({"research_policy": policy}))

            self.assertEqual(ledger["daily_budget"], 20)
            self.assertEqual(
                ledger["stage_budgets"],
                {"direction_probe": 8, "scale_winners": 8, "holdout": 4},
            )
            self.assertEqual(ledger["stage_order"], ["direction_probe", "scale_winners", "holdout"])
            self.assertEqual(ledger["research_policy"]["version"], 1)
            self.assertEqual(ledger["research_policy"]["exploration_share_limit"], 0.4)
            self.assertEqual(ledger["research_policy"]["digest"], expected_digest)
            self.assertEqual(
                ledger["research_policy"]["enabled_mechanisms"],
                ["reference_point_disposition_drift"],
            )
            self.assertEqual(ledger["research_policy"]["block_counts"], {})

    def test_research_policy_blocks_candidates_before_diversity_and_audits_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "configs" / "policy-workflow.json"
            source_config = root / ".local" / "research" / "scans" / "continuous-alpha" / "source" / "scan.json"
            policy = self._research_policy(
                daily_limit=2,
                stage_allocations={"direction_probe": 2},
            )
            self._write_json(
                config_path,
                {
                    "capacity_estimate": {"recommended_mode": "standard", "max_scan_concurrency": 3},
                    "daily_budget_modes": {"standard": {"daily_budget": 99, "stage_budgets": {}}},
                    "stage_order": ["legacy"],
                    "default_queued_scan_configs": [
                        ".local/research/scans/continuous-alpha/source/scan.json"
                    ],
                    "submitted_registry_sync_enabled": False,
                    "diversity_caps": {
                        "single_base_alpha_daily_budget_max_share": 1.0,
                        "single_field_daily_budget_max_share": 1.0,
                    },
                    "research_policy": policy,
                },
            )
            candidates = [
                {
                    "candidate_id": "blocked-first",
                    "expression": "rank(close)",
                    "behavior_family": "reference_point_disposition_drift",
                    "behavioral_mechanism": "reference_point_disposition_drift",
                    "fields": ["close"],
                    "kill_conditions": ["SELF_CORRELATION", "LOW_FITNESS"],
                },
                {
                    "expression": "rank(anl_revision_score)",
                    "behavior_family": "reference_point_disposition_drift",
                    "behavioral_mechanism": "reference_point_disposition_drift",
                    "fields": ["anl_revision_score"],
                    "kill_conditions": ["SELF_CORRELATION", "LOW_FITNESS"],
                },
                {
                    "candidate_id": "allowed-second",
                    "expression": "rank(fundamental_quality)",
                    "behavior_family": "reference_point_disposition_drift",
                    "behavioral_mechanism": "reference_point_disposition_drift",
                    "fields": ["fundamental_quality"],
                    "kill_conditions": ["SELF_CORRELATION", "LOW_FITNESS"],
                },
            ]
            self._write_json(
                source_config,
                {
                    "output": "unused.json",
                    "field_types": {
                        "close": "matrix",
                        "anl_revision_score": "matrix",
                        "fundamental_quality": "matrix",
                    },
                    "candidates": candidates,
                },
            )

            workflow = KimiDailyWorkflow(
                root,
                workflow_config=config_path,
                run_date=date(2026, 5, 5),
            )
            ledger = workflow.load_or_create_ledger()
            plan = workflow.prepare_budgeted_scan(workflow.plan_next_scan(ledger))

            assert plan.sliced_config is not None, plan
            sliced = json.loads(plan.sliced_config.read_text(encoding="utf-8"))
            self.assertEqual(
                [row["expression"] for row in sliced["candidates"]],
                ["rank(anl_revision_score)", "rank(fundamental_quality)"],
            )
            policy_context = sliced["daily_budget_context"]["research_policy"]
            self.assertEqual(policy_context["version"], 1)
            self.assertEqual(policy_context["blocked_candidates"], 1)
            self.assertEqual(policy_context["block_counts"], {"proxy_field_outside_boundary": 1, "pure_price_volume_candidate": 1})

            audit = json.loads((workflow.run_dir / "research_policy_evaluation.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["evaluated_candidates"], 3)
            self.assertEqual(audit["allowed_candidates"], 2)
            self.assertEqual(audit["blocked_candidates"], 1)
            missing_id = audit["evaluations"][1]
            self.assertEqual(missing_id["row_index"], 1)
            self.assertRegex(missing_id["candidate_id"], r"^row-000001-[0-9a-f]{12}$")
            self.assertRegex(missing_id["identity"], r"^[0-9a-f]{64}$")
            self.assertEqual(ledger["research_policy"]["blocked_candidates"], 1)
            self.assertEqual(ledger["research_policy"]["block_counts"], policy_context["block_counts"])

            workflow._apply_research_policy(source_config, [(3, candidates[0])])
            cumulative = json.loads(
                (workflow.run_dir / "research_policy_evaluation.json").read_text(encoding="utf-8")
            )
            self.assertEqual(cumulative["evaluated_candidates"], 4)
            self.assertEqual(cumulative["allowed_candidates"], 2)
            self.assertEqual(cumulative["blocked_candidates"], 2)
            self.assertEqual(len(cumulative["evaluations"]), 4)
            self.assertEqual(ledger["research_policy"]["blocked_candidates"], 2)

            policy["behavioral_boundaries"]["mechanisms"][0]["kill_conditions"].append("HIGH_TURNOVER")
            self._write_json(config_path, {**json.loads(config_path.read_text(encoding="utf-8")), "research_policy": policy})
            changed_workflow = KimiDailyWorkflow(
                root,
                workflow_config=config_path,
                run_date=date(2026, 5, 5),
            )
            changed_workflow.load_or_create_ledger()
            changed_workflow._apply_research_policy(source_config, [(0, candidates[0])])
            changed_audit = json.loads(
                (changed_workflow.run_dir / "research_policy_evaluation.json").read_text(encoding="utf-8")
            )
            self.assertEqual(changed_audit["evaluated_candidates"], 1)
            self.assertEqual(len(changed_audit["evaluations"]), 1)
            self.assertNotEqual(changed_audit["digest"], audit["digest"])

    def test_choose_budgeted_candidates_respects_budget_and_diversity(self) -> None:
        candidates = []
        for index in range(20):
            candidates.append({
                "expression": f"group_rank(rank(field_{index}) / 10 + rank(-returns) / {20 + index}, industry)",
                "behavior_family": "family_a" if index < 12 else "family_b",
                "base_alpha_id": "BASE1" if index < 10 else f"BASE{index}",
            })

        selected = choose_budgeted_candidates(candidates, 8, single_base_share=0.25, single_field_share=0.50)

        self.assertEqual(len(selected), 8)
        base1_count = sum(1 for item in selected if item.get("base_alpha_id") == "BASE1")
        self.assertLessEqual(base1_count, 2)
        self.assertTrue(any(item.get("behavior_family") == "family_b" for item in selected))

    def test_choose_budgeted_candidates_caps_repeated_skeletons_and_downweighted_families(self) -> None:
        candidates = []
        for index in range(12):
            candidates.append({
                "expression": f"group_rank(rank(crowded_field_{index}) / 10 + rank(-returns) / 20, industry)",
                "behavior_family": "weak_family",
                "note": "weak_family: crowded_proxy template0 variant 1",
            })
        for index in range(12):
            candidates.append({
                "expression": f"group_rank(rank(diverse_field_{index}) / 10 + rank(-returns) / {30 + index}, industry)",
                "behavior_family": "strong_family",
                "note": f"strong_family: diverse_proxy_{index} template{index % 3} variant {index}",
            })

        selected = choose_budgeted_candidates(
            candidates,
            10,
            single_skeleton_share=0.2,
            downweighted_families={"weak_family"},
            downweighted_family_share=0.1,
        )

        weak_count = sum(1 for item in selected if item.get("behavior_family") == "weak_family")
        crowded_count = sum(1 for item in selected if "crowded_proxy template0" in item.get("note", ""))
        self.assertLessEqual(weak_count, 1)
        self.assertLessEqual(crowded_count, 2)
        self.assertEqual(len(selected), 10)

    def test_run_once_prepares_budgeted_scan_from_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            source_config = root / ".local" / "research" / "scans" / "continuous-alpha" / "source-500" / "scan_config_round1.json"
            candidates = [
                {
                    "expression": f"group_rank(rank(field_{index}) / 10 + rank(-returns) / 20, industry)",
                    "behavior_family": "winner_neighbor_3leg" if index % 2 == 0 else "quality_pullback_3leg",
                }
                for index in range(20)
            ]
            self._write_json(source_config, {"output": "unused.json", "candidates": candidates})
            ledger_path = root / ".local" / "data" / "runs" / "continuous-alpha" / "kimi-daily-budget-20260505" / "daily_budget_ledger.json"
            self._write_json(ledger_path, {
                "daily_run_tag": "kimi-daily-budget-20260505",
                "date": "2026-05-05",
                "budget_mode": "standard",
                "daily_budget": 10,
                "spent_simulations": 0,
                "committed_simulations": 0,
                "stage_order": ["scale_winners"],
                "stage_budgets": {"scale_winners": 6},
                "stage_spend": {},
                "stage_commitments": {},
                "queued_scan_configs": [".local/research/scans/continuous-alpha/source-500/scan_config_round1.json"],
            })

            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            messages = workflow.run_once(now=datetime(2026, 5, 5, 9, 0), summary_only=False)

            self.assertTrue(any("prepared 6 candidates" in message for message in messages))
            sliced = root / ".local" / "research" / "scans" / "continuous-alpha" / "kimi-daily-budget-20260505" / "scale_winners_source-500_6.json"
            payload = json.loads(sliced.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["candidates"]), 6)
            self.assertEqual(payload["daily_budget_context"]["stage"], "scale_winners")

    def test_submitted_registry_sync_launches_worker_without_blocking_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"WQB_EMAIL": "user@example.com", "WQB_PASSWORD": "secret"},
            clear=False,
        ):
            root = Path(tmp)
            self._write_workflow_config(root)
            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            workflow.config["submitted_registry_sync_enabled"] = True
            workflow.config["submitted_registry_sync_timeout_seconds"] = 1

            with patch("src.kimi_daily_workflow.subprocess.Popen") as popen:
                popen.return_value.pid = 123
                status = workflow.sync_submitted_registry()

            self.assertEqual(status, "worker_started")
            command = popen.call_args.args[0]
            self.assertIn("scripts.workers.registry", command)

    def test_prepare_budgeted_scan_blocks_preflight_invalid_expressions_before_simulation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            source_config = root / ".local" / "research" / "scans" / "continuous-alpha" / "source-500" / "scan_config_round1.json"
            candidates = [
                {"expression": "ts_delta(event_field, 20) / cap", "behavior_family": "event_family"},
                {"expression": "group_rank(rank(good_field), industry)", "behavior_family": "valid_family"},
                {"expression": "rank(missing_field)", "behavior_family": "missing_family"},
            ]
            self._write_json(
                source_config,
                {
                    "output": "unused.json",
                    "field_types": {"event_field": "event", "cap": "matrix", "good_field": "matrix"},
                    "candidates": candidates,
                },
            )

            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            ledger = workflow.load_or_create_ledger()
            plan = workflow.prepare_budgeted_scan(workflow.plan_next_scan(ledger))

            assert plan.sliced_config is not None
            payload = json.loads(plan.sliced_config.read_text(encoding="utf-8"))
            self.assertEqual([row["expression"] for row in payload["candidates"]], ["group_rank(rank(good_field), industry)"])
            self.assertEqual(payload["daily_budget_context"]["preflight_blocked_candidates"], 2)
            report = json.loads((workflow.run_dir / "preflight_evaluation_report.json").read_text(encoding="utf-8"))
            diagnosis_types = {diagnosis["diagnosis_type"] for diagnosis in report["diagnoses"]}
            self.assertIn("field_type_operator_mismatch", diagnosis_types)
            self.assertIn("missing_field_reference", diagnosis_types)

    def test_prepare_budgeted_scan_downweights_proxy_map_families(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            config_path = root / ".local" / "research" / "workflows" / "continuous-alpha" / "kimi_daily_budget_20260504.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["diversity_caps"] = {
                "single_family_daily_budget_max_share": 0.8,
                "single_skeleton_daily_budget_max_share": 0.8,
                "downweighted_family_daily_budget_max_share": 0.1,
            }
            config["behavioral_proxy_map"] = {
                "path": ".local/data/behavioral_proxy/behavioral_proxy_map.json",
                "max_mechanisms": 5,
            }
            self._write_json(config_path, config)
            self._write_json(
                root / ".local" / "data" / "behavioral_proxy" / "behavioral_proxy_map.json",
                {
                    "mechanisms": [
                        {"mechanism": "weak_family", "budget_policy": "downweight", "result_strength": "weak"},
                        {"mechanism": "strong_family", "budget_policy": "promote", "result_strength": "promising"},
                    ]
                },
            )
            source_config = root / ".local" / "research" / "scans" / "continuous-alpha" / "source-500" / "scan_config_round1.json"
            candidates = [
                {
                    "expression": f"group_rank(rank(weak_field_{index}) / 10 + rank(-returns) / {20 + index}, industry)",
                    "behavior_family": "weak_family",
                    "note": f"weak_family: weak_proxy_{index} template0 variant {index}",
                }
                for index in range(10)
            ] + [
                {
                    "expression": f"group_rank(rank(strong_field_{index}) / 10 + rank(-returns) / {40 + index}, industry)",
                    "behavior_family": "strong_family",
                    "note": f"strong_family: strong_proxy_{index} template0 variant {index}",
                }
                for index in range(10)
            ]
            self._write_json(source_config, {"output": "unused.json", "candidates": candidates})

            workflow = KimiDailyWorkflow(
                root,
                workflow_config=Path(".local/research/workflows/continuous-alpha/kimi_daily_budget_20260504.json"),
                run_date=date(2026, 5, 5),
                execute_scans=False,
            )
            ledger = workflow.load_or_create_ledger()
            ledger["stage_order"] = ["scale_winners"]
            ledger["stage_budgets"] = {"scale_winners": 10}
            ledger["remaining_simulations_after_commitments"] = 10
            plan = workflow.prepare_budgeted_scan(workflow.plan_next_scan(ledger))

            assert plan.sliced_config is not None
            payload = json.loads(plan.sliced_config.read_text(encoding="utf-8"))
            weak_count = sum(1 for row in payload["candidates"] if row["behavior_family"] == "weak_family")
            self.assertLessEqual(weak_count, 1)
            self.assertEqual(payload["daily_budget_context"]["candidate_diversity_gate"]["downweighted_families"], ["weak_family"])

    def test_prepare_budgeted_scan_skips_previous_stage_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            source_config = root / ".local" / "research" / "scans" / "continuous-alpha" / "source-500" / "scan_config_round1.json"
            candidates = [
                {
                    "expression": f"group_rank(rank(field_{index}) / 10 + rank(ts_mean(returns, 20)) / 24, industry)",
                    "settings": {"decay": 6},
                    "behavior_family": "family_a" if index % 2 == 0 else "family_b",
                }
                for index in range(6)
            ]
            self._write_json(source_config, {"output": "unused.json", "candidates": candidates})

            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            workflow.config["stage_order"] = ["direction_probe", "scale_winners"]
            previous_slice = workflow.config_dir / "direction_probe_source-500_2.json"
            self._write_json(previous_slice, {
                "daily_budget_context": {"stage": "direction_probe"},
                "candidates": candidates[:2],
            })
            ledger = workflow.load_or_create_ledger()
            ledger["stage_order"] = ["direction_probe", "scale_winners"]
            ledger["stage_budgets"] = {"direction_probe": 2, "scale_winners": 3}
            ledger["stage_spend"] = {"direction_probe": 2}
            ledger["spent_simulations"] = 2
            ledger["remaining_simulations_after_commitments"] = 8

            plan = workflow.plan_next_scan(ledger)
            self.assertEqual(plan.stage, "scale_winners")
            prepared = workflow.prepare_budgeted_scan(plan)
            assert prepared.sliced_config is not None
            payload = json.loads(prepared.sliced_config.read_text(encoding="utf-8"))
            selected_expressions = {row["expression"] for row in payload["candidates"]}

            self.assertFalse(selected_expressions & {row["expression"] for row in candidates[:2]})
            self.assertEqual(len(payload["candidates"]), 3)

    def test_policy_stage_order_drives_cross_stage_candidate_deduplication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5))
            workflow.config.pop("stage_order", None)
            workflow.research_policy = load_research_policy(
                {
                    "research_policy": self._research_policy(
                        daily_limit=10,
                        stage_allocations={"direction_probe": 2, "scale_winners": 3, "holdout": 5},
                    )
                }
            )
            candidate = {"expression": "rank(anl_revision_score)", "settings": {"decay": 4}}
            self._write_json(
                workflow.config_dir / "direction_probe_source_1.json",
                {"daily_budget_context": {"stage": "direction_probe"}, "candidates": [candidate]},
            )

            used = workflow._used_candidate_identities_before_stage("scale_winners")

            self.assertEqual({kimi_daily_workflow_module.candidate_identity(candidate)}, used)

    def test_reconcile_existing_stage_progress_updates_ledger_before_planning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            source_config = root / ".local" / "research" / "scans" / "continuous-alpha" / "source-500" / "scan_config_round1.json"
            candidates = [
                {
                    "expression": f"group_rank(rank(field_{index}) / 10 + rank(ts_mean(returns, 20)) / 24, industry)",
                    "settings": {"decay": 6},
                }
                for index in range(10)
            ]
            self._write_json(source_config, {"output": "unused.json", "candidates": candidates})

            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            workflow.config["stage_order"] = ["direction_probe", "scale_winners"]
            ledger = workflow.load_or_create_ledger()
            ledger["stage_order"] = ["direction_probe", "scale_winners"]
            ledger["stage_budgets"] = {"direction_probe": 2, "scale_winners": 6}
            ledger["stage_spend"] = {"direction_probe": 2}
            ledger["spent_simulations"] = 2
            ledger["current_stage"] = "direction_probe_complete"
            self._write_json(workflow.ledger_path, ledger)

            scale_slice = workflow.config_dir / "scale_winners_source-500_6.json"
            scale_output = workflow.run_dir / "scale_winners_source-500_results.json"
            self._write_json(scale_slice, {
                "output": str(scale_output.relative_to(workflow.root).as_posix()),
                "daily_budget_context": {"stage": "scale_winners", "selected_candidates": 6},
                "candidates": candidates[2:8],
            })
            self._write_json(scale_output, [
                {"expression": candidate["expression"], "settings": candidate["settings"], "error": "interrupted"}
                for candidate in candidates[2:5]
            ])

            changed = workflow.reconcile_existing_stage_progress(ledger)

            self.assertTrue(changed)
            self.assertEqual(ledger["stage_spend"]["scale_winners"], 3)
            self.assertEqual(ledger["spent_simulations"], 5)
            self.assertEqual(ledger["current_stage"], "scale_winners_partial")
            self.assertEqual(ledger["remaining_simulations_after_commitments"], 5)
            self.assertEqual(
                ledger["last_completed_scan"],
                ".local/data/runs/continuous-alpha/kimi-daily-budget-20260505/scale_winners_source-500_results.json",
            )

    def test_run_once_refreshes_closed_loop_artifacts_after_reconcile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            source_config = root / ".local" / "research" / "scans" / "continuous-alpha" / "source-500" / "scan_config_round1.json"
            candidates = [
                {
                    "expression": f"group_rank(rank(field_{index}) / 10 + rank(ts_mean(returns, 20)) / 24, industry)",
                    "settings": {"decay": 6},
                }
                for index in range(10)
            ]
            self._write_json(source_config, {"output": "unused.json", "candidates": candidates})

            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            workflow.config["stage_order"] = ["direction_probe", "scale_winners"]
            ledger = workflow.load_or_create_ledger()
            ledger["stage_order"] = ["direction_probe", "scale_winners"]
            ledger["stage_budgets"] = {"direction_probe": 2, "scale_winners": 6}
            ledger["stage_spend"] = {"direction_probe": 2}
            ledger["spent_simulations"] = 2
            ledger["current_stage"] = "direction_probe_complete"
            self._write_json(workflow.ledger_path, ledger)

            scale_slice = workflow.config_dir / "scale_winners_source-500_6.json"
            scale_output = workflow.run_dir / "scale_winners_source-500_results.json"
            self._write_json(scale_slice, {
                "output": str(scale_output.relative_to(workflow.root).as_posix()),
                "daily_budget_context": {"stage": "scale_winners", "selected_candidates": 6},
                "candidates": candidates[2:8],
            })
            self._write_json(scale_output, [
                {"expression": candidate["expression"], "settings": candidate["settings"], "error": "interrupted"}
                for candidate in candidates[2:5]
            ])

            with patch.object(workflow, "run_llm_plan", return_value=None):
                messages = workflow.run_once(now=datetime(2026, 5, 5, 10, 0))

            self.assertIn("refreshed closed-loop artifacts after reconcile", messages)
            snapshot = json.loads((workflow.run_dir / "scan_results_snapshot.json").read_text(encoding="utf-8"))
            self.assertEqual(len(snapshot), 3)
            updated = json.loads(workflow.ledger_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["current_stage"], "scale_winners_partial")
            self.assertEqual(updated["spent_simulations"], 5)

    def test_prepare_budgeted_scan_skips_completed_current_stage_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            source_config = root / ".local" / "research" / "scans" / "continuous-alpha" / "source-500" / "scan_config_round1.json"
            candidates = [
                {
                    "expression": f"group_rank(rank(field_{index}) / 10 + rank(ts_mean(returns, 20)) / 24, industry)",
                    "settings": {"decay": 6},
                }
                for index in range(10)
            ]
            self._write_json(source_config, {"output": "unused.json", "candidates": candidates})

            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            workflow.config["stage_order"] = ["scale_winners"]
            scale_slice = workflow.config_dir / "scale_winners_source-500_6.json"
            scale_output = workflow.run_dir / "scale_winners_source-500_results.json"
            self._write_json(scale_slice, {
                "output": str(scale_output.relative_to(workflow.root).as_posix()),
                "daily_budget_context": {"stage": "scale_winners", "selected_candidates": 6},
                "candidates": candidates[:6],
            })
            self._write_json(scale_output, [
                {"expression": candidate["expression"], "settings": candidate["settings"], "error": "interrupted"}
                for candidate in candidates[:3]
            ])

            ledger = workflow.load_or_create_ledger()
            ledger["stage_order"] = ["scale_winners"]
            ledger["stage_budgets"] = {"scale_winners": 6}
            ledger["stage_spend"] = {"scale_winners": 3}
            ledger["spent_simulations"] = 3
            ledger["remaining_simulations_after_commitments"] = 7

            plan = workflow.plan_next_scan(ledger)
            prepared = workflow.prepare_budgeted_scan(plan)
            assert prepared.sliced_config is not None
            payload = json.loads(prepared.sliced_config.read_text(encoding="utf-8"))
            selected_expressions = {row["expression"] for row in payload["candidates"]}

            self.assertFalse(selected_expressions & {row["expression"] for row in candidates[:3]})
            self.assertEqual(len(payload["candidates"]), 3)

    def test_prepare_budgeted_scan_records_decision_attribution_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            workflow_config_path = root / ".local" / "research" / "workflows" / "production.json"
            config = json.loads(workflow_config_path.read_text(encoding="utf-8"))
            config["decision_attribution"] = {"enabled": True, "output": "decision_attribution.json"}
            self._write_json(workflow_config_path, config)
            source_config = root / ".local" / "research" / "scans" / "continuous-alpha" / "source-500" / "scan_config_round1.json"
            candidates = [
                {
                    "expression": f"group_rank(rank(field_{index}) / 10 + rank(-returns) / 20, industry)",
                    "behavior_family": "media_sentiment_reversal" if index % 2 == 0 else "reference_point_disposition_drift",
                }
                for index in range(8)
            ]
            self._write_json(source_config, {"output": "unused.json", "candidates": candidates})
            self._write_json(
                root / ".local" / "data" / "behavioral_proxy" / "behavioral_proxy_map.json",
                {
                    "mechanisms": [
                        {"mechanism": "media_sentiment_reversal", "budget_policy": "promote", "proxy_strength": "medium", "result_strength": "promising"}
                    ]
                },
            )

            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            ledger = workflow.load_or_create_ledger()
            plan = workflow.prepare_budgeted_scan(workflow.plan_next_scan(ledger))

            attribution_path = workflow.run_dir / "decision_attribution.json"
            self.assertTrue(attribution_path.exists())
            payload = json.loads(attribution_path.read_text(encoding="utf-8"))
            self.assertEqual(payload[0]["stage"], plan.stage)
            self.assertEqual(payload[0]["candidate_count"], 6)
            self.assertIn("media_sentiment_reversal", payload[0]["families_affected"])
            self.assertEqual(payload[0]["proxy_signals_used"][0]["mechanism"], "media_sentiment_reversal")

    def test_deepseek_adapter_uses_deepseek_paths_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_json(root / ".local" / "research" / "workflows" / "continuous-alpha" / "deepseek.json", {
                "capacity_estimate": {"recommended_mode": "standard", "max_scan_concurrency": 3},
                "daily_run_tag_prefix": "deepseek-v4-pro-daily-budget",
                "daily_budget_modes": {"standard": {"daily_budget": 10, "stage_budgets": {}}},
                "stage_order": ["deepseek_v4_pro_daily_direction_plan"],
                "deepseek_v4_pro": {
                    "provider": "deepseek",
                    "model": "deepseek-v4-pro",
                    "prompt_file_pattern": ".local/data/runs/continuous-alpha/{daily_run_tag}/deepseek_prompts/{stage}.md",
                    "output_file_pattern": ".local/data/runs/continuous-alpha/{daily_run_tag}/deepseek_outputs/{stage}.json",
                },
            })

            workflow = KimiDailyWorkflow(
                root,
                workflow_config=Path(".local/research/workflows/continuous-alpha/deepseek.json"),
                run_date=date(2026, 5, 5),
                execute_scans=False,
            )
            ledger = workflow.load_or_create_ledger()
            output_path = workflow.run_llm_plan(ledger)

            self.assertIsNotNone(output_path)
            assert output_path is not None
            self.assertEqual(output_path.name, "deepseek_v4_pro_daily_direction_plan.json")
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["provider"], "deepseek")
            self.assertTrue(payload["disabled"])
            prompt_path = root / ".local" / "data" / "runs" / "continuous-alpha" / "deepseek-v4-pro-daily-budget-20260505" / "deepseek_prompts" / "deepseek_v4_pro_daily_direction_plan.md"
            self.assertTrue(prompt_path.exists())

    def test_kimi_cli_adapter_keeps_existing_paths(self) -> None:
        adapter = LLMPlanAdapter.from_config({
            "kimi_cli": {
                "executable": "kimi-cli",
                "long_prompt_file_pattern": ".local/data/runs/continuous-alpha/{daily_run_tag}/kimi_prompts/{stage}.md",
                "output_file_pattern": ".local/data/runs/continuous-alpha/{daily_run_tag}/kimi_outputs/{stage}.json",
            }
        })

        root = Path("C:/workspace")
        run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "kimi-daily-budget-20260505"

        self.assertEqual(adapter.provider, "kimi_cli")
        self.assertEqual(adapter.executable, "kimi-cli")
        self.assertEqual(adapter.prompt_path(root, run_dir, "kimi-daily-budget-20260505").as_posix(), "C:/workspace/.local/data/runs/continuous-alpha/kimi-daily-budget-20260505/kimi_prompts/kimi_daily_direction_plan.md")
        self.assertEqual(adapter.output_path(root, run_dir, "kimi-daily-budget-20260505").as_posix(), "C:/workspace/.local/data/runs/continuous-alpha/kimi-daily-budget-20260505/kimi_outputs/kimi_daily_direction_plan.json")

    def test_budget_complete_report_ranks_live_pass_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            run_root = root / ".local" / "data" / "runs" / "continuous-alpha"
            candidate_path = run_root / "source-run" / "direct_submit_pre_corr.json"
            self._write_json(candidate_path, [
                {
                    "alpha_id": "A1",
                    "expression": "expr1",
                    "metrics": {"sharpe": 1.6, "fitness": 1.2, "turnover": 0.2},
                },
                {
                    "alpha_id": "A2",
                    "expression": "expr2",
                    "metrics": {"sharpe": 1.9, "fitness": 1.1, "turnover": 0.3},
                },
            ])
            live_path = run_root / "source-run" / "live-check-final" / "expanded_live_check.json"
            self._write_json(live_path, [
                self._live_result("A1", self_corr=0.62),
                self._live_result("A2", self_corr=0.85, failed=True),
            ])

            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            ledger = workflow.load_or_create_ledger()
            ledger["spent_simulations"] = 10
            ledger["remaining_simulations_after_commitments"] = 0
            self._write_json(workflow.ledger_path, ledger)
            messages = workflow.run_once(now=datetime(2026, 5, 5, 12, 0), summary_only=False)

            self.assertTrue(any("budget complete report" in message for message in messages))
            summary = json.loads((run_root / "kimi-daily-budget-20260505" / "submit_summary_budget_complete.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["submit_ready_count"], 1)
            self.assertEqual(summary["recommendation"], "A1")

    def test_budget_complete_report_includes_current_scan_pass_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "kimi-daily-budget-20260505"
            self._write_json(run_dir / "scale_winners_source-500_results.json", [
                self._scan_result_row("A1", "expr-current-pass", sharpe=1.5, fitness=1.2, turnover=0.12, self_corr=0.64),
                self._scan_result_row("A2", "expr-self-corr-fail", sharpe=1.8, fitness=1.4, turnover=0.10, self_corr=0.86, failed=True),
                self._scan_result_row("A3", "expr-low-fitness", sharpe=1.5, fitness=0.8, turnover=0.10, self_corr=0.45),
            ])

            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            ledger = workflow.load_or_create_ledger()
            ledger["spent_simulations"] = 10
            ledger["remaining_simulations_after_commitments"] = 0
            self._write_json(workflow.ledger_path, ledger)

            workflow.run_once(now=datetime(2026, 5, 5, 12, 0), summary_only=False)

            summary = json.loads((run_dir / "submit_summary_budget_complete.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["submit_ready_count"], 1)
            self.assertEqual(summary["recommendation"], "A1")
            candidate = summary["submit_ready"][0]
            self.assertEqual(candidate["validation_source"], "scan_result")
            self.assertTrue(candidate["requires_live_recheck"])
            self.assertEqual(candidate["source_path"], ".local/data/runs/continuous-alpha/kimi-daily-budget-20260505/scale_winners_source-500_results.json")

    def test_budget_complete_report_writes_closed_loop_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "kimi-daily-budget-20260505"
            direct_row = self._scan_result_row("A1", "expr-current-pass", sharpe=1.5, fitness=1.2, turnover=0.12, self_corr=0.64)
            direct_row["behavior_family"] = "limited_attention_drift"
            optimize_row = self._scan_result_row("A2", "expr-self-corr-repair", sharpe=1.8, fitness=1.4, turnover=0.10, self_corr=0.715, failed=True)
            optimize_row["behavior_family"] = "limited_attention_drift"
            low_row = self._scan_result_row("A3", "expr-low-signal", sharpe=0.9, fitness=0.7, turnover=0.10, self_corr=0.45)
            low_row["behavior_family"] = "weak_behavior"
            self._write_json(run_dir / "scale_winners_source-500_results.json", [direct_row, optimize_row, low_row])

            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            ledger = workflow.load_or_create_ledger()
            ledger["spent_simulations"] = 10
            ledger["remaining_simulations_after_commitments"] = 0
            self._write_json(workflow.ledger_path, ledger)

            workflow.run_once(now=datetime(2026, 5, 5, 12, 0), summary_only=False)

            direct_submit = json.loads((run_dir / "direct_submit.json").read_text(encoding="utf-8"))
            optimize_next = json.loads((run_dir / "optimize_next.json").read_text(encoding="utf-8"))
            low_value = json.loads((run_dir / "low_value_avoid.json").read_text(encoding="utf-8"))
            backlog = json.loads((run_dir / "submission_backlog.json").read_text(encoding="utf-8"))
            state = json.loads((run_dir / "iteration_state.json").read_text(encoding="utf-8"))
            summary = json.loads((run_dir / "submit_summary_budget_complete.json").read_text(encoding="utf-8"))
            diagnosis_policy = json.loads((run_dir / "diagnosis_policy_evaluation.json").read_text(encoding="utf-8"))
            output_evaluation = json.loads((run_dir / "output_evaluation_report.json").read_text(encoding="utf-8"))

            self.assertEqual([row["alpha_id"] for row in direct_submit], ["A1"])
            self.assertEqual([row["alpha_id"] for row in optimize_next], ["A2"])
            self.assertEqual(len(low_value), 1)
            self.assertEqual([row["alpha_id"] for row in backlog], ["A1"])
            self.assertEqual(backlog[0]["recommended_action"], "live_recheck_then_submit")
            self.assertEqual(state["counts"]["direct_submit"], 1)
            self.assertEqual(state["counts"]["optimize_next"], 1)
            self.assertEqual(
                state["workflow_config_path"],
                ".local/research/workflows/production.json",
            )
            self.assertEqual(summary["closed_loop"]["counts"]["submit_ready"], 1)
            self.assertIn("diagnosis_policy_evaluation", state["artifacts"])
            self.assertGreaterEqual(diagnosis_policy["policy_count"], 1)
            self.assertIn("output_evaluation_report", state["artifacts"])
            self.assertGreaterEqual(output_evaluation["record_count"], 1)
            self.assertTrue((run_dir / "triage_summary.md").exists())

    def test_closed_loop_routes_self_corr_by_value_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "kimi-daily-budget-20260505"
            mild_row = self._scan_result_row("A1", "expr-mild-self-corr", sharpe=1.7, fitness=1.2, turnover=0.10, self_corr=0.715, failed=True)
            not_near_row = self._scan_result_row("A3", "expr-not-near-self-corr", sharpe=1.7, fitness=1.2, turnover=0.10, self_corr=0.73, failed=True)
            extreme_row = self._scan_result_row("A2", "expr-extreme-self-corr", sharpe=1.7, fitness=1.2, turnover=0.10, self_corr=0.94, failed=True)
            self._write_json(run_dir / "scale_winners_source-500_results.json", [mild_row, not_near_row, extreme_row])

            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            ledger = workflow.load_or_create_ledger()
            ledger["spent_simulations"] = 10
            ledger["remaining_simulations_after_commitments"] = 0
            self._write_json(workflow.ledger_path, ledger)

            workflow.run_once(now=datetime(2026, 5, 5, 12, 0), summary_only=False)

            optimize_next = json.loads((run_dir / "optimize_next.json").read_text(encoding="utf-8"))
            low_value = json.loads((run_dir / "low_value_avoid.json").read_text(encoding="utf-8"))
            snapshot = json.loads((run_dir / "scan_results_snapshot.json").read_text(encoding="utf-8"))
            by_id = {row["alpha_id"]: row for row in snapshot}

            self.assertEqual([row["alpha_id"] for row in optimize_next], ["A1"])
            self.assertEqual(by_id["A1"]["route_decision"], "self_corr_light_repair")
            self.assertEqual(by_id["A3"]["triage_bucket"], "low_value")
            self.assertEqual(by_id["A3"]["route_decision"], "self_corr_escape")
            self.assertEqual(by_id["A2"]["triage_bucket"], "low_value")
            self.assertEqual(by_id["A2"]["route_decision"], "replace_overcrowded_signal")
            self.assertEqual(by_id["A2"]["failure_diagnoses"][0]["evidence"]["self_corr_bucket"], "extreme")
            self.assertGreaterEqual(len(low_value), 1)

    def test_closed_loop_routes_other_failures_by_value_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "kimi-daily-budget-20260505"
            severe_sub = self._scan_result_row("A1", "expr-severe-sub", sharpe=1.7, fitness=1.2, turnover=0.10, self_corr=0.61)
            severe_sub["checks"].append({"name": "LOW_SUB_UNIVERSE_SHARPE", "result": "FAIL", "limit": 0.70, "value": 0.20})
            deep_weak = self._scan_result_row("A2", "expr-deep-weak", sharpe=0.62, fitness=0.25, turnover=0.10, self_corr=0.45)
            concentrated = self._scan_result_row("A3", "expr-concentrated", sharpe=1.7, fitness=1.2, turnover=0.10, self_corr=0.61)
            concentrated["checks"].append({"name": "CONCENTRATED_WEIGHT", "result": "FAIL", "limit": 0.10, "value": 0.24})
            self._write_json(run_dir / "scale_winners_source-500_results.json", [severe_sub, deep_weak, concentrated])

            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            ledger = workflow.load_or_create_ledger()
            ledger["spent_simulations"] = 10
            ledger["remaining_simulations_after_commitments"] = 0
            self._write_json(workflow.ledger_path, ledger)

            workflow.run_once(now=datetime(2026, 5, 5, 12, 0), summary_only=False)

            snapshot = json.loads((run_dir / "scan_results_snapshot.json").read_text(encoding="utf-8"))
            by_id = {row["alpha_id"]: row for row in snapshot}

            self.assertEqual(by_id["A1"]["route_decision"], "replace_unstable_universe_proxy")
            self.assertEqual(by_id["A1"]["triage_bucket"], "low_value")
            self.assertEqual(by_id["A2"]["route_decision"], "replace_weak_behavior_proxy")
            self.assertEqual(by_id["A3"]["route_decision"], "replace_concentrated_expression_structure")
            self.assertEqual(by_id["A3"]["failure_diagnoses"][0]["diagnosis_type"], "weight_concentration")

    def test_closed_loop_artifacts_include_failure_diagnosis_objects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "kimi-daily-budget-20260505"
            optimize_row = self._scan_result_row(
                "A2",
                "expr-self-corr-repair",
                sharpe=1.8,
                fitness=1.4,
                turnover=0.10,
                self_corr=0.715,
                failed=True,
            )
            optimize_row["behavior_family"] = "quality_value_reversal"
            low_row = self._scan_result_row(
                "A3",
                "expr-low-signal",
                sharpe=0.82,
                fitness=0.41,
                turnover=0.10,
                self_corr=0.45,
            )
            low_row["checks"][0]["result"] = "FAIL"
            low_row["behavior_family"] = "attention_amplified_anomaly"
            event_error_row = {
                "expression": "ts_delta(event_field, 20) / cap",
                "settings": {"decay": 6},
                "metrics": {},
                "checks": [],
                "error": "Simulation returned message: Operator ts_delta does not support event inputs.",
                "behavior_family": "event_field_family",
            }
            self._write_json(run_dir / "scale_winners_source-500_results.json", [optimize_row, low_row, event_error_row])

            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            ledger = workflow.load_or_create_ledger()
            ledger["spent_simulations"] = 10
            ledger["remaining_simulations_after_commitments"] = 0
            self._write_json(workflow.ledger_path, ledger)

            workflow.run_once(now=datetime(2026, 5, 5, 12, 0), summary_only=False)

            optimize_next = json.loads((run_dir / "optimize_next.json").read_text(encoding="utf-8"))
            low_value = json.loads((run_dir / "low_value_avoid.json").read_text(encoding="utf-8"))
            snapshot = json.loads((run_dir / "scan_results_snapshot.json").read_text(encoding="utf-8"))

            self.assertEqual(optimize_next[0]["failure_diagnoses"][0]["diagnosis_type"], "overcrowded_skeleton")
            low_diagnosis_types = {
                diagnosis["diagnosis_type"]
                for row in low_value
                for diagnosis in row["failure_diagnoses"]
            }
            self.assertIn("weak_behavior_proxy", low_diagnosis_types)
            self.assertIn("field_type_operator_mismatch", low_diagnosis_types)
            self.assertTrue(all("failure_diagnoses" in row for row in snapshot))

    def test_next_day_starts_only_after_midnight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            source_config = root / ".local" / "research" / "scans" / "continuous-alpha" / "source-500" / "scan_config_round1.json"
            self._write_json(source_config, {
                "output": "unused.json",
                "candidates": [
                    {
                        "expression": f"group_rank(rank(field_{index}) / 10 + rank(-returns) / 20, industry)",
                        "behavior_family": "winner_neighbor_3leg",
                    }
                    for index in range(10)
                ],
            })
            first_ledger = root / ".local" / "data" / "runs" / "continuous-alpha" / "kimi-daily-budget-20260505" / "daily_budget_ledger.json"
            self._write_json(first_ledger, {
                "daily_run_tag": "kimi-daily-budget-20260505",
                "date": "2026-05-05",
                "budget_mode": "standard",
                "daily_budget": 10,
                "spent_simulations": 0,
                "committed_simulations": 0,
                "stage_order": ["scale_winners"],
                "stage_budgets": {"scale_winners": 6},
                "stage_spend": {},
                "stage_commitments": {},
                "queued_scan_configs": [".local/research/scans/continuous-alpha/source-500/scan_config_round1.json"],
            })

            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            ledger = workflow.load_or_create_ledger()
            ledger["spent_simulations"] = 10
            ledger["remaining_simulations_after_commitments"] = 0
            self._write_json(workflow.ledger_path, ledger)
            workflow.run_once(now=datetime(2026, 5, 5, 17, 0), summary_only=False)
            workflow._set_run_date(date(2026, 5, 6))
            messages = workflow.run_once(now=datetime(2026, 5, 6, 9, 0), summary_only=False)

            self.assertEqual(workflow.run_tag, "kimi-daily-budget-20260506")
            self.assertTrue(any("prepared 6 candidates" in message for message in messages))
            next_ledger = root / ".local" / "data" / "runs" / "continuous-alpha" / "kimi-daily-budget-20260506" / "daily_budget_ledger.json"
            self.assertTrue(next_ledger.exists())
            next_config = root / ".local" / "research" / "scans" / "continuous-alpha" / "kimi-daily-budget-20260506" / "scale_winners_source-500_6.json"
            self.assertTrue(next_config.exists())

    def test_future_run_date_does_not_write_time_based_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            source_config = root / ".local" / "research" / "scans" / "continuous-alpha" / "source-500" / "scan_config_round1.json"
            self._write_json(source_config, {
                "output": "unused.json",
                "candidates": [
                    {
                        "expression": f"group_rank(rank(field_{index}) / 10 + rank(-returns) / 20, industry)",
                        "behavior_family": "winner_neighbor_3leg",
                    }
                    for index in range(10)
                ],
            })

            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 6), execute_scans=False)
            messages = workflow.run_once(now=datetime(2026, 5, 5, 17, 30), summary_only=False)

            self.assertTrue(any("waiting for daily start" in message for message in messages))
            self.assertFalse(any("daily report" in message for message in messages))
            self.assertFalse(any("budget complete report" in message for message in messages))
            self.assertFalse(any("prepared 6 candidates" in message for message in messages))
            premature_summary = root / ".local" / "data" / "runs" / "continuous-alpha" / "kimi-daily-budget-20260506" / "submit_summary_budget_complete.json"
            self.assertFalse(premature_summary.exists())

    def test_execute_scan_reconciles_existing_results_on_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            source_config = root / ".local" / "research" / "scans" / "continuous-alpha" / "source-500" / "scan_config_round1.json"
            candidates = [
                {
                    "expression": f"group_rank(rank(field_{index}) / 10 + rank(-returns) / 20, industry)",
                    "behavior_family": "winner_neighbor_3leg",
                    "settings": {"delay": 1, "decay": 4},
                }
                for index in range(10)
            ]
            self._write_json(source_config, {"output": "unused.json", "candidates": candidates})

            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=True)
            ledger = workflow.load_or_create_ledger()
            plan = workflow.plan_next_scan(ledger)
            plan = workflow.prepare_budgeted_scan(plan)

            partial_rows = []
            assert plan.output_path is not None
            assert plan.sliced_config is not None
            sliced_payload = json.loads(plan.sliced_config.read_text(encoding="utf-8"))
            sliced_candidates = sliced_payload["candidates"]
            for candidate in sliced_candidates[:3]:
                partial_rows.append({
                    "expression": candidate["expression"],
                    "settings": candidate.get("settings", {}),
                    "error": "previously interrupted",
                })
            self._write_json(plan.output_path, partial_rows)

            def fake_run(*args, **kwargs):
                rows = json.loads(plan.output_path.read_text(encoding="utf-8"))
                seen = {(row["expression"], json.dumps(row.get("settings", {}), sort_keys=True, ensure_ascii=False)) for row in rows}
                for candidate in sliced_candidates:
                    key = (candidate["expression"], json.dumps(candidate.get("settings", {}), sort_keys=True, ensure_ascii=False))
                    if key in seen:
                        continue
                    rows.append({
                        "expression": candidate["expression"],
                        "settings": candidate.get("settings", {}),
                        "alpha_id": "A1",
                        "metrics": {"sharpe": 1.3, "fitness": 1.0, "turnover": 0.2},
                        "checks": [],
                    })
                plan.output_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
                return subprocess.CompletedProcess(args[0], 0)

            with patch("src.kimi_daily_workflow.subprocess.run", side_effect=fake_run):
                spent = workflow.execute_scan(plan, ledger)

            self.assertEqual(spent, 6)
            self.assertEqual(ledger["spent_simulations"], 6)
            self.assertEqual(ledger["stage_spend"]["scale_winners"], 6)
            self.assertEqual(ledger["current_stage"], "scale_winners_complete")

    def test_execute_scan_credits_stage_budget_when_resume_slice_completes_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            source_config = root / ".local" / "research" / "scans" / "continuous-alpha" / "source-500" / "scan_config_round1.json"
            candidates = [
                {
                    "expression": f"group_rank(rank(field_{index}) / 10 + rank(-returns) / 20, industry)",
                    "settings": {"decay": 6},
                }
                for index in range(10)
            ]
            self._write_json(source_config, {"output": "unused.json", "candidates": candidates})

            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=True)
            ledger = workflow.load_or_create_ledger()
            ledger["stage_budgets"] = {"scale_winners": 6}
            ledger["stage_spend"] = {"scale_winners": 5}
            ledger["spent_simulations"] = 9
            ledger["daily_budget"] = 10
            ledger["remaining_simulations_after_commitments"] = 1
            plan = workflow.plan_next_scan(ledger)
            plan = workflow.prepare_budgeted_scan(plan)
            assert plan.output_path is not None
            assert plan.sliced_config is not None
            sliced_payload = json.loads(plan.sliced_config.read_text(encoding="utf-8"))

            def fake_run(*args, **kwargs):
                rows = [
                    {
                        "expression": candidate["expression"],
                        "settings": candidate.get("settings", {}),
                        "alpha_id": f"A{index}",
                        "metrics": {"sharpe": 0.5, "fitness": 0.2, "turnover": 0.1},
                        "checks": [{"name": "LOW_SHARPE", "result": "FAIL"}],
                    }
                    for index, candidate in enumerate(sliced_payload["candidates"])
                ]
                plan.output_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
                return subprocess.CompletedProcess(args[0], 0)

            with patch("src.kimi_daily_workflow.subprocess.run", side_effect=fake_run):
                spent = workflow.execute_scan(plan, ledger)

            self.assertEqual(spent, 1)
            self.assertEqual(ledger["stage_spend"]["scale_winners"], 6)
            self.assertEqual(ledger["spent_simulations"], 10)
            self.assertEqual(ledger["remaining_simulations_after_commitments"], 0)
            self.assertEqual(ledger["current_stage"], "scale_winners_complete")

    def test_budget_complete_report_never_sends_email_even_when_env_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            ledger = workflow.load_or_create_ledger()
            ledger["spent_simulations"] = 10
            ledger["remaining_simulations_after_commitments"] = 0

            with patch.dict(os.environ, {
                "WQB_NOTIFY_EMAIL_ENABLED": "1",
                "WQB_NOTIFY_EMAIL_TO": "notify@example.com",
                "WQB_NOTIFY_EMAIL_FROM": "from@example.com",
                "WQB_SMTP_HOST": "smtp.example.com",
                "WQB_SMTP_PORT": "587",
                "WQB_SMTP_USERNAME": "smtp-user",
                "WQB_SMTP_PASSWORD": "smtp-pass",
            }, clear=False), patch("smtplib.SMTP", side_effect=AssertionError("SMTP should not be called")), patch(
                "smtplib.SMTP_SSL",
                side_effect=AssertionError("SMTP_SSL should not be called"),
            ):
                workflow.write_daily_report(ledger, now=datetime(2026, 5, 5, 12, 0), reason="budget_complete", force=True)
                workflow.write_daily_report(ledger, now=datetime(2026, 5, 5, 12, 1), reason="budget_complete", force=True)

            self.assertFalse(ledger.get("completion_email_sent_at"))
            self.assertFalse(ledger.get("completion_email_error"))

    def test_collect_submit_ready_prefers_current_run_and_filters_submitted_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            current_run = root / ".local" / "data" / "runs" / "continuous-alpha" / "kimi-daily-budget-20260505"
            historical_run = root / ".local" / "data" / "runs" / "continuous-alpha" / "older-run"

            self._write_json(current_run / "current_submit_candidate_snapshot.json", [
                {"alpha_id": "A1", "expression": "expr-current", "metrics": {"sharpe": 1.7, "fitness": 1.2, "turnover": 0.2}},
            ])
            self._write_json(historical_run / "direct_submit_pre_corr.json", [
                {"alpha_id": "A1", "expression": "expr-historical", "metrics": {"sharpe": 1.4, "fitness": 1.0, "turnover": 0.3}},
                {"alpha_id": "A2", "expression": "expr-submitted", "metrics": {"sharpe": 1.8, "fitness": 1.3, "turnover": 0.2}},
            ])
            self._write_json(historical_run / "live-check-final" / "expanded_live_check.json", [
                self._live_result("A1", self_corr=0.62),
                self._live_result("A2", self_corr=0.55),
            ])
            self._write_json(root / ".local" / "data" / "registry" / "submitted_alphas.json", {
                "submitted": [
                    {"alpha_id": "A2", "expression": "expr-submitted", "status": "ACTIVE", "dateSubmitted": "2026-05-05T00:00:00"},
                ]
            })

            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            ready = workflow.collect_submit_ready()

            self.assertEqual([row["alpha_id"] for row in ready], ["A1"])
            self.assertEqual(ready[0]["expression"], "expr-current")
            self.assertEqual(ready[0]["source_path"], ".local/data/runs/continuous-alpha/kimi-daily-budget-20260505/current_submit_candidate_snapshot.json")

    def test_collect_submit_ready_filters_failed_submit_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            run_root = root / ".local" / "data" / "runs" / "continuous-alpha"
            historical_run = run_root / "older-run"

            self._write_json(historical_run / "direct_submit_pre_corr.json", [
                {"alpha_id": "A1", "expression": "expr-stale", "metrics": {"sharpe": 1.6, "fitness": 1.2, "turnover": 0.2}},
            ])
            self._write_json(historical_run / "live-check-final" / "expanded_live_check.json", [
                self._live_result("A1", self_corr=0.62),
            ])
            self._write_json(historical_run / "direct_submit_results_20260506.json", {
                "results": [
                    {
                        "alpha_id": "A1",
                        "action": "submit_unconfirmed",
                        "submitted": False,
                        "post_attempted": True,
                    }
                ]
            })

            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            ready = workflow.collect_submit_ready()

            self.assertEqual(ready, [])

    def test_collect_submit_ready_filters_historical_submission_state_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            run_root = root / ".local" / "data" / "runs" / "continuous-alpha"
            historical_run = run_root / "older-run"

            self._write_json(historical_run / "direct_submit_pre_corr.json", [
                {"alpha_id": "A1", "expression": "expr-submitted", "metrics": {"sharpe": 1.6, "fitness": 1.2, "turnover": 0.2}},
                {"alpha_id": "A2", "expression": "expr-fresh", "metrics": {"sharpe": 1.5, "fitness": 1.1, "turnover": 0.2}},
            ])
            self._write_json(historical_run / "live-check-final" / "expanded_live_check.json", [
                self._live_result("A1", self_corr=0.62),
                self._live_result("A2", self_corr=0.61),
            ])
            self._write_json(run_root / "submitted-elsewhere" / "submission_state.json", {
                "jobs": [
                    {"alpha_id": "A1", "status": "submitted_confirmed"},
                ]
            })

            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            ready = workflow.collect_submit_ready()

            self.assertEqual([row["alpha_id"] for row in ready], ["A2"])

    def test_run_once_syncs_submitted_registry_before_reporting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            ledger = workflow.load_or_create_ledger()
            ledger["spent_simulations"] = 10
            ledger["remaining_simulations_after_commitments"] = 0
            self._write_json(workflow.ledger_path, ledger)

            calls: list[str] = []

            def fake_sync() -> str:
                calls.append("sync")
                return "ok"

            def fake_report(*args, **kwargs):
                calls.append("report")
                return workflow.run_dir / "submit_summary_budget_complete.json", workflow.run_dir / "submit_summary_budget_complete.md"

            with patch.object(workflow, "sync_submitted_registry", side_effect=fake_sync), patch.object(workflow, "write_daily_report", side_effect=fake_report):
                workflow.run_once(now=datetime(2026, 5, 5, 12, 0), summary_only=False)

            self.assertEqual(calls, ["sync", "report"])

    def test_run_until_budget_complete_stays_on_fixed_run_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 8), execute_scans=False)
            self._write_json(workflow.ledger_path, {
                "daily_run_tag": workflow.run_tag,
                "date": "2026-05-08",
                "daily_budget": 10,
                "spent_simulations": 10,
                "remaining_simulations_after_commitments": 0,
                "last_budget_complete_report": None,
            })

            calls: list[date] = []

            def fake_run_once(*, now=None, summary_only=False):
                calls.append(workflow.run_date)
                ledger = json.loads(workflow.ledger_path.read_text(encoding="utf-8"))
                ledger["last_budget_complete_report"] = ".local/data/runs/continuous-alpha/kimi-daily-budget-20260508/submit_summary_budget_complete.md"
                workflow.ledger_path.write_text(json.dumps(ledger, indent=2, ensure_ascii=False), encoding="utf-8")
                return ["budget complete report: .local/data/runs/continuous-alpha/kimi-daily-budget-20260508/submit_summary_budget_complete.md"]

            with patch.object(workflow, "run_once", side_effect=fake_run_once):
                workflow.run_until_budget_complete(poll_seconds=1)

            self.assertEqual(calls, [date(2026, 5, 8)])
            self.assertEqual(workflow.run_date, date(2026, 5, 8))
            self.assertEqual(workflow.run_tag, "kimi-daily-budget-20260508")

    def test_run_daemon_stop_after_summary_does_not_advance_past_completed_run_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            run_date = date(2000, 1, 1)
            workflow = KimiDailyWorkflow(root, run_date=run_date, execute_scans=False)
            self._write_json(workflow.ledger_path, {
                "daily_run_tag": workflow.run_tag,
                "date": run_date.isoformat(),
                "daily_budget": 10,
                "spent_simulations": 10,
                "remaining_simulations_after_commitments": 0,
                "last_budget_complete_report": f".local/data/runs/continuous-alpha/{workflow.run_tag}/submit_summary_budget_complete.md",
            })

            with patch.object(workflow, "run_once", side_effect=AssertionError("stop-after-summary should not start the next day")), patch(
                "src.kimi_daily_workflow.time.sleep",
                side_effect=AssertionError("stop-after-summary should return without sleeping"),
            ):
                workflow.run_daemon(poll_seconds=1, continue_next_day=False)

            self.assertEqual(workflow.run_date, run_date)
            self.assertEqual(workflow.run_tag, "kimi-daily-budget-20000101")

    def test_main_defaults_to_run_until_budget_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)

            def fake_run_until_budget_complete(self, *, poll_seconds=900):
                print(f"looped:{self.run_tag}:{poll_seconds}")

            with patch.object(kimi_daily_workflow_module.KimiDailyWorkflow, "run_until_budget_complete", fake_run_until_budget_complete), patch.object(
                kimi_daily_workflow_module.KimiDailyWorkflow,
                "run_once",
                side_effect=AssertionError("run_once should not be the default entrypoint"),
            ), patch(
                "sys.argv",
                [
                    "kimi_daily_workflow",
                    "--workspace-root",
                    str(root),
                    "--workflow-config",
                    ".local/research/workflows/continuous-alpha/kimi_daily_budget_20260504.json",
                    "--date",
                    "2026-05-05",
                ],
            ), patch("sys.stdout", new_callable=io.StringIO) as stdout:
                exit_code = kimi_daily_workflow_module.main()

            self.assertEqual(exit_code, 0)
            self.assertIn("looped:kimi-daily-budget-20260505:900", stdout.getvalue())

    def test_deepseek_latest_behavioral_config_covers_new_research_families(self) -> None:
        root = Path(__file__).resolve().parents[1]
        config_path = root / ".local" / "research" / "workflows" / "continuous-alpha" / "deepseek_v4_pro_daily_budget.json"
        scan_config_path = root / ".local" / "research" / "scans" / "continuous-alpha" / "latest-behavioral-20260704" / "scan_config_round1.json"
        required = {
            "media_sentiment_reversal",
            "attention_amplified_anomaly",
            "limits_to_arbitrage_conditioned_mispricing",
            "reference_point_disposition_drift",
        }

        if not config_path.exists() or not scan_config_path.exists():
            self.skipTest("private research configs are intentionally excluded from the public snapshot")

        workflow_config = json.loads(config_path.read_text(encoding="utf-8"))
        priors = workflow_config["current_direction_priors"]
        configured_priors = set(priors["promote"]) | set(priors["controlled"]) | set(priors["downweight"])

        self.assertTrue(required <= configured_priors)
        self.assertNotIn("default_queued_scan_configs", workflow_config)

        scan_config = json.loads(scan_config_path.read_text(encoding="utf-8"))
        candidates = scan_config["candidates"]
        families = {row["behavior_family"] for row in candidates}

        self.assertTrue(required <= families)
        for family in required:
            self.assertGreaterEqual(sum(1 for row in candidates if row["behavior_family"] == family), 120)

        workflow = KimiDailyWorkflow(
            root,
            workflow_config=Path(".local/research/workflows/continuous-alpha/deepseek_v4_pro_daily_budget.json"),
            run_date=date(2026, 7, 5),
            budget_mode="standard",
            execute_scans=False,
            dry_run=True,
        )
        ledger = workflow.load_or_create_ledger()
        plan = workflow.plan_next_scan(ledger)
        self.assertEqual(plan.stage, "direction_probe")
        caps = workflow_config["diversity_caps"]
        selected = choose_budgeted_candidates(
            candidates,
            1000,
            single_base_share=float(caps["single_base_alpha_daily_budget_max_share"]),
            single_field_share=float(caps["single_field_daily_budget_max_share"]),
            pure_price_volume_share=float(caps["pure_price_volume_standalone_daily_budget_max_share"]),
        )
        selected_families = {row["behavior_family"] for row in selected}

        self.assertTrue(required <= selected_families)
        self.assertLessEqual(self._price_volume_only_count(selected), 100)

    def test_deepseek_config_enables_autonomous_loop_and_auto_submit(self) -> None:
        root = Path(__file__).resolve().parents[1]
        config_path = root / ".local" / "research" / "workflows" / "continuous-alpha" / "deepseek_v4_pro_daily_budget.json"
        if not config_path.exists():
            self.skipTest("private workflow config is intentionally excluded from the public snapshot")
        workflow_config = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertTrue(workflow_config["autonomous_loop"]["enabled"])
        self.assertTrue(workflow_config["autonomous_loop"]["auto_submit"])
        self.assertEqual(workflow_config["autonomous_loop"]["max_daily_budget"], 1000)
        self.assertTrue(workflow_config["auto_submit_direct"]["enabled"])

    def test_auto_submit_enqueues_backlog_and_launches_submission_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            workflow = KimiDailyWorkflow(root, run_date=date(2026, 7, 5), budget_mode="standard", execute_scans=False)
            workflow.config["auto_submit_direct"] = {"enabled": True}
            self._write_json(
                workflow.run_dir / "submission_backlog.json",
                [
                    {"alpha_id": "A1", "recommended_action": "live_recheck_then_submit", "score": 5.0},
                    {"alpha_id": "A2", "recommended_action": "submit", "score": 4.0},
                ],
            )

            with patch.dict(os.environ, {"WQB_LIVE_SUBMIT_CAPABILITY": "1"}), patch.object(
                kimi_daily_workflow_module.subprocess,
                "Popen",
            ) as popen:
                popen.return_value.pid = 1234
                message = workflow._auto_submit_direct()

            self.assertIn("submission worker", message or "")
            state = json.loads((workflow.run_dir / "submission_state.json").read_text(encoding="utf-8"))
            self.assertEqual([job["alpha_id"] for job in state["jobs"]], ["A1", "A2"])
            popen.assert_called_once()
            command = popen.call_args.args[0]
            self.assertIn("scripts.submit.submission_worker", command)
            self.assertIn("--daemon", command)
            checkpoint = json.loads(
                (workflow.run_dir / "stage_checkpoints" / "submission.json").read_text(encoding="utf-8")
            )
            self.assertEqual("completed", checkpoint["status"])
            self.assertTrue(
                checkpoint["extensions"]["remote_execution_delegated_to_journaled_worker"]
            )

    def test_llm_prompt_includes_behavioral_proxy_map_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow_config_path = root / ".local" / "research" / "workflows" / "continuous-alpha" / "kimi_daily_budget_20260504.json"
            proxy_map_path = root / ".local" / "data" / "behavioral_proxy" / "behavioral_proxy_map.json"
            self._write_json(workflow_config_path, {
                "capacity_estimate": {"recommended_mode": "standard", "max_scan_concurrency": 3},
                "daily_budget_modes": {
                    "standard": {
                        "daily_budget": 1000,
                        "stage_budgets": {"direction_probe": 120},
                    }
                },
                "stage_order": ["direction_probe"],
                "default_queued_scan_configs": [],
                "current_direction_priors": {"promote": [], "controlled": [], "downweight": []},
                "objective": {"primary": "maximize_final_submitted_count"},
                "behavioral_proxy_map": {
                    "path": ".local/data/behavioral_proxy/behavioral_proxy_map.json",
                    "max_mechanisms": 2,
                },
            })
            self._write_json(proxy_map_path, {
                "mechanisms": [
                    {
                        "mechanism": "media_sentiment_reversal",
                        "label_zh": "媒体/情绪反转",
                        "proxy_strength": "medium",
                        "result_strength": "promising",
                        "budget_policy": "promote",
                        "field_evidence": {"matched_field_count": 508},
                        "result_feedback": {"tested_count": 18, "all_pass_count": 1, "near_pass_count": 1},
                        "rationale_zh": "已经出现全检查通过的候选。",
                    },
                    {
                        "mechanism": "reference_point_disposition_drift",
                        "label_zh": "参考点/处置效应漂移",
                        "proxy_strength": "medium",
                        "result_strength": "weak",
                        "budget_policy": "downweight",
                        "field_evidence": {"matched_field_count": 73},
                        "result_feedback": {"tested_count": 18, "all_pass_count": 0, "near_pass_count": 0},
                        "rationale_zh": "当前模拟结果偏弱。",
                    },
                ],
            })

            workflow = KimiDailyWorkflow(
                root,
                workflow_config=Path(".local/research/workflows/continuous-alpha/kimi_daily_budget_20260504.json"),
                run_date=date(2026, 5, 5),
                budget_mode="standard",
                dry_run=True,
            )
            prompt = workflow._build_llm_prompt({"daily_budget": 1000, "spent_simulations": 0, "remaining_simulations_after_commitments": 1000})

            self.assertIn("Behavioral proxy map", prompt)
            self.assertIn("media_sentiment_reversal", prompt)
            self.assertIn("budget_policy=promote", prompt)
            self.assertIn("reference_point_disposition_drift", prompt)
            self.assertIn("budget_policy=downweight", prompt)

    def test_workflow_launches_memory_worker_after_closed_loop_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            config_path = root / ".local" / "research" / "workflows" / "production.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["post_stage_memory_sync"] = {"enabled": True, "db_path": ".local/data/memory/alpha_memory.db"}
            self._write_json(config_path, config)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "kimi-daily-budget-20260505"
            self._write_json(
                run_dir / "daily_budget_ledger.json",
                {
                    "daily_run_tag": "kimi-daily-budget-20260505",
                    "date": "2026-05-05",
                    "budget_mode": "standard",
                    "daily_budget": 10,
                    "spent_simulations": 6,
                    "committed_simulations": 0,
                    "stage_order": ["scale_winners"],
                    "stage_budgets": {"scale_winners": 6},
                    "stage_spend": {"scale_winners": 6},
                    "queued_scan_configs": [],
                    "current_stage": "scale_winners_complete",
                },
            )
            self._write_json(
                run_dir / "scale_winners_results.json",
                [
                    {
                        "alpha_id": "A1",
                        "expression": "rank(ts_mean(cashflow, 60)) - rank(close)",
                        "metrics": {"sharpe": 1.6, "fitness": 1.1, "turnover": 0.12},
                        "checks": [{"name": "SELF_CORRELATION", "result": "FAIL", "value": 0.8}],
                        "note": "quality_value_reversal: synthetic",
                    }
                ],
            )

            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            ledger = workflow.load_or_create_ledger()
            with patch.object(kimi_daily_workflow_module.subprocess, "Popen") as popen:
                popen.return_value.pid = 456
                state = workflow.write_closed_loop_artifacts(ledger)

            self.assertIn("memory_sync_state", state["artifacts"])
            self.assertEqual(state["artifacts"]["memory_sync_state"], ".local/data/runs/continuous-alpha/kimi-daily-budget-20260505/memory_sync_state.json")
            command = popen.call_args.args[0]
            self.assertIn("scripts.workers.memory", command)
            self.assertIn("--once", command)
            output_evaluation = json.loads((run_dir / "output_evaluation_report.json").read_text(encoding="utf-8"))
            artifacts = {record["artifact"] for record in output_evaluation["records"]}
            self.assertNotIn("memory_sync_report.json", artifacts)

    def _write_workflow_config(self, root: Path) -> None:
        payload = {
            "daily_run_tag_prefix": "kimi-daily-budget",
            "capacity_estimate": {"recommended_mode": "standard", "max_scan_concurrency": 3},
            "daily_budget_modes": {
                "standard": {
                    "daily_budget": 10,
                    "stage_budgets": {"scale_winners": 6},
                }
            },
            "stage_order": ["scale_winners"],
            "default_queued_scan_configs": [".local/research/scans/continuous-alpha/source-500/scan_config_round1.json"],
            "submitted_registry_sync_enabled": False,
            "diversity_caps": {
                "single_base_alpha_daily_budget_max_share": 0.12,
                "single_field_daily_budget_max_share": 0.12,
            },
        }
        self._write_json(root / ".local" / "research" / "workflows" / "production.json", payload)
        self._write_json(root / ".local" / "research" / "workflows" / "continuous-alpha" / "deepseek_v4_pro_daily_budget.json", payload)
        self._write_json(root / ".local" / "research" / "workflows" / "continuous-alpha" / "kimi_daily_budget_20260504.json", payload)

    def _research_policy(
        self,
        *,
        daily_limit: int,
        stage_allocations: dict[str, int],
    ) -> dict[str, object]:
        return {
            "version": 1,
            "budget": {
                "daily_simulation_limit": daily_limit,
                "exploration_share_limit": (
                    float(stage_allocations.get("direction_probe") or 0) / float(daily_limit)
                ),
                "exploration_stages": ["direction_probe"],
                "stage_allocations": stage_allocations,
            },
            "behavioral_boundaries": {
                "block_unclassified_candidates": True,
                "require_kill_conditions": True,
                "forbid_pure_price_volume": True,
                "mechanisms": [
                    {
                        "mechanism_id": "reference_point_disposition_drift",
                        "enabled": True,
                        "allowed_proxy_fields": ["anl*", "fundamental_*"],
                        "kill_conditions": ["SELF_CORRELATION", "LOW_FITNESS"],
                    }
                ],
            },
        }

    def _price_volume_only_count(self, candidates: list[dict[str, object]]) -> int:
        price_tokens = ("close", "open", "vwap", "volume", "returns")
        semantic_tokens = ("mdl", "analyst", "fundamental", "news_", "snt_", "implied_volatility", "shortsentiment")
        count = 0
        for row in candidates:
            expression = str(row.get("expression") or "")
            has_price_volume = any(token in expression for token in price_tokens)
            has_semantic_proxy = any(token in expression for token in semantic_tokens)
            if has_price_volume and not has_semantic_proxy:
                count += 1
        return count

    def _live_result(self, alpha_id: str, *, self_corr: float, failed: bool = False) -> dict[str, object]:
        return {
            "alpha_id": alpha_id,
            "status_code": 200,
            "eligible": True,
            "data": {
                "is": {
                    "checks": [
                        {"name": "LOW_SHARPE", "result": "PASS", "value": 1.5},
                        {"name": "LOW_FITNESS", "result": "PASS", "value": 1.1},
                        {"name": "HIGH_TURNOVER", "result": "PASS", "value": 0.2},
                        {"name": "SELF_CORRELATION", "result": "FAIL" if failed else "PASS", "value": self_corr, "limit": 0.7},
                    ]
                }
            },
        }

    def _scan_result_row(
        self,
        alpha_id: str,
        expression: str,
        *,
        sharpe: float,
        fitness: float,
        turnover: float,
        self_corr: float,
        failed: bool = False,
    ) -> dict[str, object]:
        return {
            "alpha_id": alpha_id,
            "expression": expression,
            "settings": {"decay": 6},
            "metrics": {"sharpe": sharpe, "fitness": fitness, "turnover": turnover},
            "checks": [
                {"name": "LOW_SHARPE", "result": "PASS", "value": sharpe},
                {"name": "LOW_FITNESS", "result": "PASS" if fitness >= 1.0 else "FAIL", "value": fitness},
                {"name": "HIGH_TURNOVER", "result": "PASS", "value": turnover},
                {"name": "SELF_CORRELATION", "result": "FAIL" if failed else "PASS", "value": self_corr, "limit": 0.7},
            ],
        }

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
