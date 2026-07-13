from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Iterable, Sequence, TextIO


_PRIVATE_PREFIXES = (
    ".local/data/runs/",
    ".local/data/callbacks/",
    ".local/data/memory/",
    ".local/data/evaluations/",
    ".local/data/behavioral_candidate_generation/",
    ".local/data/behavioral_proxy/",
    ".local/data/registry/",
    "output/playwright/",
    ".local/research/scans/",
    ".local/research/workflows/",
)
_PRIVATE_NAMES = {
    "worldquant_interview_defense.html",
}
_PRIVATE_PARTS = {
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}
_PRIVATE_SUFFIXES = (".log", ".pid", ".pyc", ".pyo")
_PLACEHOLDER_METADATA = (
    "github.com/your-org/",
    "github.com/your-username/",
    "github.com/example/",
)
_METADATA_FILES = {
    "pyproject.toml",
    "package.json",
    "packages/wqb-agent-mcp/package.json",
    "packages/wqb-agent-ui/package.json",
}
_CONFIG_SUFFIXES = {".env", ".ini", ".cfg", ".toml", ".yaml", ".yml", ".json"}
_CREDENTIAL_KEY = r"(?:[A-Z0-9]+_)*(?:PASSWORD|SECRET|TOKEN|API_KEY|ACCESS_KEY|PRIVATE_KEY)"
_CONFIG_CREDENTIAL_ASSIGNMENT = re.compile(
    rf"(?im)^\s*[\"']?(?P<key>{_CREDENTIAL_KEY})[\"']?"
    r"\s*[:=]\s*[\"']?(?P<value>[^\"'\s,#}]+)"
)
_SOURCE_CREDENTIAL_LITERAL = re.compile(
    rf"(?im)^\s*(?P<key>{_CREDENTIAL_KEY})\s*[:=]\s*"
    r"(?P<quote>[\"'])(?P<value>[^\"'\r\n]+)(?P=quote)"
)
_UNSAFE_LIVE_DEFAULT = re.compile(
    r"(?im)^\s*(?:export\s+)?(?:WQB_LIVE_SUBMIT_CAPABILITY|WQB_AUTO_SUBMIT_ENABLED)"
    r"\s*[:=]\s*[\"']?(?:1|true|yes|on)[\"']?\s*(?:#.*)?$"
)
_TEXT_SUFFIXES = {
    ".cfg",
    ".env",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}


@dataclass(frozen=True, slots=True)
class AuditFinding:
    code: str
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class AuditReport:
    root: str
    candidate_count: int
    findings: list[AuditFinding]

    @property
    def ok(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "root": self.root,
            "candidate_count": self.candidate_count,
            "finding_count": len(self.findings),
            "findings": [asdict(finding) for finding in self.findings],
        }


class AuditExecutionError(RuntimeError):
    pass


