"""Refresh the submitted-alpha registry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from wqb_agent_lab.runtime.config import load_config
from src.session import create_brain_session


DEFAULT_OUTPUT = Path(".local/data/registry/submitted_alphas.json")
DEFAULT_EXPRESSIONS = Path(".local/data/registry/submitted_expressions.txt")
DEFAULT_BLOCKLIST = Path(".local/data/registry/submitted_blocklist.json")


def normalize_expression(expression: str) -> str:
    return " ".join(str(expression or "").split())


def alpha_expression(alpha: dict[str, Any]) -> str:
    regular = alpha.get("regular", {}) or {}
    return str(regular.get("code", "") or alpha.get("expression", "") or "")


def compact_record(alpha: dict[str, Any]) -> dict[str, Any]:
    is_data = alpha.get("is", {}) or {}
    return {
        "alpha_id": str(alpha.get("id", "") or ""),
        "expression": alpha_expression(alpha),
        "settings": alpha.get("settings", {}) or {},
        "status": str(alpha.get("status", "") or ""),
        "dateSubmitted": alpha.get("dateSubmitted"),
        "metrics": {
            "sharpe": is_data.get("sharpe"),
            "fitness": is_data.get("fitness"),
            "turnover": is_data.get("turnover"),
            "returns": is_data.get("returns"),
            "drawdown": is_data.get("drawdown"),
            "margin": is_data.get("margin"),
        },
    }


def is_submitted(record: dict[str, Any], submitted_statuses: set[str]) -> bool:
    status = str(record.get("status", "") or "").upper()
    return bool(record.get("dateSubmitted")) or status in submitted_statuses


def build_blocklist(expressions: list[str]) -> list[dict[str, str]]:
    blocklist: list[dict[str, str]] = []
    for expression in expressions:
        if "if_else(rank(cap) >" in expression:
            base = expression.split("if_else(rank(cap) >", 1)[0].strip()
            if base.endswith("+"):
                base = base[:-1].strip()
            blocklist.append({
                "type": "skeleton_with_cap_gate",
                "full_expression": expression,
                "base_skeleton": base,
            })
            continue
        blocklist.append({
            "type": "full_expression",
            "full_expression": expression,
            "base_skeleton": expression,
        })
    return blocklist


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="拉取账号中已提交的 BRAIN Alpha，并生成 registry 索引。")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="已提交 Alpha JSON 输出路径。")
    parser.add_argument("--expressions", default=str(DEFAULT_EXPRESSIONS), help="已提交表达式文本输出路径。")
    parser.add_argument("--blocklist", default=str(DEFAULT_BLOCKLIST), help="表达式 blocklist JSON 输出路径。")
    parser.add_argument(
        "--submitted-statuses",
        default="SUBMITTED,ACTIVE",
        help="逗号分隔的已提交状态名；dateSubmitted 非空的 Alpha 始终视为已提交。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    submitted_statuses = {item.strip().upper() for item in args.submitted_statuses.split(",") if item.strip()}

    config = load_config()
    session = create_brain_session(config)

    print("正在从 BRAIN 拉取账号 Alpha 列表...", flush=True)
    all_alphas = session.filter_alphas(limit=100, offset=0)
    records = [compact_record(alpha) for alpha in all_alphas]
    submitted = [record for record in records if is_submitted(record, submitted_statuses)]

    output_path = Path(args.output)
    expressions_path = Path(args.expressions)
    blocklist_path = Path(args.blocklist)

    write_json(output_path, {
        "total_alpha_count": len(records),
        "submitted_alpha_count": len(submitted),
        "submitted_statuses": sorted(submitted_statuses),
        "submitted": submitted,
        "all_records": records,
    })

    expressions = sorted({normalize_expression(record["expression"]) for record in submitted if record.get("expression")})
    expressions_path.parent.mkdir(parents=True, exist_ok=True)
    expressions_path.write_text("\n".join(expressions), encoding="utf-8")
    write_json(blocklist_path, build_blocklist(expressions))

    print(f"账号 Alpha 总数: {len(records)}", flush=True)
    print(f"已提交 Alpha 数: {len(submitted)}", flush=True)
    print(f"已写入: {output_path}", flush=True)
    print(f"已写入: {expressions_path}", flush=True)
    print(f"已写入: {blocklist_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
