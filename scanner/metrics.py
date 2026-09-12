"""Canonical formula layer — doc 06 (single source of truth, implemented).

Pure functions only; no fetching, no I/O. Direction conventions:
higher_better scores map to 0–100 with 100 = best.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config


def safe_div(a, b) -> float | None:
    if a is None or b is None or b == 0 or not np.isfinite(a) or not np.isfinite(b):
        return None
    return float(a) / float(b)


def ev(mktcap, debt, pref, mi, cash, sti) -> float | None:
    parts = [mktcap, debt, pref, mi, cash, sti]
    if any(p is None for p in parts):
        return None
    return float(mktcap + debt + pref + mi - cash - sti)  # doc 06 §4


def invested_capital(debt, pref, mi, equity, cash, sti) -> float | None:
    parts = [debt, pref, mi, equity, cash, sti]
    if any(p is None for p in parts):
        return None
    return float(debt + pref + mi + equity - cash - sti)  # doc 06 §5


def t_eff_winsorized(tax, pretax, fallback: float | None = None) -> float | None:
    """t_eff = tax/pretax, band [0%, 40%] (doc 06 §6). A value outside the band is an
    artifact (one-off charges, zero provision) -> 3y average fallback; still
    undefined -> None. Never silently clipped: we adjust explicitly or not at all."""
    if tax is None or pretax is None or pretax <= 0:
        return fallback
    t = tax / pretax
    if not np.isfinite(t) or t <= config.TEFF_MIN or t > config.TEFF_MAX:
        return fallback
    return float(t)


def nopat(ebit_val, t_eff) -> float | None:
    if ebit_val is None or t_eff is None:
        return None
    return float(ebit_val * (1.0 - t_eff))


def fcf(cfo, capex) -> float | None:
    if cfo is None or capex is None:
        return None
    return float(cfo - capex)


def fcf_adj(cfo, capex, sbc) -> float | None:
    base = fcf(cfo, capex)
    return None if base is None or sbc is None else float(base - sbc)


def ebitda(ebit_val, dna) -> float | None:
    if ebit_val is None or dna is None:
        return None
    return float(ebit_val + dna)


def cagr(cur, base, cur_rev: float | None = None, years: int = 5) -> float | None:
    """(cur/base)^(1/n)−1; null if base <= 0 or |base| < 5% of current revenue (doc 06 §8)."""
    if cur is None or base is None or base <= 0 or cur <= 0:
        return None
    if cur_rev is not None and abs(base) < config.CAGR_BASE_FLOOR * abs(cur_rev):
        return None
    return float((cur / base) ** (1.0 / years) - 1.0)


def winsor_pct_rank(s: pd.Series, higher_better: bool = True) -> pd.Series:
    """Winsorize at 1/99 within the group, then percentile rank 0–100 (avg ties).
    Direction normalized so 100 = best (doc 06 §9)."""
    s = pd.to_numeric(s, errors="coerce")
    lo, hi = s.quantile(0.01), s.quantile(0.99)
    if pd.notna(lo) and pd.notna(hi) and lo != hi:
        s = s.clip(lo, hi)
    pct = s.rank(pct=True, method="average") * 100.0
    return pct if higher_better else 100.0 - pct


def fixed_scale_yield(y: float | None) -> float | None:
    """fcf_yield -> clip(0,10%)/10% -> 0–100. Absolute metrics are NEVER cross-sectionally
    ranked (doc 06 §9)."""
    if y is None or not np.isfinite(y):
        return None
    return float(np.clip(y, 0.0, config.FCF_YIELD_SCALE_MAX) / config.FCF_YIELD_SCALE_MAX * 100.0)


def self_stats(series: pd.Series) -> dict | None:
    """SELF percentile / median-centered z / median-ratio + validity rules
    (docs 02, 06 §9): >= 3y of valid observations AND >= 60% of the window valid,
    else null.

    `series`: the stock's own trailing-window observations of the multiple
    INCLUDING invalid weeks (NaN) — valid observations form the distribution,
    the full window length is the validity denominator. The current value is
    the LAST observation of the window and must itself be valid (an invalid
    current multiple has no rank).

    Returns pct of the current value within its valid window (0 = cheapest
    ever; ties -> average rank, doc 06 §9), median-centered z, median-ratio.
    """
    s_full = pd.to_numeric(series, errors="coerce")
    if s_full.size == 0 or pd.isna(s_full.iloc[-1]):
        return None
    n_window = int(s_full.size)
    s = s_full.dropna()
    n_valid = int(len(s))
    if n_valid < int(config.SELF_MIN_HISTORY_YEARS * 52):
        return None
    if n_valid < config.SELF_MIN_VALID_FRAC * n_window:
        return None
    cur = float(s.iloc[-1])
    med = float(s.median())
    std = float(s.std(ddof=0))
    pct = float(s.rank(pct=True, method="average").iloc[-1] * 100.0)
    return {
        "pct": pct,
        "z": float((cur - med) / std) if std > 0 else None,
        "median_ratio": float(cur / med) if med > 0 else None,
        "n_valid": n_valid,
        "n_window": n_window,
    }
