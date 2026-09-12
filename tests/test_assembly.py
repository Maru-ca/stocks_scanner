"""Assembly tests: YTD differencing, TTM, restatement dedupe, visibility (doc 08 §3)."""
import datetime as dt

from scanner.sec_edgar import (
    durations_all, fiscal_years, instant_asof, quarters_all, ttm_series, ttm_value,
)

D = dt.date


def make_facts(rows, tag="NetCashProvidedByUsedInOperatingActivities"):
    """rows: (start, end, val, filed) for a single duration tag."""
    return {"cik": 1, "entityName": "TEST CO", "facts": {"us-gaap": {
        tag: {"label": "x", "units": {"USD": [
            {"start": s.isoformat(), "end": e.isoformat(), "val": v, "accn": "a",
             "fy": 2024, "fp": "Q1", "form": "10-Q", "filed": f.isoformat()} for s, e, v, f in rows
        ]}}
    }}}


def ytd_pattern(base=(10, 11, 12, 13)):
    """One fiscal year of CUMULATIVE YTD facts (all starting at the FY start)."""
    q_ends = [D(2024, 3, 31), D(2024, 6, 30), D(2024, 9, 30), D(2024, 12, 31)]
    filed = [D(2024, 4, 25), D(2024, 7, 25), D(2024, 10, 25), D(2025, 2, 20)]
    rows, cum = [], 0
    for i, (q, v) in enumerate(zip(q_ends, base)):
        cum += v
        rows.append((D(2024, 1, 1), q, cum, filed[i]))  # YTD: start = FY start
    return rows


def test_ytd_differencing():
    facts = make_facts(ytd_pattern())  # YTD 10, 21, 33, 46 -> quarters 10,11,12,13
    q = quarters_all(durations_all(facts, "cfo"))
    assert len(q) == 4
    vals = [round(v, 6) for v in q["value"]]
    assert vals == [10, 11, 12, 13]


def test_standalone_quarter_passes_through():
    rows = [
        (D(2024, 1, 1), D(2024, 3, 31), 10, D(2024, 4, 25)),
        (D(2024, 4, 1), D(2024, 6, 30), 11, D(2024, 7, 25)),
    ]
    q = quarters_all(durations_all(make_facts(rows), "cfo"))
    assert list(q["value"]) == [10.0, 11.0]


def test_restatement_latest_filed_wins():
    rows = ytd_pattern() + [
        (D(2024, 1, 1), D(2024, 12, 31), 47, D(2025, 4, 10))  # FY restated 46 -> 47
    ]
    facts = make_facts(rows)
    asof = D(2025, 6, 1)
    assert ttm_value(quarters_all(durations_all(facts, "cfo")), asof) == 47.0
    # before the restatement was filed, the scan still sees 46
    assert ttm_value(quarters_all(durations_all(facts, "cfo")), D(2025, 3, 31)) == 46.0


def test_ttm_known_from():
    q = quarters_all(durations_all(make_facts(ytd_pattern()), "cfo"))
    s = ttm_series(q, D(2025, 6, 1))
    assert len(s) == 1
    # TTM of the full FY is only visible when the 10-K is filed (2025-02-20)
    assert s["known_from"].iloc[0] == D(2025, 2, 20)
    assert s["value"].iloc[0] == 46.0


def test_fiscal_years_from_12mo_durations():
    rows = [(D(2023, 1, 1), D(2023, 12, 31), 40, D(2024, 2, 20))] + ytd_pattern()
    facts = make_facts(rows)
    fy = fiscal_years(durations_all(facts, "cfo"))
    assert len(fy) == 2
    assert set(fy["value"]) == {40.0, 46.0}


def test_instant_asof_respects_filed():
    rows = [
        (D(2024, 3, 31), 100, D(2024, 4, 25)),
        (D(2024, 6, 30), 110, D(2024, 7, 25)),
        (D(2024, 6, 30), 105, D(2024, 8, 15)),  # revision of Q2
    ]
    inst = pd_instants(rows)
    end, val = instant_asof(inst, D(2024, 8, 1))
    assert (end, val) == (D(2024, 6, 30), 110.0)  # revision not visible yet
    end, val = instant_asof(inst, D(2024, 9, 1))
    assert val == 105.0  # revision visible


def pd_instants(rows):
    import pandas as pd
    df = pd.DataFrame(rows, columns=["end", "val", "filed"])
    return df
