from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError

from src.llm_provider import (
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMUsage,
    resolve_llm_provider_config,
    validate_structured_content,
)
from src.llm_provider.identity import invalid_llm_config_identity, llm_config_identity
from src.llm_provider.errors import sanitize_url
from src.llm_provider.registry import create_llm_provider


class LLMProviderModelTests(unittest.TestCase):
    def test_request_response_and_usage_are_immutable(self) -> None:
        request = LLMRequest("system", "user", metadata={"trace": "one"})
        usage = LLMUsage(input_tokens=2, output_tokens=3, total_tokens=5)
        response = LLMResponse("ok", "ollama", "qwen", usage)

        with self.assertRaises(FrozenInstanceError):
            request.user_prompt = "changed"
        with self.assertRaises(TypeError):
            request.metadata["trace"] = "changed"
        with self.assertRaises(FrozenInstanceError):
            usage.total_tokens = 6
        with self.assertRaises(FrozenInstanceError):
            response.content = "changed"

    def test_request_validates_prompts_and_generation_settings(self) -> None:
        invalid_requests = (
            {"system_prompt": "", "user_prompt": "user"},
            {"system_prompt": "   ", "user_prompt": "user"},
            {"system_prompt": 123, "user_prompt": "user"},
            {"system_prompt": "system", "user_prompt": "\t"},
            {"system_prompt": "system", "user_prompt": 123},
            {"system_prompt": "system", "user_prompt": "user", "temperature": True},
            {"system_prompt": "system", "user_prompt": "user", "temperature": -0.1},
            {"system_prompt": "system", "user_prompt": "user", "temperature": 2.1},
            {
                "system_prompt": "system",
                "user_prompt": "user",
                "temperature": float("nan"),
            },
            {
                "system_prompt": "system",
                "user_prompt": "user",
                "temperature": float("inf"),
            },
            {"system_prompt": "system", "user_prompt": "user", "max_tokens": True},
            {"system_prompt": "system", "user_prompt": "user", "max_tokens": 0},
            {"system_prompt": "system", "user_prompt": "user", "max_tokens": 1.5},
            {"system_prompt": "system", "user_prompt": "user", "max_tokens": 131073},
            {"system_prompt": "system", "user_prompt": "user", "timeout_seconds": True},
            {"system_prompt": "system", "user_prompt": "user", "timeout_seconds": 0},
            {"system_prompt": "system", "user_prompt": "user", "timeout_seconds": 601},
            {"system_prompt": "system", "user_prompt": "user", "timeout_seconds": 1.5},
            {"system_prompt": "system", "user_prompt": "user", "response_format": "yaml"},
            {"system_prompt": "system", "user_prompt": "user", "response_format": {}},
            {"system_prompt": "system", "user_prompt": "user", "metadata": {1: "value"}},
            {"system_prompt": "system", "user_prompt": "user", "metadata": {"key": 1}},
        )
        for request in invalid_requests:
            with self.subTest(request=request), self.assertRaises(
                LLMProviderError
            ) as raised:
                LLMRequest(**request)
            self.assertEqual("invalid_configuration", raised.exception.code)

        request = LLMRequest(
            "system",
            "user",
            temperature=2,
            max_tokens=1,
            timeout_seconds=600,
            response_format="json",
            metadata={"trace": "one"},
        )
        self.assertEqual(2.0, request.temperature)

    def test_raw_response_is_transitively_immutable_and_detached(self) -> None:
        source = {
            "nested": {
                "items": [{"value": 1}, [2, 3]],
                "enabled": True,
            }
        }
        response = LLMResponse(
            "ok",
            "ollama",
            "qwen",
            LLMUsage(),
            raw_response=source,
        )

        source["nested"]["items"][0]["value"] = 99
        source["nested"]["items"].append(4)

        self.assertEqual(1, response.raw_response["nested"]["items"][0]["value"])
        self.assertEqual(2, len(response.raw_response["nested"]["items"]))
        self.assertIsInstance(response.raw_response["nested"]["items"], tuple)
        with self.assertRaises(TypeError):
            response.raw_response["nested"]["items"][0]["value"] = 2
        with self.assertRaises(TypeError):
            response.raw_response["nested"]["items"][0] = {"value": 2}

    def test_empty_response_is_invalid(self) -> None:
        with self.assertRaises(LLMProviderError) as raised:
            LLMResponse("  ", "anthropic", "claude-test", LLMUsage())

        self.assertEqual("invalid_response", raised.exception.code)

    def test_response_requires_non_empty_provider_and_model(self) -> None:
        for provider, model in (("   ", "model"), ("provider", "\t"), (123, "model")):
            with self.subTest(provider=provider, model=model), self.assertRaises(
                LLMProviderError
            ) as raised:
                LLMResponse("ok", provider, model, LLMUsage())
            self.assertEqual("invalid_response", raised.exception.code)

    def test_usage_accepts_only_nonnegative_integer_counts(self) -> None:
        for field in ("input_tokens", "output_tokens", "total_tokens"):
            for value in (-1, True, 1.5, "1"):
                with self.subTest(field=field, value=value), self.assertRaises(
                    LLMProviderError
                ) as raised:
                    LLMUsage(**{field: value})
                self.assertEqual("invalid_response", raised.exception.code)

        usage = LLMUsage(input_tokens=0, output_tokens=None, total_tokens=0)
        self.assertEqual(0, usage.input_tokens)
        self.assertIsNone(usage.output_tokens)

    def test_response_validates_usage_and_finish_reason_types(self) -> None:
        with self.assertRaises(LLMProviderError) as raised_usage:
            LLMResponse("ok", "provider", "model", {"total_tokens": 1})
        self.assertEqual("invalid_response", raised_usage.exception.code)

        with self.assertRaises(LLMProviderError) as raised_finish:
            LLMResponse(
                "ok",
                "provider",
                "model",
                LLMUsage(),
                finish_reason=123,
            )
        self.assertEqual("invalid_response", raised_finish.exception.code)

    def test_json_response_accepts_only_object_or_array(self) -> None:
        self.assertEqual({"ok": True}, validate_structured_content('{"ok": true}'))
        self.assertEqual([1, 2], validate_structured_content("[1, 2]"))

        for content in ("null", '"text"', "1", "true"):
            with self.subTest(content=content), self.assertRaises(LLMProviderError) as raised:
                validate_structured_content(content)
            self.assertEqual("invalid_structured_output", raised.exception.code)

    def test_json_response_rejects_markdown_fences_and_invalid_json(self) -> None:
        for content in ("```json\n{}\n```", "{not-json}", ""):
            with self.subTest(content=content), self.assertRaises(LLMProviderError) as raised:
                validate_structured_content(content)
            self.assertEqual("invalid_structured_output", raised.exception.code)


