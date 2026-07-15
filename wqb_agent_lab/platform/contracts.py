from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ContractIssue:
    endpoint: str
    code: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def validate_read_contract(endpoint: str, status_code: int, payload: Any) -> list[ContractIssue]:
    if status_code >= 400:
        return [ContractIssue(endpoint, "http_error", f"HTTP {status_code}")]
    if endpoint == "/authentication":
        return [] if isinstance(payload, Mapping) else [ContractIssue(endpoint, "payload_not_object", "expected JSON object")]
    if endpoint == "/operators":
        valid = isinstance(payload, list) or (isinstance(payload, Mapping) and isinstance(payload.get("results"), list))
        return [] if valid else [ContractIssue(endpoint, "operators_not_list", "expected a list or results list")]
    if endpoint == "/users/self/alphas":
        if not isinstance(payload, Mapping):
            return [ContractIssue(endpoint, "payload_not_object", "expected paginated JSON object")]
        issues: list[ContractIssue] = []
        if not isinstance(payload.get("results"), list):
            issues.append(ContractIssue(endpoint, "results_not_list", "results must be a list"))
        if "count" in payload and not isinstance(payload.get("count"), int):
            issues.append(ContractIssue(endpoint, "count_not_integer", "count must be an integer when present"))
        return issues
    return []


def validate_simulation_create_contract(status_code: int, headers: Mapping[str, Any]) -> list[ContractIssue]:
    if not 200 <= status_code < 400:
        return []
    if not headers.get("Location"):
        return [
            ContractIssue(
                "/simulations",
                "location_header_missing",
                "successful simulation creation must expose the polling URL in Location",
            )
        ]
    return []


__all__ = ["ContractIssue", "validate_read_contract", "validate_simulation_create_contract"]
