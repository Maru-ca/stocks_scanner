"""Rates: FRED DGS10 (daily 10Y Treasury) — the nightly scan's rate anchor.

Doc 08 §1: DGS10, not the monthly GS10; last observation carried on holidays.
FRED being unreachable must never kill the scan: we serve the last cached series
(carry-forward) or None.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import requests

from . import config


def _read_cached() -> pd.DataFrame:
    try:
        df = pd.read_csv(config.GS10_FILE)
    except Exception:
        return pd.DataFrame()
    if "dgs10" not in df.columns:
        return pd.DataFrame()
    df["dgs10"] = pd.to_numeric(df["dgs10"], errors="coerce")
    return df.dropna(subset=["dgs10"])


def fetch_dgs10(refresh: bool = False) -> pd.DataFrame:
    if not refresh and config.GS10_FILE.exists():
        df = _read_cached()
        if len(df) > 100:
            return df
    try:
        csv = requests.get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10", timeout=30
        ).text
        df = pd.read_csv(pd.io.common.StringIO(csv))
        df.columns = ["date", "dgs10"]
        df["dgs10"] = pd.to_numeric(df["dgs10"], errors="coerce")
        df = df.dropna()
    except Exception:
        # FRED unreachable (or the payload unparseable): a short/stale cache beats no
        # rate anchor — serve it and let the caller carry the last observation forward
        cached = _read_cached()
        if len(cached):
            return cached
        return pd.DataFrame(columns=["date", "dgs10"])
    if df.empty:
        cached = _read_cached()
        if len(cached):
            return cached
    df.to_csv(config.GS10_FILE, index=False)
    return df


def gs10_asof(asof: dt.date, refresh: bool = False) -> float | None:
    """Latest DGS10 observation on or before `asof` (carry-forward, percent -> fraction).
    Never raises: None when the series can't be served, so the scan continues without
    the rate anchor (flags fall back to the fixed 4% yield floor)."""
    try:
        df = fetch_dgs10(refresh)
    except Exception:
        return None
    if df.empty or "dgs10" not in df.columns:
        return None
    dates = pd.to_datetime(df["date"]).dt.date
    val = df.loc[dates <= asof, "dgs10"].dropna()
    if val.empty:
        return None
    return float(val.iloc[-1]) / 100.0
