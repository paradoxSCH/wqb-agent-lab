from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .registry import load_schema


@dataclass(frozen=True, slots=True)
class ValidationError:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def validate_contract(name: str, payload: dict[str, Any]) -> list[ValidationError]:
    schema = load_schema(name)
    return _validate(schema, payload, "$")


def assert_valid_contract(name: str, payload: dict[str, Any]) -> None:
    errors = validate_contract(name, payload)
    if errors:
        joined = "; ".join(str(error) for error in errors)
        raise ValueError(f"{name} contract validation failed: {joined}")


def _validate(schema: dict[str, Any], value: Any, path: str) -> list[ValidationError]:
    errors: list[ValidationError] = []
    if "anyOf" in schema:
        alternatives = schema.get("anyOf")
        if isinstance(alternatives, list):
            schemas = [item for item in alternatives if isinstance(item, dict)]
            if schemas and not any(not _validate(alt, value, path) for alt in schemas):
                expected = " or ".join(_type_label(alt.get("type")) for alt in schemas)
                errors.append(ValidationError(path, f"expected {expected}, got {_value_type(value)}"))

    if "oneOf" in schema:
        alternatives = schema.get("oneOf")
        if isinstance(alternatives, list):
            schemas = [item for item in alternatives if isinstance(item, dict)]
            matches = sum(not _validate(alt, value, path) for alt in schemas)
            if matches != 1:
                errors.append(ValidationError(path, f"expected exactly one matching schema, got {matches}"))

    expected_type = schema.get("type")
    if expected_type is not None and not _matches_type(value, expected_type):
        errors.append(ValidationError(path, f"expected {_type_label(expected_type)}, got {_value_type(value)}"))
        return errors

    if "enum" in schema and value not in schema["enum"]:
        errors.append(ValidationError(path, f"expected one of {schema['enum']}, got {value!r}"))
    if "const" in schema and value != schema["const"]:
        errors.append(ValidationError(path, f"expected {schema['const']!r}, got {value!r}"))

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(ValidationError(path, f"expected >= {schema['minimum']}, got {value}"))
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(ValidationError(path, f"expected <= {schema['maximum']}, got {value}"))

    if isinstance(value, dict):
        errors.extend(_validate_object(schema, value, path))
    elif isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(_validate(item_schema, item, f"{path}[{index}]"))

    alternatives = schema.get("allOf")
    if isinstance(alternatives, list):
        for alternative in alternatives:
            if isinstance(alternative, dict):
                errors.extend(_validate(alternative, value, path))

    condition = schema.get("if")
    if isinstance(condition, dict):
        branch_name = "then" if not _validate(condition, value, path) else "else"
        branch = schema.get(branch_name)
        if isinstance(branch, dict):
            errors.extend(_validate(branch, value, path))

    return errors


def _validate_object(schema: dict[str, Any], value: dict[str, Any], path: str) -> list[ValidationError]:
    errors: list[ValidationError] = []
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}

    for required in schema.get("required", []):
        if required not in value:
            errors.append(ValidationError(f"{path}.{required}", "missing required property"))

    additional = schema.get("additionalProperties", True)
    for key, item in value.items():
        child_path = f"{path}.{key}"
        child_schema = properties.get(key)
        if isinstance(child_schema, dict):
            errors.extend(_validate(child_schema, item, child_path))
            continue
        if additional is False:
            errors.append(ValidationError(child_path, "unexpected additional property"))
        elif isinstance(additional, dict):
            errors.extend(_validate(additional, item, child_path))

    return errors


def _matches_type(value: Any, expected_type: Any) -> bool:
    if isinstance(expected_type, list):
        return any(_matches_type(value, item) for item in expected_type)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return True


def _value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _type_label(expected_type: Any) -> str:
    if isinstance(expected_type, list):
        return " or ".join(str(item) for item in expected_type)
    return str(expected_type)
