"""Divergence flags — doc 05 §3 (frozen v0.4). Every flag stores its evidence so the
UI can always answer "why is this name on the list?".
"""
from __future__ import annotations

import json

import pandas as pd

from . import config


def _val(row, key):
    """Scalar value or None for missing/NaN — never let NaN compare truthy."""
    v = row.get(key)
    return None if v is None or pd.isna(v) else v


def _arm_conditions(row, gs10: float | None) -> dict:
    """derated_quality arms, evaluated on one row. Returns dict of arm -> bool/value."""
    self_pct = _val(row, "ev_fcf_self_pct")
    self_z = _val(row, "ev_fcf_self_z")
    self_ratio = _val(row, "ev_fcf_self_ratio")
    arms = {
        "self_pct": self_pct is not None and self_pct <= config.FLAG_SELF_PCT,
        "self_z": self_z is not None and self_z <= config.FLAG_SELF_Z,
        "median_ratio": self_ratio is not None and self_ratio <= config.FLAG_MEDIAN_RATIO,
    }
    y = _val(row, "fcf_yield")
    yield_threshold = max(config.FLAG_YIELD_FLOOR, gs10 or 0.0)
    yield_arm = y is not None and y >= yield_threshold
    rp = _val(row, "residual_pct")
    residual_arm = rp is not None and rp <= config.FLAG_RESIDUAL_PCT

    def ge(key, thr):   # hard-AND brake: missing/NaN margin data must fail, not pass
        v = _val(row, key)
        return v is not None and v >= thr

    brakes = {
        "gm": ge("brake_gm", config.FLAG_BRAKE_GM),
        "fcf_margin": ge("brake_fcf_margin", config.FLAG_BRAKE_FCF_MARGIN),
        "roic": ge("brake_roic", config.FLAG_BRAKE_ROIC),
    }
    return {
        "depressed": arms,
        "yield_arm": bool(yield_arm),
        "residual_arm": bool(residual_arm),
        "brakes": brakes,
        "yield_threshold": yield_threshold,
    }


def derated_quality(row, gs10: float | None) -> tuple[bool, dict]:
    ev = _arm_conditions(row, gs10)
    floor_v = row.get("quality_floor_pass")
    floor = floor_v is not None and not pd.isna(floor_v) and bool(floor_v)
    depressed = any(ev["depressed"].values())
    brakes_ok = all(ev["brakes"].values())
    fired = floor and depressed and (ev["yield_arm"] or ev["residual_arm"]) and brakes_ok
    evidence = {
        "floor": floor,
        "depressed_arms": {k: bool(v) for k, v in ev["depressed"].items()},
        "yield_arm": ev["yield_arm"],
        "residual_arm": ev["residual_arm"],
        "brakes": {k: bool(v) for k, v in ev["brakes"].items()},
        "self_pct": _val(row, "ev_fcf_self_pct"),
        "self_z": _val(row, "ev_fcf_self_z"),
        "median_ratio": _val(row, "ev_fcf_self_ratio"),
        "fcf_yield": _val(row, "fcf_yield"),
        "yield_threshold": ev["yield_threshold"],   # max(4%, GS10) as of the scan date
        "gs10": gs10,
        "residual_pct": _val(row, "residual_pct"),
        "brake_values": {
            "gm": _val(row, "brake_gm"),
            "fcf_margin": _val(row, "brake_fcf_margin"),
            "roic": _val(row, "brake_roic"),
        },
    }
    return fired, evidence


def beaten_but_delivering(row) -> tuple[bool, dict]:
    floor_v = row.get("quality_floor_pass")
    floor = floor_v is not None and not pd.isna(floor_v) and bool(floor_v)
    mom_pct = _val(row, "pct_mom_sector")
    mom_ok = mom_pct is not None and mom_pct <= config.MOM_SECTOR_PCT
    breadth = _val(row, "eps_rev_proxy")
    breadth_ok = breadth is not None and breadth > 0
    evidence = {
        "floor": floor,
        "mom_sector_pct": mom_pct,
        "eps_rev_proxy": breadth,
    }
    return (floor and mom_ok and breadth_ok), evidence


def apply_flags(df: pd.DataFrame, gs10: float | None) -> pd.DataFrame:
    flags, evidences = [], []
    for _, row in df.iterrows():
        fired, ev = derated_quality(row, gs10)
        flags.append(fired)
        evidences.append(ev)
    df["flag_derated_quality"] = flags
    df["evidence_derated"] = [json.dumps(e) for e in evidences]

    b_flags, b_ev = [], []
    for _, row in df.iterrows():
        fired, ev = beaten_but_delivering(row)
        b_flags.append(fired)
        b_ev.append(ev)
    df["flag_beaten_but_delivering"] = b_flags
    df["evidence_beaten"] = [json.dumps(e) for e in b_ev]
    return df
