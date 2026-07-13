from __future__ import annotations

import json
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator
from unittest.mock import MagicMock, patch

import requests

from src.llm_provider import LLMProviderError, LLMRequest
from src.llm_provider.providers.anthropic import AnthropicProvider
from src.llm_provider.providers.common import (
    require_nonempty_string,
    require_positive_timeout,
)
from src.llm_provider.providers.gemini import GeminiProvider
from src.llm_provider.providers.ollama import OllamaProvider
from src.llm_provider.providers.openai_compatible import OpenAICompatibleProvider


class _RecordingServer(ThreadingHTTPServer):
    response_status: int
    response_body: bytes
    response_delay: float
    request_count: int
    request_headers: dict[str, str]
    request_json: Any
    request_path: str


class _Handler(BaseHTTPRequestHandler):
    server: _RecordingServer

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.request_count += 1
        self.server.request_headers = dict(self.headers.items())
        self.server.request_json = json.loads(body)
        self.server.request_path = self.path
        if self.server.response_delay:
            time.sleep(self.server.response_delay)
        self.send_response(self.server.response_status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        try:
            self.wfile.write(self.server.response_body)
        except OSError:
            pass

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def serve_json(
    response: Any,
    *,
    status: int = 200,
    delay: float = 0,
    raw_body: bytes | None = None,
) -> Iterator[_RecordingServer]:
    server = _RecordingServer(("127.0.0.1", 0), _Handler)
    server.response_status = status
    server.response_body = raw_body if raw_body is not None else json.dumps(response).encode()
    server.response_delay = delay
    server.request_count = 0
    server.request_headers = {}
    server.request_json = None
    server.request_path = ""
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.url = f"http://127.0.0.1:{server.server_port}/v1"  # type: ignore[attr-defined]
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _success(content: str = '{"directions": []}') -> dict[str, Any]:
    return {
        "choices": [
            {"message": {"content": content}, "finish_reason": "stop"}
        ],
        "usage": {
            "prompt_tokens": 7,
            "completion_tokens": 3,
            "total_tokens": 10,
        },
    }


def _mock_response(payload: Any, *, status: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    response.text = json.dumps(payload)
    return response


class OpenAICompatibleProviderTests(unittest.TestCase):
    def test_maps_request_response_and_usage_exactly(self) -> None:
        with serve_json(_success()) as server:
            provider = OpenAICompatibleProvider(
                model="test-model", api_key="secret", base_url=server.url
            )
            result = provider.complete(
                LLMRequest(
                    "system",
                    "user",
                    temperature=0.4,
                    max_tokens=123,
                    response_format="json",
                    metadata={"trace": "local-only"},
                )
            )

        self.assertEqual("/v1/chat/completions", server.request_path)
        self.assertEqual("Bearer secret", server.request_headers["Authorization"])
        self.assertEqual(
            {
                "model": "test-model",
                "messages": [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "user"},
                ],
                "temperature": 0.4,
                "max_tokens": 123,
                "stream": False,
                "response_format": {"type": "json_object"},
            },
            server.request_json,
        )
        self.assertEqual(1, server.request_count)
        self.assertEqual('{"directions":[]}', result.content)
        self.assertEqual("openai_compatible", result.provider)
        self.assertEqual("test-model", result.model)
        self.assertEqual("stop", result.finish_reason)
        self.assertEqual(7, result.usage.input_tokens)
        self.assertEqual(3, result.usage.output_tokens)
        self.assertEqual(10, result.usage.total_tokens)

    def test_text_request_omits_response_format(self) -> None:
        with serve_json(_success("plain text")) as server:
            provider = OpenAICompatibleProvider(
                model="test-model", api_key="secret", base_url=server.url
            )
            result = provider.complete(LLMRequest("system", "user"))

        self.assertNotIn("response_format", server.request_json)
        self.assertEqual("plain text", result.content)

    def test_success_response_redacts_api_key_from_content_and_nested_raw_fields(
        self,
    ) -> None:
        api_key = "test-success-secret-token"
        response = _success(f"analysis before {api_key} analysis after")
        response["provider_metadata"] = {
            "nested": [{"echo": f"metadata before {api_key} metadata after"}],
            "preserved": "ordinary value",
        }
        with serve_json(response) as server:
            provider = OpenAICompatibleProvider(
                model="test-model", api_key=api_key, base_url=server.url
            )
            result = provider.complete(LLMRequest("system", "user"))

        self.assertEqual(
            "analysis before <redacted> analysis after", result.content
        )
        self.assertEqual(
            "metadata before <redacted> metadata after",
            result.raw_response["provider_metadata"]["nested"][0]["echo"],
        )
        self.assertEqual(
            "ordinary value",
            result.raw_response["provider_metadata"]["preserved"],
        )
        self.assertNotIn(api_key, repr(result.raw_response))

    def test_json_success_redacts_api_key_while_preserving_valid_structure(
        self,
    ) -> None:
        api_key = "test-json-secret-token"
        response = _success(
            json.dumps(
                {
                    "credential": api_key,
                    "summary": f"keep before {api_key} keep after",
                    "count": 2,
                }
            )
        )
        response["provider_metadata"] = {
            "nested": {"credential": api_key, "enabled": True}
        }
        with serve_json(response) as server:
            provider = OpenAICompatibleProvider(
                model="test-model", api_key=api_key, base_url=server.url
            )
            result = provider.complete(
                LLMRequest("system", "user", response_format="json")
            )

        self.assertEqual(
            {
                "credential": "<redacted>",
                "summary": "keep before <redacted> keep after",
                "count": 2,
            },
            json.loads(result.content),
        )
        self.assertEqual(
            "<redacted>",
            result.raw_response["provider_metadata"]["nested"]["credential"],
        )
        self.assertTrue(
            result.raw_response["provider_metadata"]["nested"]["enabled"]
        )
        self.assertNotIn(api_key, result.content)
        self.assertNotIn(api_key, repr(result.raw_response))

    def test_json_success_redacts_quoted_and_escaped_credentials_structurally(
        self,
    ) -> None:
        for api_key in ('quote"credential', "backslash\\credential"):
            with self.subTest(api_key=api_key):
                content = json.dumps(
                    {
                        api_key: "test-secret-key-name",
                        "credential": api_key,
                        "sentence": f"before {api_key} after",
                        "preserved": {"count": 3, "enabled": True},
                    }
                )
                response = _success(content)
                with serve_json(response) as server:
                    provider = OpenAICompatibleProvider(
                        model="test-model", api_key=api_key, base_url=server.url
                    )
                    result = provider.complete(
                        LLMRequest("system", "user", response_format="json")
                    )

                parsed = json.loads(result.content)
                self.assertNotIn(api_key, json.dumps(parsed, ensure_ascii=False))
                self.assertEqual("test-secret-key-name", parsed["<redacted>"])
                self.assertEqual("<redacted>", parsed["credential"])
                self.assertEqual("before <redacted> after", parsed["sentence"])
                self.assertEqual(
                    {"count": 3, "enabled": True}, parsed["preserved"]
                )
                self.assertEqual(
                    result.content,
                    result.raw_response["choices"][0]["message"]["content"],
                )
                self.assertNotIn(api_key, repr(result.raw_response))

    def test_json_success_sanitizes_every_retained_choice_content(self) -> None:
        api_key = 'quote"and\\backslash-credential'
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"selected": "first", "preserved": 7}
                        )
                    },
                    "finish_reason": "stop",
                },
                {
                    "message": {
                        "content": json.dumps(
                            {
                                api_key: "test-secret-key-name",
                                "credential": api_key,
                                "sentence": f"before {api_key} after",
                            }
                        )
                    },
                    "finish_reason": "stop",
                },
            ],
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 8,
                "total_tokens": 13,
            },
        }
        with serve_json(response) as server:
            provider = OpenAICompatibleProvider(
                model="test-model", api_key=api_key, base_url=server.url
            )
            result = provider.complete(
                LLMRequest("system", "user", response_format="json")
            )

        self.assertEqual(
            {"preserved": 7, "selected": "first"}, json.loads(result.content)
        )
        self.assertEqual(
            result.content,
            result.raw_response["choices"][0]["message"]["content"],
        )
        for choice in result.raw_response["choices"]:
            parsed_content = json.loads(choice["message"]["content"])
            self.assertNotIn(
                api_key,
                json.dumps(parsed_content, ensure_ascii=False),
            )
        later = json.loads(
            result.raw_response["choices"][1]["message"]["content"]
        )
        self.assertEqual("test-secret-key-name", later["<redacted>"])
        self.assertEqual("<redacted>", later["credential"])
        self.assertEqual("before <redacted> after", later["sentence"])

    def test_internal_session_is_closed_after_success(self) -> None:
        session = MagicMock()
        session.post.return_value = _mock_response(_success("plain text"))
        with patch(
            "src.llm_provider.providers.http.requests.Session",
            return_value=session,
        ):
            provider = OpenAICompatibleProvider(
                model="test-model",
                api_key="secret",
                base_url="http://127.0.0.1:1/v1",
            )
            result = provider.complete(LLMRequest("system", "user"))

        self.assertEqual("plain text", result.content)
        session.close.assert_called_once_with()

    def test_internal_session_is_closed_on_every_http_boundary_exception(self) -> None:
        cases = (
            ("timeout", requests.Timeout("timeout"), None),
            ("connection", requests.ConnectionError("refused"), None),
            ("request", requests.RequestException("failed"), None),
            ("http_status", None, _mock_response({"error": "failed"}, status=500)),
            ("invalid_json", None, _mock_response(None)),
        )
        cases[-1][2].json.side_effect = ValueError("invalid JSON")
        for name, side_effect, response in cases:
            with self.subTest(name=name):
                session = MagicMock()
                session.post.side_effect = side_effect
                if response is not None:
                    session.post.return_value = response
                with patch(
                    "src.llm_provider.providers.http.requests.Session",
                    return_value=session,
                ):
                    provider = OpenAICompatibleProvider(
                        model="test-model",
                        api_key="secret",
                        base_url="http://127.0.0.1:1/v1",
                    )
                    with self.assertRaises(LLMProviderError):
                        provider.complete(LLMRequest("system", "user"))
                session.close.assert_called_once_with()

    def test_injected_session_remains_caller_owned_on_success_and_failure(self) -> None:
        session = MagicMock()
        session.post.return_value = _mock_response(_success("plain text"))
        provider = OpenAICompatibleProvider(
            model="test-model",
            api_key="secret",
            base_url="http://127.0.0.1:1/v1",
            session=session,
        )

        self.assertEqual(
            "plain text", provider.complete(LLMRequest("system", "user")).content
        )
        session.close.assert_not_called()

        session.post.side_effect = requests.Timeout("timeout")
        with self.assertRaises(LLMProviderError):
            provider.complete(LLMRequest("system", "user"))
        session.close.assert_not_called()

    def test_request_timeout_overrides_provider_timeout(self) -> None:
        with serve_json(_success(), delay=0.15) as server:
            provider = OpenAICompatibleProvider(
                model="test-model",
                api_key="secret",
                base_url=server.url,
                timeout_seconds=0.05,
            )
            result = provider.complete(
                LLMRequest("system", "user", timeout_seconds=1)
            )

        self.assertEqual('{"directions": []}', result.content)
        self.assertEqual(1, server.request_count)

    def test_401_and_403_map_to_authentication_error_without_secret(self) -> None:
        for status in (401, 403):
            with self.subTest(status=status), serve_json(
                {"error": {"message": "bad secret-token"}}, status=status
            ) as server:
                provider = OpenAICompatibleProvider(
                    model="test-model",
                    api_key="test-secret-token",
                    base_url=server.url,
                )
                with self.assertRaises(LLMProviderError) as raised:
                    provider.complete(LLMRequest("system", "user"))
            error = raised.exception
            self.assertEqual("authentication_error", error.code)
            self.assertFalse(error.retryable)
            self.assertEqual(status, error.status_code)
            self.assertNotIn("test-secret-token", json.dumps(error.to_dict()))
            self.assertEqual(1, server.request_count)