def discover_publish_candidates(root: Path | str) -> list[str]:
    workspace = Path(root).resolve()
    completed = subprocess.run(
        ["git", "-C", str(workspace), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise AuditExecutionError(detail or "Unable to enumerate Git publish candidates.")
    return sorted(
        path.replace("\\", "/")
        for path in completed.stdout.decode("utf-8", errors="surrogateescape").split("\0")
        if path
    )


def audit_repository(root: Path | str) -> AuditReport:
    workspace = Path(root).resolve()
    return audit_candidates(workspace, discover_publish_candidates(workspace))


def audit_candidates(root: Path | str, candidate_paths: Iterable[str]) -> AuditReport:
    workspace = Path(root).resolve()
    normalized_candidates = {_normalize_relative_path(str(path)) for path in candidate_paths}
    candidates = sorted(
        relative_path
        for relative_path in normalized_candidates
        if (workspace / PurePosixPath(relative_path)).is_file()
    )
    findings: list[AuditFinding] = []

    for relative_path in candidates:
        if _is_private_artifact(relative_path):
            findings.append(
                AuditFinding(
                    code="private_artifact",
                    path=relative_path,
                    message="Local runtime or private artifact is included in the publish candidate set.",
                )
            )
            continue

        path = workspace / PurePosixPath(relative_path)
        if not path.is_file() or not _is_text_candidate(path):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        lowered_content = content.lower()
        if relative_path.lower() in _METADATA_FILES and any(
            marker in lowered_content for marker in _PLACEHOLDER_METADATA
        ):
            findings.append(
                AuditFinding(
                    code="placeholder_metadata",
                    path=relative_path,
                    message="Project metadata still contains a placeholder repository URL.",
                )
            )

        if _contains_credential_value(path, content):
            findings.append(
                AuditFinding(
                    code="credential_value",
                    path=relative_path,
                    message="A real-looking credential assignment is present; the value was redacted.",
                )
            )

        if _is_live_default_surface(relative_path) and _UNSAFE_LIVE_DEFAULT.search(content):
            findings.append(
                AuditFinding(
                    code="unsafe_live_default",
                    path=relative_path,
                    message="A public candidate enables live submission by default.",
                )
            )

    findings.sort(key=lambda item: (item.path, item.code, item.message))
    return AuditReport(root=str(workspace), candidate_count=len(candidates), findings=findings)


def run(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    candidate_paths: Iterable[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Audit repository publish candidates without live WQB access.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(list(argv or []))
    out_stream = stdout or sys.stdout
    err_stream = stderr or sys.stderr
    root = Path(args.root).resolve()

    try:
        report = (
            audit_candidates(root, candidate_paths)
            if candidate_paths is not None
            else audit_repository(root)
        )
    except AuditExecutionError as exc:
        if args.as_json:
            out_stream.write(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False) + "\n")
        else:
            err_stream.write(f"release audit error: {exc}\n")
        return 2

    if args.as_json:
        out_stream.write(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    elif report.ok:
        out_stream.write(f"Release audit passed ({report.candidate_count} publish candidates).\n")
    else:
        out_stream.write(
            f"Release audit found {len(report.findings)} issue(s) in "
            f"{report.candidate_count} publish candidates.\n"
        )
        for finding in report.findings:
            out_stream.write(f"[{finding.code}] {finding.path}: {finding.message}\n")
    return 0 if report.ok else 1


def main() -> None:
    raise SystemExit(run(sys.argv[1:]))


def _is_private_artifact(relative_path: str) -> bool:
    normalized = _normalize_relative_path(relative_path)
    lowered = normalized.lower()
    parts = set(PurePosixPath(lowered).parts)
    name = PurePosixPath(lowered).name

    if lowered in _PRIVATE_NAMES:
        return True
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    if lowered.endswith(_PRIVATE_SUFFIXES):
        return True
    if parts.intersection(_PRIVATE_PARTS):
        return True
    if any(lowered.startswith(prefix) for prefix in _PRIVATE_PREFIXES):
        return name != ".gitkeep"
    return False


def _normalize_relative_path(relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _is_text_candidate(path: Path) -> bool:
    name = path.name.lower()
    return path.suffix.lower() in _TEXT_SUFFIXES or name.startswith(".env") or name in {
        "license",
        "manifest.in",
    }


def _contains_credential_value(path: Path, content: str) -> bool:
    name = path.name.lower()
    is_config = path.suffix.lower() in _CONFIG_SUFFIXES or name.startswith(".env")
    pattern = _CONFIG_CREDENTIAL_ASSIGNMENT if is_config else _SOURCE_CREDENTIAL_LITERAL
    for match in pattern.finditer(content):
        value = match.group("value").strip()
        if not _is_documentation_value(value):
            return True
    return False


def _is_live_default_surface(relative_path: str) -> bool:
    normalized = _normalize_relative_path(relative_path).lower()
    name = PurePosixPath(normalized).name
    return (
        normalized == "readme.md"
        or name.startswith(".env")
        or normalized.startswith(".github/workflows/")
        or normalized.startswith("configs/templates/")
        or normalized.startswith("examples/")
        or name == "package.json"
    )


def _is_documentation_value(value: str) -> bool:
    lowered = value.lower()
    return (
        not value
        or lowered.startswith(("your_", "your-", "example", "placeholder", "dummy", "test"))
        or lowered in {"changeme", "change_me", "secret", "password", "none", "null"}
        or value.startswith(("${", "<"))
        or "example.com" in lowered
    )


if __name__ == "__main__":
    main()
