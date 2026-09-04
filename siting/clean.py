"""Cleaning rules declared in the handbooks, applied here.

Every rule reports how many rows it removed so the provenance record can
carry a drop histogram instead of an unexplained shrinking row count.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .provenance import Pull


def _truthy(s: pd.Series) -> pd.Series:
    """WPdx ships booleans as True/False, 'true'/'false' and 'True'/'False'."""
    if s.dtype == bool:
        return s
    return s.astype(str).str.strip().str.lower().isin(["true", "1", "yes", "t"])


def apply_rules(
    df: pd.DataFrame, rules: list[dict[str, Any]], pull: Pull
) -> pd.DataFrame:
    """Run handbook cleaning rules in order, recording every drop."""
    pull.rows_raw = len(df)

    for rule in rules:
        kind = rule["rule"]
        reason = rule.get("reason", kind)
        before = len(df)

        if kind == "require_fields":
            cols = [c for c in rule["fields"] if c in df.columns]
            if cols:
                df = df.dropna(subset=cols)

        elif kind == "drop_where":
            col = rule["field"]
            if col in df.columns:
                df = df[~_truthy(df[col])] if rule["equals"] is True else df[df[col] != rule["equals"]]

        elif kind == "keep_where":
            col = rule["field"]
            if col in df.columns:
                df = df[_truthy(df[col])] if rule["equals"] is True else df[df[col] == rule["equals"]]

        elif kind == "numeric_range":
            col = rule["field"]
            if col in df.columns:
                v = pd.to_numeric(df[col], errors="coerce")
                df = df[v.between(rule["min"], rule["max"])]

        elif kind == "drop_null_island":
            lat = _first_present(df, ["lat_deg", "lat", "latitude"])
            lon = _first_present(df, ["lon_deg", "lon", "longitude"])
            if lat and lon:
                a = pd.to_numeric(df[lat], errors="coerce").abs()
                b = pd.to_numeric(df[lon], errors="coerce").abs()
                df = df[~((a < 1e-6) & (b < 1e-6))]

        elif kind == "max_staleness":
            col = rule["field"]
            if col in df.columns:
                v = pd.to_numeric(df[col], errors="coerce")
                df = df[v.isna() | (v <= rule["max"])]

        elif kind == "dedupe_within_metres":
            df = _dedupe_within(df, float(rule["distance"]))

        elif kind in ("nodata_to_zero", "clip_negative", "nodata_to_max", "make_valid"):
            continue  # raster and geometry rules, handled by their own readers

        else:
            raise ValueError(f"unknown cleaning rule: {kind!r}")

        pull.drop(reason, before - len(df))

    pull.rows_clean = len(df)
    return df.reset_index(drop=True)


def _first_present(df: pd.DataFrame, names: list[str]) -> str | None:
    for n in names:
        if n in df.columns:
            return n
    return None


def _dedupe_within(df: pd.DataFrame, metres: float) -> pd.DataFrame:
    """Grid-snap deduplication. Good enough at 50 m, and it does not need a tree."""
    lat = _first_present(df, ["lat_deg", "lat", "latitude"])
    lon = _first_present(df, ["lon_deg", "lon", "longitude"])
    if not (lat and lon) or df.empty:
        return df
    deg = metres / 111_320.0
    key = (
        (pd.to_numeric(df[lat], errors="coerce") / deg).round().astype("Int64").astype(str)
        + "_"
        + (pd.to_numeric(df[lon], errors="coerce") / deg).round().astype("Int64").astype(str)
    )
    return df[~key.duplicated()]


def haversine_m(lat1, lon1, lat2, lon2):
    """Vectorised great-circle distance in metres."""
    r = 6_371_000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = p2 - p1
    dl = np.radians(lon2) - np.radians(lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