class NativeProviderTests(unittest.TestCase):
    def test_anthropic_maps_messages_response(self) -> None:
        response = {
            "content": [
                {"type": "text", "text": "first"},
                {"type": "tool_use", "id": "ignored"},
                {"type": "text", "text": " second"},
            ],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 11, "output_tokens": 4},
        }
        with serve_json(response) as server:
            provider = AnthropicProvider(
                model="claude-test", api_key="secret", base_url=server.url
            )
            result = provider.complete(
                LLMRequest("system", "user", temperature=0.3, max_tokens=321)
            )

        self.assertEqual("/v1/v1/messages", server.request_path)
        self.assertEqual("secret", server.request_headers["x-api-key"])
        self.assertEqual("2023-06-01", server.request_headers["anthropic-version"])
        self.assertEqual(
            {
                "model": "claude-test",
                "system": "system",
                "messages": [{"role": "user", "content": "user"}],
                "temperature": 0.3,
                "max_tokens": 321,
            },
            server.request_json,
        )
        self.assertEqual("first second", result.content)
        self.assertEqual("end_turn", result.finish_reason)
        self.assertEqual(11, result.usage.input_tokens)
        self.assertEqual(4, result.usage.output_tokens)
        self.assertEqual(15, result.usage.total_tokens)

    def test_anthropic_text_mode_preserves_prompts_and_json_mode_adds_constraint(
        self,
    ) -> None:
        response = {"content": [{"type": "text", "text": '{"ok":true}'}]}
        with serve_json(response) as server:
            provider = AnthropicProvider(
                model="claude-test", api_key="secret", base_url=server.url
            )
            provider.complete(LLMRequest("semantic system", "semantic user"))
        self.assertEqual("semantic system", server.request_json["system"])
        self.assertEqual(
            "semantic user", server.request_json["messages"][0]["content"]
        )

        with serve_json(response) as server:
            provider = AnthropicProvider(
                model="claude-test", api_key="secret", base_url=server.url
            )
            provider.complete(
                LLMRequest(
                    "semantic system", "semantic user", response_format="json"
                )
            )
        constrained_system = server.request_json["system"]
        self.assertTrue(constrained_system.startswith("semantic system"))
        self.assertIn("valid JSON object or array", constrained_system)
        self.assertIn("no Markdown", constrained_system)
        self.assertEqual(
            "semantic user", server.request_json["messages"][0]["content"]
        )

    def test_anthropic_json_validation_and_context_error(self) -> None:
        with serve_json(
            {"content": [{"type": "text", "text": "not-json"}]}
        ) as server:
            provider = AnthropicProvider(
                model="claude-test", api_key="secret", base_url=server.url
            )
            with self.assertRaises(LLMProviderError) as raised:
                provider.complete(LLMRequest("system", "user", response_format="json"))
        self.assertEqual("invalid_structured_output", raised.exception.code)

        with serve_json(
            {"error": {"type": "context_length_exceeded", "message": "too long"}},
            status=400,
        ) as server:
            provider = AnthropicProvider(
                model="claude-test", api_key="secret", base_url=server.url
            )
            with self.assertRaises(LLMProviderError) as raised:
                provider.complete(LLMRequest("system", "user"))
        self.assertEqual("context_length_exceeded", raised.exception.code)

    def test_anthropic_prompt_too_long_error_is_context_length_exceeded(self) -> None:
        payload = {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "prompt is too long: 120001 tokens > 100000 maximum",
            },
        }
        with serve_json(payload, status=400) as server:
            provider = AnthropicProvider(
                model="claude-test", api_key="secret", base_url=server.url
            )
            with self.assertRaises(LLMProviderError) as raised:
                provider.complete(LLMRequest("system", "user"))

        self.assertEqual("context_length_exceeded", raised.exception.code)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(400, raised.exception.status_code)

    def test_http_error_redacts_json_escaped_credentials_structurally(self) -> None:
        for secret in ('quote"credential', "backslash\\credential"):
            with self.subTest(secret=secret), serve_json(
                {"error": {"message": json.dumps({"credential": secret})}},
                status=400,
            ) as server:
                provider = AnthropicProvider(
                    model="claude-test", api_key=secret, base_url=server.url
                )
                with self.assertRaises(LLMProviderError) as raised:
                    provider.complete(LLMRequest("system", "user"))

            error = raised.exception
            serialized_message = error.details["response"]["error"]["message"]
            self.assertEqual(
                {"credential": "<redacted>"}, json.loads(serialized_message)
            )
            self.assertNotIn(secret, json.dumps(error.to_dict(), ensure_ascii=False))

    def test_gemini_maps_generate_content_response(self) -> None:
        response = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": '{"first":'}, {"text": '"second"}'}]
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 9,
                "candidatesTokenCount": 5,
                "totalTokenCount": 14,
            },
        }
        with serve_json(response) as server:
            provider = GeminiProvider(
                model="models/gemini test", api_key="secret", base_url=server.url
            )
            result = provider.complete(
                LLMRequest(
                    "system", "user", temperature=0.6, max_tokens=222,
                    response_format="json",
                )
            )

        self.assertEqual(
            "/v1/v1beta/models/gemini%20test:generateContent",
            server.request_path,
        )
        self.assertEqual("secret", server.request_headers["x-goog-api-key"])
        self.assertEqual(
            {
                "system_instruction": {"parts": [{"text": "system"}]},
                "contents": [{"role": "user", "parts": [{"text": "user"}]}],
                "generationConfig": {
                    "temperature": 0.6,
                    "maxOutputTokens": 222,
                    "responseMimeType": "application/json",
                },
            },
            server.request_json,
        )
        self.assertEqual('{"first":"second"}', result.content)
        self.assertEqual("STOP", result.finish_reason)
        self.assertEqual(9, result.usage.input_tokens)
        self.assertEqual(5, result.usage.output_tokens)
        self.assertEqual(14, result.usage.total_tokens)

    def test_gemini_bare_and_resource_model_ids_use_one_models_segment(self) -> None:
        response = {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}]
        }
        for model in ("gemini-x", "models/gemini-x"):
            with self.subTest(model=model), serve_json(response) as server:
                provider = GeminiProvider(
                    model=model, api_key="secret", base_url=server.url
                )
                provider.complete(LLMRequest("system", "user"))
            self.assertEqual(
                "/v1/v1beta/models/gemini-x:generateContent",
                server.request_path,
            )

    def test_gemini_rejects_empty_or_malformed_resource_model_ids(self) -> None:
        for model in ("models/", "models/   ", "models//gemini-x"):
            with self.subTest(model=model):
                with self.assertRaises(LLMProviderError) as raised:
                    GeminiProvider(
                        model=model,
                        api_key="secret",
                        base_url="http://127.0.0.1:1",
                    )
                self.assertEqual("invalid_configuration", raised.exception.code)

    def test_gemini_safety_blocks_are_provider_diagnostics(self) -> None:
        secret = 'test-quote"and\\backslash'
        payloads = (
            {
                "promptFeedback": {
                    "blockReason": "SAFETY",
                    "blockReasonMessage": f"blocked {secret}",
                    "safetyRatings": [
                        {"category": "HARM_CATEGORY_TEST", "blocked": True}
                    ],
                }
            },
            {
                "candidates": [
                    {
                        "finishReason": "SAFETY",
                        "safetyRatings": [
                            {"category": "HARM_CATEGORY_TEST", "blocked": True}
                        ],
                    }
                ]
            },
            {
                "candidates": [
                    {
                        "content": {"parts": [{"text": "must not be returned"}]},
                        "finishReason": "STOP",
                        "safetyRatings": [
                            {
                                "category": f"HARM_{secret}",
                                "probability": "HIGH",
                                "blocked": True,
                            }
                        ],
                    }
                ]
            },
        )
        for payload in payloads:
            with self.subTest(payload=payload), serve_json(payload) as server:
                provider = GeminiProvider(
                    model="gemini-x", api_key=secret, base_url=server.url
                )
                with self.assertRaises(LLMProviderError) as raised:
                    provider.complete(LLMRequest("system", "user"))
            error = raised.exception
            self.assertEqual("provider_error", error.code)
            self.assertFalse(error.retryable)
            self.assertIn("safety", error.details)
            self.assertNotIn(secret, json.dumps(error.to_dict(), ensure_ascii=False))

    def test_gemini_rejects_empty_parts_and_malformed_json(self) -> None:
        responses = (
            {"candidates": [{"content": {"parts": []}}]},
            {"candidates": [{"content": {"parts": [{"text": "not-json"}]}}]},
        )
        for response in responses:
            with self.subTest(response=response), serve_json(response) as server:
                provider = GeminiProvider(
                    model="gemini-test", api_key="secret", base_url=server.url
                )
                with self.assertRaises(LLMProviderError) as raised:
                    provider.complete(
                        LLMRequest("system", "user", response_format="json")
                    )
            self.assertIn(
                raised.exception.code,
                {"invalid_response", "invalid_structured_output"},
            )

    def test_ollama_maps_native_chat_response_without_auth(self) -> None:
        response = {
            "message": {"role": "assistant", "content": '{"ok":true}'},
            "done_reason": "stop",
            "prompt_eval_count": 6,
            "eval_count": 2,
        }
        with serve_json(response) as server:
            provider = OllamaProvider(model="qwen-local", base_url=server.url)
            result = provider.complete(
                LLMRequest(
                    "system", "user", temperature=0.25, max_tokens=111,
                    response_format="json",
                )
            )

        self.assertEqual("/v1/api/chat", server.request_path)
        self.assertNotIn("Authorization", server.request_headers)
        self.assertEqual(
            {
                "model": "qwen-local",
                "messages": [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "user"},
                ],
                "stream": False,
                "options": {"temperature": 0.25, "num_predict": 111},
                "format": "json",
            },
            server.request_json,
        )
        self.assertEqual('{"ok":true}', result.content)
        self.assertEqual("stop", result.finish_reason)
        self.assertEqual(6, result.usage.input_tokens)
        self.assertEqual(2, result.usage.output_tokens)
        self.assertEqual(8, result.usage.total_tokens)

    def test_ollama_rejects_empty_message(self) -> None:
        with serve_json({"message": {"content": ""}}) as server:
            provider = OllamaProvider(model="qwen-local", base_url=server.url)
            with self.assertRaises(LLMProviderError) as raised:
                provider.complete(LLMRequest("system", "user"))
        self.assertEqual("invalid_response", raised.exception.code)

    def test_native_providers_redact_json_escaped_secrets_from_success(self) -> None:
        for provider_type, response in (
            (
                AnthropicProvider,
                lambda secret: {
                    "content": [{"type": "text", "text": json.dumps({"key": secret})}],
                    "debug": secret,
                },
            ),
            (
                GeminiProvider,
                lambda secret: {
                    "candidates": [{"content": {"parts": [{"text": json.dumps({"key": secret})}]}}],
                    "debug": secret,
                },
            ),
        ):
            for secret in ('quote"credential', "backslash\\credential"):
                with self.subTest(provider=provider_type.__name__, secret=secret), serve_json(
                    response(secret)
                ) as server:
                    provider = provider_type(
                        model="test-model", api_key=secret, base_url=server.url
                    )
                    result = provider.complete(
                        LLMRequest("system", "user", response_format="json")
                    )
                self.assertEqual({"key": "<redacted>"}, json.loads(result.content))
                self.assertNotIn(secret, repr(result.raw_response))

    def test_gemini_scrubs_json_escaped_secret_from_unselected_candidates(self) -> None:
        secret = 'quote"and\\backslash'
        response = {
            "candidates": [
                {"content": {"parts": [{"text": '{"selected":true}'}]}},
                {
                    "content": {
                        "parts": [{"text": json.dumps({"credential": secret})}]
                    }
                },
            ]
        }
        with serve_json(response) as server:
            provider = GeminiProvider(
                model="gemini-test", api_key=secret, base_url=server.url
            )
            result = provider.complete(
                LLMRequest("system", "user", response_format="json")
            )

        self.assertEqual({"selected": True}, json.loads(result.content))
        unselected = result.raw_response["candidates"][1]["content"]["parts"][0][
            "text"
        ]
        self.assertNotEqual(secret, json.loads(unselected)["credential"])

    def test_gemini_secret_redaction_preserves_candidates_and_multipart_shape(
        self,
    ) -> None:
        secret = 'quote"and\\backslash'
        response = {
            "candidates": [
                {
                    secret: "test-secret-key metadata",
                    "content": {
                        "parts": [
                            {"text": '{"ordinary":"keep",', "citation": "keep"},
                            {
                                "text": (
                                    json.dumps(secret)
                                    + ':"secret-key-name","credential":'
                                    + json.dumps(secret)
                                    + "}"
                                ),
                                "extra": 7,
                            },
                        ]
                    },
                    "finishReason": "STOP",
                },
                {
                    "content": {"parts": [{"text": "unrelated candidate"}]},
                    "finishReason": "OTHER",
                },
            ],
            "modelVersion": "keep-version",
        }
        with serve_json(response) as server:
            provider = GeminiProvider(
                model="gemini-x", api_key=secret, base_url=server.url
            )
            result = provider.complete(
                LLMRequest("system", "user", response_format="json")
            )

        raw = result.raw_response
        self.assertEqual(2, len(raw["candidates"]))
        first_parts = raw["candidates"][0]["content"]["parts"]
        self.assertEqual(2, len(first_parts))
        self.assertEqual('{"ordinary":"keep",', first_parts[0]["text"])
        self.assertEqual("keep", first_parts[0]["citation"])
        self.assertEqual(7, first_parts[1]["extra"])
        self.assertEqual(
            {
                "ordinary": "keep",
                "<redacted>": "secret-key-name",
                "credential": "<redacted>",
            },
            json.loads("".join(part["text"] for part in first_parts)),
        )
        self.assertEqual(
            "test-secret-key metadata", raw["candidates"][0]["<redacted>"]
        )
        self.assertEqual(
            "unrelated candidate",
            raw["candidates"][1]["content"]["parts"][0]["text"],
        )
        self.assertEqual("keep-version", raw["modelVersion"])

    def test_common_provider_validation_helpers_keep_existing_contract(self) -> None:
        self.assertEqual("model", require_nonempty_string("model", "model"))
        self.assertEqual(2.5, require_positive_timeout(2.5))
        for value in ("", "   "):
            with self.subTest(value=value), self.assertRaises(LLMProviderError):
                require_nonempty_string(value, "model")

    def test_429_maps_to_retryable_rate_limit_without_retrying(self) -> None:
        with serve_json({"error": {"message": "slow down"}}, status=429) as server:
            provider = OpenAICompatibleProvider(
                model="test-model", api_key="secret", base_url=server.url
            )
            with self.assertRaises(LLMProviderError) as raised:
                provider.complete(LLMRequest("system", "user"))

        self.assertEqual("rate_limited", raised.exception.code)
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(1, server.request_count)

    def test_500_maps_to_retryable_provider_error_without_retrying(self) -> None:
        with serve_json({"error": {"message": "unavailable"}}, status=500) as server:
            provider = OpenAICompatibleProvider(
                model="test-model", api_key="secret", base_url=server.url
            )
            with self.assertRaises(LLMProviderError) as raised:
                provider.complete(LLMRequest("system", "user"))

        self.assertEqual("provider_error", raised.exception.code)
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(500, raised.exception.status_code)
        self.assertEqual(1, server.request_count)

    def test_context_length_payload_is_classified(self) -> None:
        with serve_json(
            {"error": {"code": "context_length_exceeded", "message": "too long"}},
            status=400,
        ) as server:
            provider = OpenAICompatibleProvider(
                model="test-model", api_key="secret", base_url=server.url
            )
            with self.assertRaises(LLMProviderError) as raised:
                provider.complete(LLMRequest("system", "user"))

        self.assertEqual("context_length_exceeded", raised.exception.code)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(1, server.request_count)

    def test_timeout_maps_to_retryable_timeout_without_retrying(self) -> None:
        with serve_json(_success(), delay=0.2) as server:
            provider = OpenAICompatibleProvider(
                model="test-model",
                api_key="secret",
                base_url=server.url,
                timeout_seconds=0.05,
            )
            with self.assertRaises(LLMProviderError) as raised:
                provider.complete(LLMRequest("system", "user"))

        self.assertEqual("timeout", raised.exception.code)
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(1, server.request_count)

    def test_connection_failure_maps_to_retryable_connection_error(self) -> None:
        provider = OpenAICompatibleProvider(
            model="test-model",
            api_key="secret",
            base_url="http://127.0.0.1:1/v1",
            timeout_seconds=1,
        )

        with patch.object(
            requests.Session,
            "post",
            side_effect=requests.ConnectionError("local connection refused"),
        ) as post, self.assertRaises(LLMProviderError) as raised:
            provider.complete(LLMRequest("system", "user"))

        self.assertEqual("connection_error", raised.exception.code)
        self.assertTrue(raised.exception.retryable)
        post.assert_called_once()

    def test_non_json_http_response_is_invalid_response(self) -> None:
        with serve_json(None, raw_body=b"not-json") as server:
            provider = OpenAICompatibleProvider(
                model="test-model", api_key="secret", base_url=server.url
            )
            with self.assertRaises(LLMProviderError) as raised:
                provider.complete(LLMRequest("system", "user"))

        self.assertEqual("invalid_response", raised.exception.code)
        self.assertEqual(1, server.request_count)

    def test_empty_choices_or_content_is_invalid_response(self) -> None:
        payloads = (
            {"choices": [], "usage": {}},
            {"choices": [{}], "usage": {}},
            {"choices": [{"message": {"content": ""}}], "usage": {}},
        )
        for payload in payloads:
            with self.subTest(payload=payload), serve_json(payload) as server:
                provider = OpenAICompatibleProvider(
                    model="test-model", api_key="secret", base_url=server.url
                )
                with self.assertRaises(LLMProviderError) as raised:
                    provider.complete(LLMRequest("system", "user"))
            self.assertEqual("invalid_response", raised.exception.code)
            self.assertEqual(1, server.request_count)

    def test_json_mode_rejects_malformed_or_scalar_json(self) -> None:
        for content in ("not-json", "123", '"text"', "```json\n{}\n```"):
            with self.subTest(content=content), serve_json(_success(content)) as server:
                provider = OpenAICompatibleProvider(
                    model="test-model", api_key="secret", base_url=server.url
                )
                with self.assertRaises(LLMProviderError) as raised:
                    provider.complete(
                        LLMRequest("system", "user", response_format="json")
                    )
            self.assertEqual("invalid_structured_output", raised.exception.code)
            self.assertEqual("openai_compatible", raised.exception.provider)
            self.assertEqual("test-model", raised.exception.model)
            self.assertEqual(1, server.request_count)


if __name__ == "__main__":
    unittest.main()
