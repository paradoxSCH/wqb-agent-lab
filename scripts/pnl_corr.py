from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import pandas as pd

from src.config import load_config
from src.session import create_session


PNL_URL = "https://api.worldquantbrain.com/alphas/{}/recordsets/pnl"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def normalize_title(title: str) -> str:
    return " ".join(str(title or "PnL").split()) or "PnL"


def alpha_ids_from_scan(path: Path, passes_only: bool) -> list[str]:
    rows = read_json(path)
    alpha_ids: list[str] = []
    for row in rows if isinstance(rows, list) else []:
        alpha_id = str(row.get("alpha_id", "") or "")
        if not alpha_id:
            continue
        if passes_only:
            checks = row.get("checks") or []
            if any(str(check.get("result", "")).upper() in {"FAIL", "ERROR"} for check in checks):
                continue
        alpha_ids.append(alpha_id)
    return alpha_ids


def parse_alpha_ids(args: argparse.Namespace) -> list[str]:
    alpha_ids: list[str] = []
    if args.from_scan:
        alpha_ids.extend(alpha_ids_from_scan(Path(args.from_scan), args.passes_only))
    for value in args.alpha_ids or []:
        alpha_ids.extend(part.strip() for part in value.split(",") if part.strip())
    seen: set[str] = set()
    deduped: list[str] = []
    for alpha_id in alpha_ids:
        if alpha_id not in seen:
            seen.add(alpha_id)
            deduped.append(alpha_id)
    return deduped


def recordsets_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        recordsets: list[dict[str, Any]] = []
        for item in payload:
            recordsets.extend(recordsets_from_payload(item))
        return recordsets
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("records"), list):
        return [payload]
    for key in ("recordsets", "results", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return recordsets_from_payload(value)
    return []


def recordset_to_frame(alpha_id: str, recordset: dict[str, Any]) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    schema = recordset.get("schema") or {}
    title = normalize_title(recordset.get("title") or schema.get("title") or recordset.get("name") or "PnL")
    records = recordset.get("records") or []
    meta = {"alpha_id": alpha_id, "title": title, "records": len(records)}
    if not records:
        meta["error"] = "empty records"
        return None, meta

    properties = schema.get("properties") or []
    columns = [str(item.get("name") or item.get("title") or "") for item in properties if isinstance(item, dict)]
    if not columns and isinstance(records[0], (list, tuple)):
        columns = ["date", "pnl"] if len(records[0]) == 2 else [f"value_{index}" for index in range(len(records[0]))]

    frame = pd.DataFrame(records, columns=columns or None)
    if "date" not in frame.columns:
        frame = frame.rename(columns={frame.columns[0]: "date"})
    value_columns = [column for column in frame.columns if column != "date"]
    if not value_columns:
        meta["error"] = "no value column"
        return None, meta

    value_column = "pnl" if "pnl" in value_columns else value_columns[0]
    output_column = f"{alpha_id}:{title}"
    frame = frame[["date", value_column]].rename(columns={value_column: output_column})
    frame["date"] = pd.to_datetime(frame["date"])
    frame[output_column] = pd.to_numeric(frame[output_column], errors="coerce")
    return frame.set_index("date"), meta


def fetch_alpha_recordsets(session: Any, alpha_id: str, retries: int, retry_wait: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    url = PNL_URL.format(alpha_id)
    attempts = max(1, retries + 1)
    last_meta: dict[str, Any] = {"alpha_id": alpha_id}
    for attempt in range(1, attempts + 1):
        response = session.get(url)
        text = response.text or ""
        last_meta = {
            "alpha_id": alpha_id,
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type"),
            "content_length": len(text),
            "attempt": attempt,
        }
        if response.ok and text.strip():
            try:
                return recordsets_from_payload(response.json()), last_meta
            except ValueError as exc:
                last_meta["error"] = f"json decode error: {exc}"
                return [], last_meta
        last_meta["error"] = f"HTTP {response.status_code}; empty={not bool(text.strip())}; prefix={text[:160]}"
        if attempt < attempts and retry_wait > 0:
            time.sleep(retry_wait)
    return [], last_meta


def build_pnl_dataframe(alpha_ids: list[str], titles: set[str], retries: int, retry_wait: float) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    session = create_session(load_config())
    frames: list[pd.DataFrame] = []
    meta: list[dict[str, Any]] = []
    seen_columns: set[str] = set()
    for alpha_id in alpha_ids:
        recordsets, fetch_meta = fetch_alpha_recordsets(session, alpha_id, retries, retry_wait)
        if not recordsets:
            meta.append(fetch_meta)
            continue
        for recordset in recordsets:
            frame, record_meta = recordset_to_frame(alpha_id, recordset)
            record_meta.update(fetch_meta)
            if titles and normalize_title(record_meta.get("title", "")) not in titles:
                record_meta["skipped"] = "title filter"
                meta.append(record_meta)
                continue
            if frame is None:
                meta.append(record_meta)
                continue
            column = str(frame.columns[0])
            if column in seen_columns:
                suffix = 2
                new_column = f"{column}#{suffix}"
                while new_column in seen_columns:
                    suffix += 1
                    new_column = f"{column}#{suffix}"
                frame = frame.rename(columns={column: new_column})
                record_meta["column"] = new_column
            else:
                record_meta["column"] = column
            seen_columns.add(str(frame.columns[0]))
            frames.append(frame)
            meta.append(record_meta)
    if not frames:
        return pd.DataFrame(), meta
    return pd.concat(frames, axis=1).sort_index(), meta


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch BRAIN PnL recordsets and compute local DataFrame correlations.")
    parser.add_argument("alpha_ids", nargs="*", help="Alpha ids, space-separated or comma-separated.")
    parser.add_argument("--from-scan", help="Read alpha ids from a scan result JSON file.")
    parser.add_argument("--passes-only", action="store_true", help="When using --from-scan, keep only rows without FAIL/ERROR checks.")
    parser.add_argument("--titles", default="", help="Comma-separated recordset titles to keep, e.g. PnL,Investability Constrained PnL.")
    parser.add_argument("--output-dir", default=".local/data/reports/pnl-corr", help="Directory for pnl_dataframe.csv, pnl_corr.csv and metadata.")
    parser.add_argument("--retries", type=int, default=1, help="Retries for empty/temporary responses.")
    parser.add_argument("--retry-wait", type=float, default=3.0, help="Seconds between retries.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    alpha_ids = parse_alpha_ids(args)
    if not alpha_ids:
        raise SystemExit("No alpha ids provided. Use positional ids or --from-scan.")
    titles = {normalize_title(title) for title in args.titles.split(",") if title.strip()}
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    pnl_df, meta = build_pnl_dataframe(alpha_ids, titles, args.retries, args.retry_wait)
    write_json(output_dir / "pnl_corr_meta.json", {
        "alpha_ids": alpha_ids,
        "titles": sorted(titles),
        "dataframe_shape": list(pnl_df.shape),
        "metadata": meta,
    })
    if pnl_df.empty:
        print("No usable PnL recordsets fetched.")
        return 1

    corr = pnl_df.corr()
    pnl_df.to_csv(output_dir / "pnl_dataframe.csv")
    corr.to_csv(output_dir / "pnl_corr.csv")

    print(f"DataFrame shape: {pnl_df.shape}")
    print("Non-null counts:")
    print(pnl_df.notna().sum().to_string())
    print(f"Correlation shape: {corr.shape}")
    print(corr.round(4).to_string())
    print(f"Wrote {output_dir / 'pnl_dataframe.csv'}")
    print(f"Wrote {output_dir / 'pnl_corr.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())