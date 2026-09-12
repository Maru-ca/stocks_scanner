"""Owner-approved v0.5.2 coverage fixes: GM derived from cost-of-revenue, and
fcf_adj = fcf when SBC is never tagged. Both must carry dq flags, never be silent."""
import datetime as dt

import pandas as pd

from scanner.features import _fy_features, _ttm_timeline
from scanner.sec_edgar import durations_all

D = dt.date


def make_facts(rows_by_tag):
    """rows_by_tag: {tag: [(start, end, val, filed), ...]} under us-gaap/USD."""
    return {"facts": {"us-gaap": {
        tag: {"label": "x", "units": {"USD": [
            {"start": s.isoformat(), "end": e.isoformat(), "val": v, "accn": "a",
             "fy": 2024, "fp": "Q1", "form": "10-Q", "filed": f.isoformat()}
            for s, e, v, f in rows
        ]}} for tag, rows in rows_by_tag.items()
    }}}


def _fy(tag, vals, year=2024):
    """Annual 12-month facts ending Dec of successive years (vals newest-first)."""
    return [(D(year - i, 1, 1), D(year - i, 12, 31), v, D(year - i + 1, 2, 15))
            for i, v in enumerate(vals)]


def test_gm_derived_from_cost_of_revenue():
    facts = make_facts({
        "RevenueFromContractWithCustomerExcludingAssessedTax": _fy(2024, [80, 90, 100, 110, 120]),
        "CostOfGoodsAndServicesSold": _fy(2024, [50, 55, 60, 66, 72]),
    })
    F, _ = _fy_features(facts, D(2026, 9, 12))
    assert "gm" in F.columns and F["gm"].notna().any()
    recent = F.sort_index()["gm"].dropna().iloc[-1]
    assert abs(recent - (80 - 50) / 80) < 1e-9             # (revenue - cost)/revenue
    assert "gross_profit" not in F.columns                 # truly the derived path


def test_gm_prefers_reported_gross_profit():
    facts = make_facts({
        "RevenueFromContractWithCustomerExcludingAssessedTax": _fy(2024, [100, 110, 120]),
        "CostOfGoodsAndServicesSold": _fy(2024, [60, 66, 72]),
        "GrossProfit": _fy(2024, [45, 50, 55]),           # differs from rev-cost on purpose
    })
    F, _ = _fy_features(facts, D(2026, 9, 12))
    assert abs(F.sort_index()["gm"].dropna().iloc[-1] - 45 / 100) < 1e-9   # reported wins


def test_fcf_adj_falls_back_to_fcf_when_sbc_untagged():
    # four quarters of standalone 3-month facts (no YTD chains needed)
    q = [(D(2025, 1, 1), D(2025, 3, 31), 10, D(2025, 4, 25)),
         (D(2025, 4, 1), D(2025, 6, 30), 11, D(2025, 7, 25)),
         (D(2025, 7, 1), D(2025, 9, 30), 12, D(2025, 10, 25)),
         (D(2025, 10, 1), D(2025, 12, 31), 13, D(2026, 2, 20))]
    capex = [(s, e, 1, f) for s, e, _, f in q]
    facts = make_facts({
        "NetCashProvidedByUsedInOperatingActivities": q,
        "PaymentsToAcquirePropertyPlantAndEquipment": capex,
        # no ShareBasedCompensation at all
    })
    tl = _ttm_timeline(facts, D(2026, 9, 12))
    last = tl[tl["fcf_adj"].notna()].iloc[-1]
    assert abs(last["fcf_adj"] - (10 + 11 + 12 + 13 - 4)) < 1e-9    # == plain fcf


def test_fcf_adj_still_subtracts_sbc_when_tagged():
    q = [(D(2025, 1, 1), D(2025, 3, 31), 10, D(2025, 4, 25)),
         (D(2025, 4, 1), D(2025, 6, 30), 11, D(2025, 7, 25)),
         (D(2025, 7, 1), D(2025, 9, 30), 12, D(2025, 10, 25)),
         (D(2025, 10, 1), D(2025, 12, 31), 13, D(2026, 2, 20))]
    capex = [(s, e, 1, f) for s, e, _, f in q]
    sbc = [(s, e, 2, f) for s, e, _, f in q]
    facts = make_facts({
        "NetCashProvidedByUsedInOperatingActivities": q,
        "PaymentsToAcquirePropertyPlantAndEquipment": capex,
        "ShareBasedCompensation": sbc,
    })
    tl = _ttm_timeline(facts, D(2026, 9, 12))
    last = tl[tl["fcf_adj"].notna()].iloc[-1]
    assert abs(last["fcf_adj"] - (10 + 11 + 12 + 13 - 4 - 8)) < 1e-9
