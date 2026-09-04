"""WPdx+ water points, retrieved through the Socrata API."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from sodapy import Socrata

from .. import handbook
from ..clean import apply_rules
from ..provenance import Ledger, Pull

CACHE = Path("cache")

SELECT = (
    "row_id, lat_deg, lon_deg, status_clean, water_tech_clean, "
    "clean_country_name, clean_adm1, clean_adm2, clean_adm3, "
    "is_urban, is_latest, is_duplicate, staleness_score, "
    "days_since_report, report_date, distance_to_city"
)


def fetch(country: str, adm2: str | None, ledger: Ledger, refresh: bool = False) -> pd.DataFrame:
    """Pull water points for a country, optionally narrowed to one adm2 unit."""
    hb = handbook.load("wpdx")
    q = hb.query

    where = q["where_template"].format(country=country)
    if adm2:
        where += " AND " + q["adm2_filter"].format(adm2=adm2)

    CACHE.mkdir(exist_ok=True)
    slug = f"wpdx_{country}_{adm2 or 'all'}".replace(" ", "-").lower()
    cache_file = CACHE / f"{slug}.parquet"

    pull = Pull(
        source="WPdx+",
        handbook="wpdx",
        endpoint=hb.endpoint,
        query=where,
        licence=hb.licence,
    )

    if cache_file.exists() and not refresh:
        raw = pd.read_parquet(cache_file)
        pull.served_from_cache(cache_file)
    else:
        client = Socrata(q["domain"], None, timeout=120)
        try:
            rows = client.get(q["dataset"], select=SELECT, where=where, limit=q["page_size"])
        finally:
            client.close()
        raw = pd.DataFrame.from_records(rows)
        if not raw.empty:
            raw.to_parquet(cache_file, index=False)
            pull.downloaded(cache_file)

    if raw.empty:
        pull.note = "no records returned"
        ledger.add(pull)
        return raw

    df = apply_rules(raw.copy(), hb.cleaning, pull)
    for col in ("lat_deg", "lon_deg", "distance_to_city", "days_since_report"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = _classify_status(df, hb)
    ledger.add(pull)
    return df


def _classify_status(df: pd.DataFrame, hb) -> pd.DataFrame:
    """status_clean is not two-valued. Uganda alone returns four distinct strings,
    and an equality test against "Functional" scores every point as broken."""
    sem = hb.raw.get("status_semantics", {})
    serving = {s.strip().lower() for s in sem.get("serving", ["Functional"])}
    s = df["status_clean"].astype(str).str.strip().str.lower()
    df["serving"] = s.isin(serving)
    df["at_risk"] = s.eq("functional, needs repair")
    unknown = ~s.isin(serving | {x.strip().lower() for x in sem.get("not_serving", [])})
    if unknown.any():
        df.loc[unknown, "serving"] = False
    df["status_unrecognised"] = unknown
    return df


def adm2_summary(country: str, ledger: Ledger, top: int = 25) -> pd.DataFrame:
    """Which districts have enough points to be worth running. Cheap scouting call."""
    hb = handbook.load("wpdx")
    q = hb.query
    client = Socrata(q["domain"], None, timeout=120)
    try:
        rows = client.get(
            q["dataset"],
            select="clean_adm2, count(*)",
            where=q["where_template"].format(country=country) + " AND is_latest = true",
            group="clean_adm2",
            order="count_1 DESC",
            limit=top,
        )
    finally:
        client.close()
    df = pd.DataFrame.from_records(rows)
    if not df.empty:
        df["count_1"] = pd.to_numeric(df["count_1"], errors="coerce")
    return df