class LLMProviderErrorTests(unittest.TestCase):
    def test_exception_serialization_redacts_known_secrets_recursively(self) -> None:
        error = LLMProviderError(
            code="provider_error",
            message="request with secret-value failed",
            provider="anthropic",
            model="claude-test",
            retryable=True,
            status_code=500,
            details={
                "authorization": "Bearer secret-value",
                "nested": [
                    "secret-value",
                    {
                        "token": "prefix-secret-value-suffix",
                        "key-secret-value": {"secret-value": "nested"},
                    },
                ],
            },
            secrets=("secret-value",),
        )

        serialized = error.to_dict()
        rendered = json.dumps(serialized)
        self.assertNotIn("secret-value", rendered)
        self.assertEqual("provider_error", serialized["code"])
        self.assertTrue(serialized["retryable"])
        self.assertEqual(500, serialized["status_code"])
        self.assertNotIn("secret-value", str(error))

    def test_overlapping_secrets_are_unique_and_redacted_longest_first(self) -> None:
        error = LLMProviderError(
            code="provider_error",
            message="token-extended failed",
            details={"token-extended": "token-extended"},
            secrets=("token", "token-extended", "token", ""),
        )

        self.assertEqual("<redacted> failed", error.message)
        self.assertEqual({"<redacted>": "<redacted>"}, error.details)

    def test_final_error_payload_redacts_provider_and_model_fields(self) -> None:
        error = LLMProviderError(
            code="provider_error",
            message="request failed",
            provider="provider-secret-value",
            model="model-secret-value",
            details={"safe": "value"},
            secrets=("secret-value",),
        )

        rendered = json.dumps(error.to_dict())
        self.assertNotIn("secret-value", rendered)
        self.assertEqual("provider-<redacted>", error.to_dict()["provider"])
        self.assertEqual("model-<redacted>", error.to_dict()["model"])


