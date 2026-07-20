from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wqb_agent_lab.workflow.llm_planning import LLMPlanAdapter
from wqb_agent_lab.llm.provider import LLMRequest, LLMResponse, LLMUsage


def _valid_proposal() -> dict[str, object]:
    return {
        "schema_version": 1,
        "plan_id": "plan-adapter",
        "objective": "Preserve a novel research direction.",
        "hypotheses": [
            {
                "hypothesis_id": "hyp-new",
                "thesis": "A new proxy may capture delayed attention.",
                "mechanism": "unknown_attention_mechanism",
                "expressions": ["new_operator(unknown_field, 17)"],
                "extensions": {"proposed_proxy_fields": ["unknown_field"]},
            }
        ],
        "requested_actions": [
            {
                "action_id": "action-new",
                "kind": "future_read_only_probe",
                "extensions": {"retain": True},
            }
        ],
        "freeform_notes": "Do not discard the proposal because its mechanism is new.",
    }


class SequenceProvider:
    provider_id = "fixture"
    model = "fixture-model"

    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        content = self.responses.pop(0)
        rendered = content if isinstance(content, str) else json.dumps(content)
        return LLMResponse(
            content=rendered,
            provider=self.provider_id,
            model=self.model,
            usage=LLMUsage(),
        )


class LLMPlanProposalAdapterTests(unittest.TestCase):
    def test_adapter_repairs_structure_without_narrowing_research_content(self) -> None:
        invalid = _valid_proposal()
        invalid.pop("objective")
        provider = SequenceProvider([invalid, _valid_proposal()])
        adapter = LLMPlanAdapter.from_config(
            {"llm_provider": {"provider": "ollama", "model": "fixture-model"}},
            llm_provider=provider,
        )

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0"}
        ):
            payload = adapter.call_plan_proposal(Path(tmp), "Create a broad plan.", max_repairs=1)

        self.assertEqual("valid", payload["proposal_contract"]["status"])
        self.assertEqual(1, payload["proposal_contract"]["repair_count"])
        self.assertEqual("unknown_attention_mechanism", payload["proposal"]["hypotheses"][0]["mechanism"])
        self.assertEqual("future_read_only_probe", payload["proposal"]["requested_actions"][0]["kind"])
        self.assertEqual(2, len(provider.requests))
        self.assertEqual("plan_proposal", provider.requests[0].metadata["output_contract"])
        self.assertIn("Preserve novel mechanisms", provider.requests[0].system_prompt)
        self.assertIn("$.objective: missing required property", provider.requests[1].user_prompt)

    def test_invalid_json_enters_the_same_structural_repair_path(self) -> None:
        provider = SequenceProvider(["not-json", _valid_proposal()])
        adapter = LLMPlanAdapter.from_config(
            {"llm_provider": {"provider": "ollama", "model": "fixture-model"}},
            llm_provider=provider,
        )

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0"}
        ):
            payload = adapter.call_plan_proposal(Path(tmp), "Plan.", max_repairs=1)

        self.assertEqual("valid", payload["proposal_contract"]["status"])
        self.assertEqual(1, payload["proposal_contract"]["repair_count"])
        self.assertIn("expected object", provider.requests[1].user_prompt)

    def test_exhausted_repairs_return_auditable_contract_error(self) -> None:
        provider = SequenceProvider([{"schema_version": 1}, {"schema_version": 1}])
        adapter = LLMPlanAdapter.from_config(
            {"llm_provider": {"provider": "ollama", "model": "fixture-model"}},
            llm_provider=provider,
        )

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0"}
        ):
            payload = adapter.call_plan_proposal(Path(tmp), "Plan.", max_repairs=1)

        self.assertEqual("invalid_structured_output", payload["error"]["code"])
        self.assertEqual("error", payload["proposal_contract"]["status"])
        self.assertEqual(1, payload["proposal_contract"]["repair_count"])
        self.assertTrue(payload["proposal_contract"]["errors"])

    def test_legacy_call_path_remains_unchanged(self) -> None:
        provider = SequenceProvider([{"directions": []}])
        adapter = LLMPlanAdapter.from_config(
            {"llm_provider": {"provider": "ollama", "model": "fixture-model"}},
            llm_provider=provider,
        )

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0"}
        ):
            payload = adapter.call(Path(tmp), "Legacy plan.")

        self.assertEqual([], payload["directions"])
        self.assertNotIn("proposal_contract", payload)

    def test_configured_call_uses_plan_contract_only_when_explicitly_enabled(self) -> None:
        provider = SequenceProvider([_valid_proposal()])
        adapter = LLMPlanAdapter.from_config(
            {
                "llm_provider": {
                    "provider": "ollama",
                    "model": "fixture-model",
                    "output_contract": "plan_proposal",
                    "max_structure_repairs": 1,
                }
            },
            llm_provider=provider,
        )

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0"}
        ):
            payload = adapter.call_configured(Path(tmp), "Plan.")

        self.assertEqual("valid", payload["proposal_contract"]["status"])
        self.assertIn("Use extensions and freeform_notes", provider.requests[0].user_prompt)

    def test_unknown_output_contract_fails_without_calling_provider(self) -> None:
        provider = SequenceProvider([_valid_proposal()])
        adapter = LLMPlanAdapter.from_config(
            {
                "llm_provider": {
                    "provider": "ollama",
                    "model": "fixture-model",
                    "output_contract": "vendor_locked_schema",
                }
            },
            llm_provider=provider,
        )

        with tempfile.TemporaryDirectory() as tmp:
            payload = adapter.call_configured(Path(tmp), "Plan.")

        self.assertEqual("invalid_configuration", payload["error"]["code"])
        self.assertTrue(payload["disabled"])
        self.assertEqual([], provider.requests)


if __name__ == "__main__":
    unittest.main()
