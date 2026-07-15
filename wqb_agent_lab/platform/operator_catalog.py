from __future__ import annotations

import json
from pathlib import Path


DEFAULT_OPERATOR_NAMES = frozenset(
    {
        "abs",
        "add",
        "and",
        "bucket",
        "days_from_last_change",
        "densify",
        "divide",
        "equal",
        "greater",
        "greater_equal",
        "group_backfill",
        "group_mean",
        "group_neutralize",
        "group_rank",
        "group_scale",
        "group_zscore",
        "hump",
        "if_else",
        "inverse",
        "is_nan",
        "kth_element",
        "last_diff_value",
        "less",
        "less_equal",
        "log",
        "max",
        "min",
        "multiply",
        "normalize",
        "not",
        "not_equal",
        "or",
        "power",
        "quantile",
        "rank",
        "reverse",
        "scale",
        "sign",
        "signed_power",
        "sqrt",
        "subtract",
        "trade_when",
        "ts_arg_max",
        "ts_arg_min",
        "ts_av_diff",
        "ts_backfill",
        "ts_corr",
        "ts_count_nans",
        "ts_covariance",
        "ts_decay_linear",
        "ts_delay",
        "ts_delta",
        "ts_mean",
        "ts_product",
        "ts_quantile",
        "ts_rank",
        "ts_regression",
        "ts_scale",
        "ts_std_dev",
        "ts_step",
        "ts_sum",
        "ts_zscore",
        "vec_avg",
        "vec_sum",
        "winsorize",
        "zscore",
    }
)


def load_operator_names(catalog_path: Path | str | None = None) -> frozenset[str]:
    path = Path(catalog_path) if catalog_path is not None else Path(__file__).with_name("resources") / "operators.json"
    if not path.exists():
        return DEFAULT_OPERATOR_NAMES
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("operators", []) if isinstance(payload, dict) else payload
    names = {
        str(row.get("name") or row.get("id"))
        for row in rows
        if isinstance(row, dict) and (row.get("name") or row.get("id"))
    }
    return frozenset(names) if names else DEFAULT_OPERATOR_NAMES
