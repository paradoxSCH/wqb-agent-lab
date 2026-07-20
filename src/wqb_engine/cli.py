from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

def run(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    in_stream = stdin or sys.stdin
    out_stream = stdout or sys.stdout
    _err_stream = stderr or sys.stderr
    args = list(argv or [])
    operation = args[0] if args else ""

    try:
        if operation in {"-h", "--help"} or not operation:
            _write_json(out_stream, _help_payload("help"))
            return 0
        if len(args) == 2 and args[1] in {"-h", "--help"} and operation in _OPERATION_HELP:
            _write_json(out_stream, _help_payload(operation))
            return 0

        if operation == "schemas.list":
            from wqb_agent_lab.contracts import list_schema_names

            _write_json(
                out_stream,
                {
                    "ok": True,
                    "operation": operation,
                    "data": {"schemas": list(list_schema_names())},
                },
            )
            return 0

        if operation == "schemas.digest":
            from wqb_agent_lab.contracts import schema_digest

            parsed = _parse_schema_args(operation, args[1:])
            _write_json(
                out_stream,
                {
                    "ok": True,
                    "operation": operation,
                    "data": {"schema": parsed.schema, "digest": schema_digest(parsed.schema)},
                },
            )
            return 0

        if operation == "contracts.validate":
            from wqb_agent_lab.contracts import validate_contract

            parsed = _parse_validate_args(operation, args[1:])
            payload, error_code, error_message = _read_payload(parsed.payload, in_stream)
            if error_code:
                _write_error(out_stream, operation, error_code, error_message, [])
                return 2
            if not isinstance(payload, dict):
                _write_error(out_stream, operation, "invalid_payload", "Payload must be a JSON object.", [])
                return 2
            errors = validate_contract(parsed.schema, payload)
            if errors:
                _write_error(
                    out_stream,
                    operation,
                    "validation_failed",
                    f"{parsed.schema} contract validation failed",
                    [str(error) for error in errors],
                )
                return 2
            _write_json(
                out_stream,
                {
                    "ok": True,
                    "operation": operation,
                    "data": {"valid": True, "errors": []},
                },
            )
            return 0

        if operation in {"policy.validate", "policy.show"}:
            from wqb_agent_lab.research.policy import (
                ResearchPolicyError,
                load_research_policy,
                policy_digest,
            )

            parsed = _parse_policy_args(operation, args[1:])
            config_path = Path(parsed.config)
            if not config_path.is_file():
                _write_error(
                    out_stream,
                    operation,
                    "config_not_found",
                    f"Workflow config does not exist: {config_path}",
                    [{"path": str(config_path)}],
                )
                return 2
            try:
                config = json.loads(config_path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError as exc:
                _write_error(
                    out_stream,
                    operation,
                    "invalid_json",
                    f"Invalid workflow config JSON: {exc.msg}",
                    [{"path": str(config_path)}],
                )
                return 2
            try:
                policy = load_research_policy(config)
            except ResearchPolicyError as exc:
                _write_error(
                    out_stream,
                    operation,
                    exc.code,
                    exc.message,
                    [{"path": exc.path}],
                )
                return 2
            data: dict[str, Any] = {
                "valid": True,
                "digest": policy_digest(policy),
                "version": policy.version,
            }
            if operation == "policy.show":
                data["policy"] = policy.to_dict()
            _write_json(out_stream, {"ok": True, "operation": operation, "data": data})
            return 0

        if operation in {"llm.validate", "llm.show", "llm.probe"}:
            return _run_llm_operation(operation, args[1:], out_stream)

        if operation == "submission.evaluate":
            from wqb_agent_lab.governance.submission import (
                SubmitDecision,
                SubmissionPolicyEvaluator,
            )

            payload, error_code, error_message = _read_payload(None, in_stream)
            if error_code:
                _write_error(out_stream, operation, error_code, error_message, [])
                return 2
            if not isinstance(payload, dict):
                _write_error(out_stream, operation, "invalid_payload", "Payload must be a JSON object.", [])
                return 2
            decision = SubmitDecision.from_payload(payload)
            evaluation = SubmissionPolicyEvaluator().evaluate(decision)
            _write_json(
                out_stream,
                {
                    "ok": True,
                    "operation": operation,
                    "data": {"decision": decision.to_dict(), "evaluation": evaluation.to_dict()},
                },
            )
            return 0

        if operation in {"submission.submit_intent", "submission.execute_live"}:
            from wqb_agent_lab.governance.submission import (
                PolicyEvaluation,
                SubmitDecision,
                SubmissionExecutor,
                SubmissionPolicyEvaluator,
            )

            parsed = _parse_submission_execution_args(operation, args[1:])
            payload, error_code, error_message = _read_payload(parsed.payload, in_stream)
            if error_code:
                _write_error(out_stream, operation, error_code, error_message, [])
                return 2
            if not isinstance(payload, dict):
                _write_error(out_stream, operation, "invalid_payload", "Payload must be a JSON object.", [])
                return 2
            decision_payload = payload.get("decision") if isinstance(payload.get("decision"), dict) else payload
            decision = SubmitDecision.from_payload(decision_payload)
            if operation == "submission.submit_intent":
                decision.requested_mode = "queue_only"
            elif decision.requested_mode != "execute_live":
                decision.requested_mode = "execute_live"
            evaluation_payload = payload.get("evaluation") if isinstance(payload.get("evaluation"), dict) else None
            evaluation = (
                SubmissionPolicyEvaluator().evaluate(decision)
                if evaluation_payload is None
                else PolicyEvaluation.from_payload(evaluation_payload)
            )
            result = SubmissionExecutor(parsed.run_dir).execute(decision, evaluation)
            if result.get("status") == "capability_disabled":
                _write_error(
                    out_stream,
                    operation,
                    "capability_disabled",
                    "Live submit capability is disabled.",
                    [json.dumps(result, ensure_ascii=False, sort_keys=True)],
                )
                return 2
            if result.get("status") in {"policy_blocked", "duplicate_decision"}:
                _write_error(
                    out_stream,
                    operation,
                    str(result.get("status")),
                    f"Submission governance returned {result.get('status')}.",
                    [json.dumps(result, ensure_ascii=False, sort_keys=True)],
                )
                return 2
            _write_json(
                out_stream,
                {
                    "ok": True,
                    "operation": operation,
                    "data": {"decision": decision.to_dict(), "evaluation": evaluation.to_dict(), "result": result},
                },
            )
            return 0

        if operation == "submission.audit_tail":
            from wqb_agent_lab.governance.submission import SubmissionGovernanceLedger

            parsed = _parse_audit_tail_args(operation, args[1:])
            events = SubmissionGovernanceLedger(parsed.run_dir).audit_tail(limit=parsed.limit)
            _write_json(
                out_stream,
                {
                    "ok": True,
                    "operation": operation,
                    "data": {"events": events},
                },
            )
            return 0

        if operation in {"loop.dry_run_validate", "demo"}:
            from src.loop_validation import run_dry_run_loop_validation

            parsed = _parse_loop_dry_run_args(operation, args[1:])
            report = run_dry_run_loop_validation(parsed.workspace_root, run_tag=parsed.run_tag)
            _write_json(
                out_stream,
                {
                    "ok": True,
                    "operation": operation,
                    "data": report,
                },
            )
            return 0

        _write_error(out_stream, operation or "unknown", "unknown_operation", f"Unknown operation: {operation}", [])
        return 2
    except (KeyError, SystemExit, argparse.ArgumentError) as exc:
        _write_error(out_stream, operation or "unknown", "usage_error", str(exc), [])
        return 2
    except Exception as exc:  # pragma: no cover - last-resort machine-readable boundary
        message = (
            "LLM operation failed unexpectedly."
            if operation.startswith("llm.")
            else str(exc)
        )
        _write_error(out_stream, operation or "unknown", "internal_error", message, [])
        return 1


def main() -> None:
    raise SystemExit(run(sys.argv[1:]))


def _run_llm_operation(
    operation: str,
    args: list[str],
    out_stream: TextIO,
) -> int:
    import os

    from dotenv import dotenv_values, find_dotenv
    from wqb_agent_lab.llm.provider import (
        LLMProviderError,
        LLMRequest,
        create_llm_provider,
        llm_config_identity,
        resolve_llm_provider_config,
    )
    from wqb_agent_lab.llm.provider.errors import redact_secrets

    try:
        parsed = _parse_llm_args(operation, args)
        config_path = Path(parsed.config)
        config, load_error = _load_workflow_config(config_path)
        if load_error is not None:
            code, message = load_error
            _write_error(
                out_stream,
                operation,
                code,
                message,
                [{"path": str(config_path)}],
            )
            return 2

        dotenv_path = find_dotenv(usecwd=True)
        dotenv_environment = (
            {
                key: value
                for key, value in dotenv_values(dotenv_path).items()
                if isinstance(value, str)
            }
            if dotenv_path
            else {}
        )
        effective_environment = {**dotenv_environment, **os.environ}
        resolved = resolve_llm_provider_config(
            config,
            env=effective_environment,
            require_credentials=operation == "llm.probe",
        )
        identity = llm_config_identity(resolved)
        common_data: dict[str, Any] = {
            "provider": identity["provider"],
            "model": identity["model"],
            "config_digest": identity["config_digest"],
            "warnings": identity["migration_warnings"],
        }

        if operation == "llm.validate":
            return _write_llm_success(
                out_stream,
                operation,
                {"valid": True, **common_data},
                resolved.api_key,
                redact_secrets,
            )

        if operation == "llm.show":
            return _write_llm_success(
                out_stream,
                operation,
                {
                    **common_data,
                    "effective_config": resolved.to_redacted_dict(),
                },
                resolved.api_key,
                redact_secrets,
            )

        if resolved.config.provider == "disabled":
            _write_error(
                out_stream,
                operation,
                "usage_error",
                "LLM provider is disabled; configure a provider before probing.",
                [],
            )
            return 2

        provider = create_llm_provider(resolved, workspace_root=Path.cwd())
        if provider is None:  # Defensive guard for registry implementations.
            _write_error(
                out_stream,
                operation,
                "usage_error",
                "LLM provider is disabled; configure a provider before probing.",
                [],
            )
            return 2
        request = LLMRequest(
            system_prompt="Connectivity check. Return a concise confirmation.",
            user_prompt="Reply with OK.",
            temperature=0,
            max_tokens=16,
            response_format="text",
            timeout_seconds=resolved.config.timeout_seconds,
            metadata={"purpose": "connectivity_probe"},
        )
        started = time.perf_counter()
        response = provider.complete(request)
        latency_ms = max(0, round((time.perf_counter() - started) * 1000))
        return _write_llm_success(
            out_stream,
            operation,
            {
                **common_data,
                "provider": response.provider,
                "model": response.model,
                "latency_ms": latency_ms,
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
                "content_validation": {
                    "valid": bool(response.content.strip()),
                    "kind": "non_empty_text",
                },
            },
            resolved.api_key,
            redact_secrets,
        )
    except LLMProviderError as exc:
        _write_llm_error(out_stream, operation, exc)
        return 2


def _write_llm_success(
    stdout: TextIO,
    operation: str,
    data: dict[str, Any],
    secret: str | None,
    redactor: Any,
) -> int:
    payload = redactor(
        {"ok": True, "operation": operation, "data": data},
        (secret or "",),
    )
    if not isinstance(payload, dict):  # pragma: no cover - redactor contract
        payload = {
            "ok": False,
            "operation": operation,
            "error": {"code": "internal_error"},
        }
    _write_json(stdout, payload)
    return 0


def _parse_schema_args(operation: str, args: list[str]) -> argparse.Namespace:
    parser = _parser(operation)
    parser.add_argument("--schema", required=True)
    return parser.parse_args(args)


def _parse_validate_args(operation: str, args: list[str]) -> argparse.Namespace:
    parser = _parser(operation)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--payload")
    return parser.parse_args(args)


def _parse_policy_args(operation: str, args: list[str]) -> argparse.Namespace:
    parser = _parser(operation)
    parser.add_argument("--config", required=True)
    return parser.parse_args(args)


def _parse_llm_args(operation: str, args: list[str]) -> argparse.Namespace:
    parser = _parser(operation)
    parser.add_argument("--config", required=True)
    return parser.parse_args(args)


def _parse_submission_execution_args(operation: str, args: list[str]) -> argparse.Namespace:
    parser = _parser(operation)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--payload")
    return parser.parse_args(args)


def _parse_audit_tail_args(operation: str, args: list[str]) -> argparse.Namespace:
    parser = _parser(operation)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--limit", type=int, default=20)
    return parser.parse_args(args)


def _parse_loop_dry_run_args(operation: str, args: list[str]) -> argparse.Namespace:
    parser = _parser(operation)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--run-tag", default="dry-run-loop-validation")
    return parser.parse_args(args)


def _parser(prog: str) -> argparse.ArgumentParser:
    return argparse.ArgumentParser(prog=f"wqb-engine {prog}", add_help=True, exit_on_error=False)


def _write_json(stdout: TextIO, payload: dict[str, Any]) -> None:
    stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def _read_payload(payload_arg: str | None, stdin: TextIO) -> tuple[Any, str, str]:
    payload_text = payload_arg if payload_arg is not None else stdin.read()
    try:
        return json.loads(payload_text or "{}"), "", ""
    except json.JSONDecodeError as exc:
        return None, "invalid_json", f"Invalid JSON payload: {exc.msg}"


def _write_error(
    stdout: TextIO,
    operation: str,
    code: str,
    message: str,
    details: list[Any],
) -> None:
    _write_json(
        stdout,
        {
            "ok": False,
            "operation": operation,
            "error": {
                "code": code,
                "message": message,
                "details": details,
            },
        },
    )


def _write_llm_error(
    stdout: TextIO,
    operation: str,
    error: Any,
) -> None:
    _write_json(
        stdout,
        {
            "ok": False,
            "operation": operation,
            "error": error.to_dict(),
        },
    )


def _load_workflow_config(
    config_path: Path,
) -> tuple[dict[str, Any], tuple[str, str] | None]:
    if not config_path.is_file():
        return {}, (
            "config_not_found",
            f"Workflow config does not exist: {config_path}",
        )
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError) as exc:
        return {}, ("config_read_error", f"Unable to read workflow config: {exc}")
    except json.JSONDecodeError as exc:
        return {}, ("invalid_json", f"Invalid workflow config JSON: {exc.msg}")
    if not isinstance(payload, dict):
        return {}, ("invalid_configuration", "Workflow config must be a JSON object.")
    return payload, None


_OPERATION_HELP: dict[str, list[str]] = {
    "schemas.list": [],
    "schemas.digest": ["--schema"],
    "contracts.validate": ["--schema"],
    "policy.validate": ["--config"],
    "policy.show": ["--config"],
    "llm.validate": ["--config"],
    "llm.show": ["--config"],
    "llm.probe": ["--config"],
    "submission.evaluate": [],
    "submission.submit_intent": ["--run-dir"],
    "submission.execute_live": ["--run-dir"],
    "submission.audit_tail": ["--run-dir"],
    "loop.dry_run_validate": ["--workspace-root"],
    "demo": ["--workspace-root"],
}


def _help_payload(operation: str) -> dict[str, Any]:
    if operation == "help":
        data: dict[str, Any] = {"operations": sorted(_OPERATION_HELP)}
    else:
        data = {"required_options": _OPERATION_HELP[operation]}
    return {"ok": True, "operation": operation, "data": data}


if __name__ == "__main__":
    main()