class CanonicalLLMProviderConfigTests(unittest.TestCase):
    def test_sanitize_url_fails_closed_and_removes_fragments(self) -> None:
        invalid_urls = (
            "https://user:password@[broken-host/v1?token=secret",
            "https://user:password@example.invalid:not-a-port/v1?token=secret",
            "https://user:password@/v1?token=secret",
            "https://example.invalid/v1%ZZ?token=secret",
        )
        for url in invalid_urls:
            with self.subTest(url=url):
                sanitized = sanitize_url(url)
                self.assertEqual("<redacted-invalid-url>", sanitized)
                self.assertNotIn("user", sanitized)
                self.assertNotIn("password", sanitized)
                self.assertNotIn("secret", sanitized)

        fragmented = sanitize_url(
            "https://user:password@example.invalid/v1"
            "?mode=fast&access_token=query-secret"
            "#access_token=fragment-secret&key=fragment-key&password=fragment-password"
        )
        self.assertEqual(
            "https://<redacted>:<redacted>@example.invalid/v1"
            "?mode=fast&access_token=<redacted>",
            fragmented,
        )
        self.assertNotIn("#", fragmented)
        self.assertNotIn("fragment", fragmented)

    def test_redacted_config_sanitizes_url_userinfo_and_sensitive_query_values(self) -> None:
        resolved = resolve_llm_provider_config(
            {
                "llm_provider": {
                    "provider": "openai_compatible",
                    "model": "url-model",
                    "api_key_env": "URL_API_KEY",
                    "base_url": (
                        "https://configured-user:configured-pass@example.invalid/v1"
                        "?api_key=configured-key&mode=fast&signature=configured-signature"
                    ),
                    "base_url_env": "URL_BASE_URL",
                }
            },
            env={
                "URL_API_KEY": "known-secret",
                "URL_BASE_URL": (
                    "https://effective-user:effective-pass@example.invalid/v2"
                    "?access_token=effective-token&region=us&auth=effective-auth"
                ),
            },
            require_credentials=False,
        )

        payload = resolved.to_redacted_dict()
        configured_url = payload["config"]["base_url"]
        effective_url = payload["effective_base_url"]
        rendered = json.dumps(payload)
        for secret in (
            "configured-user",
            "configured-pass",
            "configured-key",
            "configured-signature",
            "effective-user",
            "effective-pass",
            "effective-token",
            "effective-auth",
            "known-secret",
        ):
            self.assertNotIn(secret, rendered)
        self.assertIn("https://<redacted>:<redacted>@example.invalid/v1", configured_url)
        self.assertIn("mode=fast", configured_url)
        self.assertIn("api_key=<redacted>", configured_url)
        self.assertIn("signature=<redacted>", configured_url)
        self.assertIn("https://<redacted>:<redacted>@example.invalid/v2", effective_url)
        self.assertIn("region=us", effective_url)
        self.assertIn("access_token=<redacted>", effective_url)
        self.assertIn("auth=<redacted>", effective_url)

    def test_effective_identity_digest_is_credential_independent(self) -> None:
        config = {
            "llm_provider": {
                "provider": "openai_compatible",
                "model": "identity-model",
                "api_key_env": "IDENTITY_API_KEY",
                "base_url": "https://llm.invalid/v1",
            }
        }
        missing = resolve_llm_provider_config(
            config,
            env={},
            require_credentials=False,
        )
        present = resolve_llm_provider_config(
            config,
            env={"IDENTITY_API_KEY": "first-secret"},
            require_credentials=False,
        )
        changed = resolve_llm_provider_config(
            config,
            env={"IDENTITY_API_KEY": "second-secret"},
            require_credentials=False,
        )

        digests = {
            llm_config_identity(resolved)["config_digest"]
            for resolved in (missing, present, changed)
        }
        self.assertEqual(1, len(digests))
        self.assertFalse(missing.to_redacted_dict()["credential_configured"])
        self.assertTrue(present.to_redacted_dict()["credential_configured"])
        with self.assertRaises(LLMProviderError) as raised:
            create_llm_provider(missing)
        self.assertEqual("invalid_configuration", raised.exception.code)
        self.assertIsNotNone(create_llm_provider(present))

    def test_effective_identity_uses_sanitized_urls_for_credential_rotation(self) -> None:
        digests: list[str] = []
        redacted_urls: list[tuple[str, str]] = []
        for suffix in ("one", "two"):
            config = {
                "llm_provider": {
                    "provider": "openai_compatible",
                    "model": "identity-model",
                    "api_key_env": "IDENTITY_API_KEY",
                    "base_url": (
                        f"https://configured-{suffix}:password-{suffix}@llm.invalid/v1"
                        f"?token=configured-token-{suffix}&mode=fast"
                        f"#key=configured-fragment-{suffix}"
                    ),
                    "base_url_env": "IDENTITY_BASE_URL",
                }
            }
            resolved = resolve_llm_provider_config(
                config,
                env={
                    "IDENTITY_API_KEY": f"api-key-{suffix}",
                    "IDENTITY_BASE_URL": (
                        f"https://effective-{suffix}:secret-{suffix}@llm.invalid/v2"
                        f"?signature=effective-signature-{suffix}&region=us"
                        f"#access_token=effective-fragment-{suffix}"
                    ),
                },
                require_credentials=False,
            )
            digests.append(llm_config_identity(resolved)["config_digest"])
            redacted = resolved.to_redacted_dict()
            redacted_urls.append(
                (redacted["config"]["base_url"], redacted["effective_base_url"])
            )

        self.assertEqual(digests[0], digests[1])
        self.assertEqual(redacted_urls[0], redacted_urls[1])
        self.assertEqual(
            (
                "https://<redacted>:<redacted>@llm.invalid/v1?token=<redacted>&mode=fast",
                "https://<redacted>:<redacted>@llm.invalid/v2"
                "?signature=<redacted>&region=us",
            ),
            redacted_urls[0],
        )

    def test_invalid_config_identity_is_collision_resistant_and_redacts_sensitive_values(self) -> None:
        first_error = LLMProviderError(
            code="invalid_configuration",
            message="invalid",
            details={"path": "$.llm_provider.provider"},
        )
        second_error = LLMProviderError(
            code="invalid_configuration",
            message="invalid",
            details={"path": "$.llm_provider.model"},
        )
        first = invalid_llm_config_identity(
            {
                "llm_provider": {
                    "provider": "unknown-a",
                    "api_key": "secret-one",
                    "nested": {"access_token": "secret-two"},
                }
            },
            first_error,
        )
        second = invalid_llm_config_identity(
            {"llm_provider": {"provider": "unknown-b", "api_key": "secret-three"}},
            second_error,
        )

        self.assertNotEqual(first["config_digest"], second["config_digest"])
        serialized = json.dumps(first, sort_keys=True)
        self.assertNotIn("secret-one", serialized)
        self.assertNotIn("secret-two", serialized)
        self.assertNotIn("redacted_config", first)
        self.assertEqual("$.llm_provider.provider", first["error_path"])

    def test_invalid_identity_and_diagnostic_never_expose_embedded_credentials(self) -> None:
        secrets = (
            "query-secret",
            "model-secret",
            "cli-secret",
            "arbitrary-secret",
            "literal-secret",
        )
        config = {
            "llm_provider": {
                "provider": "cli",
                "model": "model-secret",
                "api_key": "literal-secret",
                "base_url": "https://example.invalid/v1?token=query-secret",
                "command": ["tool", "--credential", "cli-secret"],
                "arbitrary": {"anything": "arbitrary-secret"},
            }
        }
        error = LLMProviderError(
            code="invalid_configuration",
            message="invalid model-secret query-secret cli-secret",
            details={"path": "$.llm_provider", "raw": "arbitrary-secret"},
        )

        identity = invalid_llm_config_identity(config, error)

        self.assertEqual("cli", identity["provider"])
        self.assertEqual("", identity["model"])
        self.assertNotIn("redacted_config", identity)
        serialized = json.dumps(identity, sort_keys=True)
        for secret in secrets:
            self.assertNotIn(secret, serialized)
    def test_canonical_anthropic_config_resolves_without_exposing_secret(self) -> None:
        resolved = resolve_llm_provider_config(
            {
                "llm_provider": {
                    "provider": "anthropic",
                    "display_name": "Research model",
                    "model": "claude-test",
                    "api_key_env": "ANTHROPIC_API_KEY",
                }
            },
            env={"ANTHROPIC_API_KEY": "secret-value"},
        )

        self.assertEqual("secret-value", resolved.api_key)
        self.assertEqual("anthropic", resolved.config.provider)
        self.assertEqual("Research model", resolved.config.display_name)
        self.assertNotIn("secret-value", json.dumps(resolved.to_redacted_dict()))
        self.assertNotIn("secret-value", repr(resolved))
        self.assertEqual((), resolved.warnings)

    def test_base_url_env_overrides_literal_base_url(self) -> None:
        resolved = resolve_llm_provider_config(
            {
                "llm_provider": {
                    "provider": "openai_compatible",
                    "model": "test",
                    "api_key_env": "KEY",
                    "base_url": "https://configured.invalid/v1",
                    "base_url_env": "MODEL_BASE_URL",
                }
            },
            env={"KEY": "secret", "MODEL_BASE_URL": "http://127.0.0.1:9000/v1"},
        )

        self.assertEqual("http://127.0.0.1:9000/v1", resolved.base_url)
        self.assertEqual("https://configured.invalid/v1", resolved.config.base_url)

    def test_redacted_config_removes_secret_from_configured_and_effective_urls(self) -> None:
        resolved = resolve_llm_provider_config(
            {
                "llm_provider": {
                    "provider": "openai_compatible",
                    "model": "test",
                    "api_key_env": "KEY",
                    "base_url": "https://api-key-value@configured.example/v1",
                    "base_url_env": "MODEL_BASE_URL",
                }
            },
            env={
                "KEY": "api-key-value",
                "MODEL_BASE_URL": "https://effective.example/api-key-value/v1",
            },
        )

        rendered = json.dumps(resolved.to_redacted_dict())
        self.assertNotIn("api-key-value", rendered)
        self.assertIn("<redacted>", rendered)

    def test_rejects_literal_api_key(self) -> None:
        with self.assertRaises(LLMProviderError) as raised:
            resolve_llm_provider_config(
                {
                    "llm_provider": {
                        "provider": "anthropic",
                        "model": "x",
                        "api_key": "secret",
                    }
                },
                env={},
            )

        self.assertEqual("invalid_configuration", raised.exception.code)
        self.assertNotIn("secret", json.dumps(raised.exception.to_dict()))

    def test_disabled_mode_needs_no_model_or_credential(self) -> None:
        resolved = resolve_llm_provider_config(
            {"llm_provider": {"provider": "disabled"}}, env={}
        )

        self.assertEqual("disabled", resolved.config.provider)
        self.assertEqual("", resolved.config.model)
        self.assertIsNone(resolved.api_key)

    def test_ollama_uses_loopback_default_without_credential(self) -> None:
        resolved = resolve_llm_provider_config(
            {"llm_provider": {"provider": "ollama", "model": "qwen-local"}}, env={}
        )

        self.assertEqual("http://127.0.0.1:11434", resolved.base_url)
        self.assertIsNone(resolved.api_key)

    def test_unknown_provider_is_rejected(self) -> None:
        with self.assertRaises(LLMProviderError) as raised:
            resolve_llm_provider_config(
                {"llm_provider": {"provider": "unknown", "model": "x"}}, env={}
            )

        self.assertEqual("invalid_configuration", raised.exception.code)

    def test_canonical_empty_provider_is_rejected(self) -> None:
        with self.assertRaises(LLMProviderError) as raised:
            resolve_llm_provider_config(
                {"llm_provider": {"provider": ""}},
                env={},
            )

        self.assertEqual("invalid_configuration", raised.exception.code)

    def test_canonical_block_requires_explicit_provider(self) -> None:
        with self.assertRaises(LLMProviderError) as raised:
            resolve_llm_provider_config(
                {"llm_provider": {}},
                env={},
            )

        self.assertEqual("invalid_configuration", raised.exception.code)

    def test_network_provider_requires_model_and_available_credential(self) -> None:
        invalid_configs = (
            {"provider": "anthropic", "api_key_env": "KEY"},
            {"provider": "gemini", "model": "gemini-test", "api_key_env": "KEY"},
            {"provider": "openai_compatible", "model": "gpt-test"},
            {"provider": "ollama"},
        )
        for provider_config in invalid_configs:
            with self.subTest(provider_config=provider_config), self.assertRaises(
                LLMProviderError
            ) as raised:
                resolve_llm_provider_config({"llm_provider": provider_config}, env={})
            self.assertEqual("invalid_configuration", raised.exception.code)

    def test_optional_credential_resolution_captures_present_key_without_requiring_it(self) -> None:
        for provider, model, key_env in (
            ("openai_compatible", "gpt-test", "OPENAI_API_KEY"),
            ("anthropic", "claude-test", "ANTHROPIC_API_KEY"),
            ("gemini", "gemini-test", "GEMINI_API_KEY"),
        ):
            with self.subTest(provider=provider):
                resolved = resolve_llm_provider_config(
                    {
                        "llm_provider": {
                            "provider": provider,
                            "model": model,
                            "api_key_env": key_env,
                        }
                    },
                    env={key_env: "must-not-be-resolved"},
                    require_credentials=False,
                )

                self.assertEqual(provider, resolved.config.provider)
                self.assertEqual(model, resolved.config.model)
                self.assertEqual("must-not-be-resolved", resolved.api_key)
                redacted = resolved.to_redacted_dict()
                self.assertTrue(redacted["credential_configured"])
                self.assertNotIn("must-not-be-resolved", json.dumps(redacted))

                missing = resolve_llm_provider_config(
                    {
                        "llm_provider": {
                            "provider": provider,
                            "model": model,
                            "api_key_env": key_env,
                        }
                    },
                    env={},
                    require_credentials=False,
                )
                self.assertIsNone(missing.api_key)

    def test_numeric_bounds_and_response_format_are_enforced(self) -> None:
        invalid_values = (
            ("timeout_seconds", 0),
            ("timeout_seconds", 601),
            ("temperature", -0.01),
            ("temperature", 2.01),
            ("max_tokens", 0),
            ("max_tokens", 131073),
            ("response_format", "yaml"),
        )
        for field, value in invalid_values:
            with self.subTest(field=field, value=value), self.assertRaises(
                LLMProviderError
            ) as raised:
                resolve_llm_provider_config(
                    {
                        "llm_provider": {
                            "provider": "ollama",
                            "model": "qwen-local",
                            field: value,
                        }
                    },
                    env={},
                )
            self.assertEqual("invalid_configuration", raised.exception.code)

    def test_non_finite_temperature_is_rejected(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaises(
                LLMProviderError
            ) as raised:
                resolve_llm_provider_config(
                    {
                        "llm_provider": {
                            "provider": "ollama",
                            "model": "qwen-local",
                            "temperature": value,
                        }
                    },
                    env={},
                )
            self.assertEqual("invalid_configuration", raised.exception.code)

    def test_unhashable_response_format_is_rejected_as_configuration_error(self) -> None:
        for value in ({}, [], {"type": []}):
            with self.subTest(value=value), self.assertRaises(
                LLMProviderError
            ) as raised:
                resolve_llm_provider_config(
                    {
                        "llm_provider": {
                            "provider": "ollama",
                            "model": "qwen-local",
                            "response_format": value,
                        }
                    },
                    env={},
                )
            self.assertEqual("invalid_configuration", raised.exception.code)

    def test_canonical_response_format_rejects_legacy_object_form(self) -> None:
        with self.assertRaises(LLMProviderError) as raised:
            resolve_llm_provider_config(
                {
                    "llm_provider": {
                        "provider": "ollama",
                        "model": "qwen-local",
                        "response_format": {"type": "json_object"},
                    }
                },
                env={},
            )

        self.assertEqual("invalid_configuration", raised.exception.code)

    def test_integer_fields_reject_booleans_and_fractional_numbers(self) -> None:
        invalid_values = (
            ("timeout_seconds", True),
            ("timeout_seconds", 10.5),
            ("max_tokens", False),
            ("max_tokens", 4096.5),
        )
        for field, value in invalid_values:
            with self.subTest(field=field, value=value), self.assertRaises(
                LLMProviderError
            ) as raised:
                resolve_llm_provider_config(
                    {
                        "llm_provider": {
                            "provider": "ollama",
                            "model": "qwen-local",
                            field: value,
                        }
                    },
                    env={},
                )
            self.assertEqual("invalid_configuration", raised.exception.code)

    def test_string_contract_fields_reject_non_string_values(self) -> None:
        invalid_configs = (
            ({"provider": 123, "model": "test"}, {}),
            ({"provider": "ollama", "model": 123}, {}),
            (
                {
                    "provider": "openai_compatible",
                    "model": "test",
                    "api_key_env": 123,
                },
                {"123": "key"},
            ),
            ({"provider": "ollama", "model": "test", "base_url": 123}, {}),
            (
                {
                    "provider": "ollama",
                    "model": "test",
                    "base_url_env": 123,
                },
                {"123": "http://127.0.0.1:9000"},
            ),
            ({"provider": "ollama", "model": "test", "display_name": 123}, {}),
            (
                {
                    "provider": "cli",
                    "command": ["model-cli", "{prompt}"],
                    "prompt_transport": 123,
                },
                {},
            ),
            (
                {
                    "provider": "cli",
                    "command": ["model-cli", "{prompt}"],
                    "working_directory": 123,
                },
                {},
            ),
        )
        for provider_config, env in invalid_configs:
            with self.subTest(provider_config=provider_config), self.assertRaises(
                LLMProviderError
            ) as raised:
                resolve_llm_provider_config({"llm_provider": provider_config}, env=env)
            self.assertEqual("invalid_configuration", raised.exception.code)

    def test_required_strings_and_credentials_reject_whitespace_only_values(self) -> None:
        invalid_cases = (
            ({"provider": "ollama", "model": "   "}, {}),
            (
                {
                    "provider": "openai_compatible",
                    "model": "test",
                    "api_key_env": "   ",
                },
                {"   ": "key"},
            ),
            (
                {
                    "provider": "openai_compatible",
                    "model": "test",
                    "api_key_env": "KEY",
                },
                {"KEY": "   "},
            ),
            ({"provider": "ollama", "model": "test", "base_url": "   "}, {}),
            (
                {
                    "provider": "cli",
                    "command": ["model-cli", "   ", "{prompt}"],
                },
                {},
            ),
        )
        for provider_config, env in invalid_cases:
            with self.subTest(provider_config=provider_config), self.assertRaises(
                LLMProviderError
            ) as raised:
                resolve_llm_provider_config({"llm_provider": provider_config}, env=env)
            self.assertEqual("invalid_configuration", raised.exception.code)

    def test_cli_validates_command_and_argument_transport(self) -> None:
        invalid_configs = (
            {"provider": "cli", "command": []},
            {"provider": "cli", "command": "model-cli --prompt {prompt}"},
            {
                "provider": "cli",
                "command": ["model-cli"],
                "prompt_transport": "argument",
            },
            {
                "provider": "cli",
                "command": ["model-cli"],
                "prompt_transport": "pipe",
            },
        )
        for provider_config in invalid_configs:
            with self.subTest(provider_config=provider_config), self.assertRaises(
                LLMProviderError
            ) as raised:
                resolve_llm_provider_config({"llm_provider": provider_config}, env={})
            self.assertEqual("invalid_configuration", raised.exception.code)

    def test_cli_rejects_unknown_and_malformed_placeholders_offline(self) -> None:
        invalid_commands = (
            ["model-cli", "{prompt}", "{unknown}"],
            ["model-cli", "{prompt}", "broken{"],
            ["model-cli", "{prompt!r}"],
            ["model-cli", "{prompt:>10}"],
        )
        for command in invalid_commands:
            with self.subTest(command=command):
                with self.assertRaises(LLMProviderError) as raised:
                    resolve_llm_provider_config(
                        {
                            "llm_provider": {
                                "provider": "cli",
                                "command": command,
                                "prompt_transport": "argument",
                            }
                        },
                        env={},
                    )
                self.assertEqual("invalid_configuration", raised.exception.code)

    def test_cli_accepts_every_documented_placeholder_offline(self) -> None:
        command = [
            "model-cli",
            "{system_prompt}",
            "{prompt}",
            "{model}",
            "{workspace_root}",
        ]

        resolved = resolve_llm_provider_config(
            {
                "llm_provider": {
                    "provider": "cli",
                    "model": "local-model",
                    "command": command,
                    "prompt_transport": "argument",
                }
            },
            env={},
        )

        self.assertEqual(tuple(command), resolved.config.command)

    def test_cli_rejects_batch_and_dynamic_executable_offline(self) -> None:
        invalid_commands = (
            ["runner.cmd", "{prompt}"],
            ["RUNNER.BAT", "{prompt}"],
            ["{workspace_root}\\model.exe", "{prompt}"],
            ["{model}", "{prompt}"],
        )
        for command in invalid_commands:
            with self.subTest(command=command):
                with self.assertRaises(LLMProviderError) as raised:
                    resolve_llm_provider_config(
                        {
                            "llm_provider": {
                                "provider": "cli",
                                "command": command,
                                "prompt_transport": "argument",
                            }
                        },
                        env={},
                    )
                self.assertEqual("invalid_configuration", raised.exception.code)


class LLMProviderConfigMigrationTests(unittest.TestCase):
    def test_removed_legacy_keys_fail_with_canonical_migration_instruction(self) -> None:
        for legacy_key in ("llm_adapter", "deepseek_v4_pro", "kimi_cli"):
            with self.subTest(legacy_key=legacy_key), self.assertRaises(
                LLMProviderError
            ) as raised:
                resolve_llm_provider_config(
                    {legacy_key: {}, "llm_provider": {"provider": "disabled"}},
                    env={},
                )

            self.assertEqual("invalid_configuration", raised.exception.code)
            self.assertIn(legacy_key, raised.exception.message)
            self.assertIn("llm_provider", raised.exception.message)

    def test_undeclared_provider_environment_does_not_enable_llm(self) -> None:
        resolved = resolve_llm_provider_config(
            {},
            env={
                "KIMI_API_KEY": "ignored",
                "MOONSHOT_API_KEY": "ignored",
                "KIMI_BASE_URL": "https://ignored.example/v1",
                "KIMI_MODEL": "ignored",
            },
        )

        self.assertEqual("disabled", resolved.config.provider)
        self.assertIsNone(resolved.api_key)
        self.assertEqual((), resolved.warnings)

    def test_no_configuration_falls_back_to_disabled(self) -> None:
        resolved = resolve_llm_provider_config({}, env={})

        self.assertEqual("disabled", resolved.config.provider)
        self.assertEqual((), resolved.warnings)


class LLMProviderRegistryTests(unittest.TestCase):
    def test_registry_builds_cli_provider_with_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            resolved = resolve_llm_provider_config(
                {
                    "llm_provider": {
                        "provider": "cli",
                        "model": "local-model",
                        "command": [sys.executable, "-c", "print('ok')", "{prompt}"],
                        "prompt_transport": "argument",
                    }
                },
                env={},
            )

            provider = create_llm_provider(resolved, workspace_root=directory)

        self.assertIsNotNone(provider)
        self.assertEqual("cli", provider.provider_id)
        self.assertEqual("local-model", provider.model)

    def test_registry_builds_openai_compatible_provider(self) -> None:
        resolved = resolve_llm_provider_config(
            {
                "llm_provider": {
                    "provider": "openai_compatible",
                    "model": "test-model",
                    "api_key_env": "KEY",
                    "base_url": "http://127.0.0.1:9999/v1",
                }
            },
            env={"KEY": "secret"},
        )

        provider = create_llm_provider(resolved)

        self.assertIsNotNone(provider)
        self.assertEqual("openai_compatible", provider.provider_id)
        self.assertEqual("test-model", provider.model)

    def test_registry_returns_none_for_disabled_provider(self) -> None:
        resolved = resolve_llm_provider_config(
            {"llm_provider": {"provider": "disabled"}}, env={}
        )

        self.assertIsNone(create_llm_provider(resolved))

    def test_registry_builds_native_http_providers(self) -> None:
        cases = (
            ("anthropic", {"KEY": "secret"}),
            ("gemini", {"KEY": "secret"}),
            ("ollama", {}),
        )
        for provider_id, env in cases:
            settings = {"provider": provider_id, "model": "test-model"}
            if env:
                settings["api_key_env"] = "KEY"
            with self.subTest(provider=provider_id):
                resolved = resolve_llm_provider_config(
                    {"llm_provider": settings}, env=env
                )
                provider = create_llm_provider(resolved)
            self.assertIsNotNone(provider)
            self.assertEqual(provider_id, provider.provider_id)
            self.assertEqual("test-model", provider.model)


if __name__ == "__main__":
    unittest.main()
