from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_NAMES = (
    "candidate",
    "diagnosis",
    "memory_event",
    "research_policy",
    "run_summary",
    "simulation_request",
    "simulation_result",
    "submission_job",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "schemas"


def list_schema_names() -> tuple[str, ...]:
    return SCHEMA_NAMES


def schema_path(name: str) -> Path:
    if name not in SCHEMA_NAMES:
        raise KeyError(f"Unknown schema: {name}")
    return SCHEMA_DIR / f"{name}.schema.json"


def load_schema(name: str) -> dict[str, Any]:
    path = schema_path(name)
    with path.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    if not isinstance(schema, dict):
        raise ValueError(f"Schema {name} did not load as an object")
    return schema


def schema_digest(name: str) -> str:
    content = schema_path(name).read_bytes()
    return hashlib.sha256(content).hexdigest()
