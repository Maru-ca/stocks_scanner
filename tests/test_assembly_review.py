"""Review regression tests: real-data bugs found against cached CIK*.json fixtures.

Each test pins one bug from the 2026-09 review of scanner/sec_edgar.py:
  * dna additive double-count (Adobe: DDA 818M + Amort 310M for FY2025),
  * sum-merge collapsing components by END only (mixed 3M/6M periods),
  * sum-merge NaN head when a component starts later (Coke/ADP/Target debt_current),
  * sum-merge leaking later-filed component revisions into earlier rows (Jabil leases),
  * quarters derived from predecessors not yet filed (AMD 2010 comparatives),
  * quarters using stale revisions of the predecessor (AMD restatements),
  * dual 3M+6M tagging resolved by accidental sort order,
  * TTM windows double-counting near-duplicate quarter ends (Best Buy/NVIDIA/Mosaic),
  * IndefiniteLivedIntangibleAssetsNet never resolving (filers use ...ExcludingGoodwill),
  * 53-week fiscal years (371d) vs short transition periods in fiscal_years.
"""
import datetime as dt

from scanner.sec_edgar import (
    durations_all, fiscal_years, instants_all, quarters_all, ttm_series, ttm_value,
)

D = dt.date


def make_facts_multi(tags_to_rows):
    """tags_to_rows: {tag: [(start|None, end, val, filed)]} -> companyfacts-like dict."""
    facts = {"cik": 1, "entityName": "TEST CO", "facts": {"us-gaap": {}}}
    for tag, rows in tags_to_rows.items():
        units = []
        for s, e, v, f in rows:
            r = {"end": e.isoformat(), "val": v, "accn": "a", "fy": 2024, "fp": "Q1",
                 "form": "10-Q", "filed": f.isoformat()}
            if s is not None:
                r["start"] = s.isoformat()
            units.append(r)
        facts["facts"]["us-gaap"][tag] = {"label": "x", "units": {"USD": units}}
    return facts


def make_facts(rows, tag="NetCashProvidedByUsedInOperatingActivities"):
    return make_facts_multi({tag: rows})


def ytd_pattern(base=(10, 11, 12, 13), fy=D(2024, 1, 1)):
    q_ends = [D(2024, 3, 31), D(2024, 6, 30), D(2024, 9, 30), D(2024, 12, 31)]
    filed = [D(2024, 4, 25), D(2024, 7, 25), D(2024, 10, 25), D(2025, 2, 20)]
    rows, cum = [], 0
    for i, (q, v) in enumerate(zip(q_ends, base)):
        cum += v
        rows.append((fy, q, cum, filed[i]))
    return rows


# --- dna: alt semantics (doc 08 §2: Depreciation + Amortization is a FALLBACK) ---

def test_dna_combined_tag_is_not_added_to_pieces():
    # Adobe FY2025 shape: combined DDA line plus its AmortizationOfIntangibleAssets subset
    rows = {
        "DepreciationDepletionAndAmortization": ytd_pattern(base=(200, 220, 230, 240)),
        "AmortizationOfIntangibleAssets": ytd_pattern(base=(80, 83, 81, 62)),
        "Depreciation": ytd_pattern(base=(50, 51, 52, 53)),
    }
    fy = fiscal_years(durations_all(make_facts_multi(rows), "dna"))
    assert set(fy["value"]) == {890.0}  # YTD Q4 cum = 200+220+230+240, pieces NOT added
    q = quarters_all(durations_all(make_facts_multi(rows), "dna"))
    assert [round(v) for v in q["value"]] == [200, 220, 230, 240]  # pieces NOT added


def test_dna_falls_back_to_depreciation_plus_amortization():
    rows = {
        "Depreciation": ytd_pattern(base=(100, 100, 100, 100)),
        "AmortizationOfIntangibleAssets": ytd_pattern(base=(40, 40, 40, 40)),
    }
    q = quarters_all(durations_all(make_facts_multi(rows), "dna"))
    assert [round(v) for v in q["value"]] == [140, 140, 140, 140]
    assert fiscal_years(durations_all(make_facts_multi(rows), "dna"))["value"].iloc[-1] == 560.0


def test_dna_fallback_fills_only_uncovered_periods():
    rows = {
        "DepreciationDepletionAndAmortization": ytd_pattern(base=(200, 220, 230, 240)),  # FY2024
        "Depreciation": [(D(2025, 1, 1), D(2025, 3, 31), 90, D(2025, 4, 25)),
                         (D(2025, 1, 1), D(2025, 6, 30), 180, D(2025, 7, 25))],
        "AmortizationOfIntangibleAssets": [(D(2025, 1, 1), D(2025, 3, 31), 30, D(2025, 4, 25)),
                                           (D(2025, 1, 1), D(2025, 6, 30), 60, D(2025, 7, 25))],
    }
    q = quarters_all(durations_all(make_facts_multi(rows), "dna"))
    by_end = {r.end: round(r.value) for r in q.itertuples()}
    assert by_end[D(2024, 12, 31)] == 240      # combined tag wins where it exists
    assert by_end[D(2025, 3, 31)] == 120       # fallback fills the new year
    assert by_end[D(2025, 6, 30)] == 120


# --- sum-merge: NaN head, point-in-time components, exact-period keying ----------

def test_sum_missing_component_contributes_zero_not_nan():
    # Coke/ADP/Target shape: ShortTermBorrowings first reported long after LTC-current
    rows = {
        "LongTermDebtCurrent": [(None, D(2008, 12, 31), 465, D(2009, 2, 26)),
                                (None, D(2009, 12, 31), 500, D(2010, 2, 25))],
        "ShortTermBorrowings": [(None, D(2010, 12, 31), 100, D(2011, 2, 24))],
    }
    ins = instants_all(make_facts_multi(rows), "debt_current")
    by = {(r.end, r.filed): r.val for r in ins.itertuples()}
    assert by[(D(2008, 12, 31), D(2009, 2, 26))] == 465.0   # was NaN before the fix
    assert by[(D(2010, 12, 31), D(2011, 2, 24))] == 600.0   # LTC holds 500 (step) + STB 100


def test_sum_does_not_leak_later_filed_component_revision():
    # Jabil finance-lease shape: both components restated in a LATER filing
    rows = {
        "FinanceLeaseLiabilityCurrent": [(None, D(2020, 8, 31), 7.465, D(2020, 10, 22)),
                                          (None, D(2020, 8, 31), 7.0, D(2021, 10, 22))],
        "FinanceLeaseLiabilityNoncurrent": [(None, D(2020, 8, 31), 160.747, D(2020, 10, 22)),
                                             (None, D(2020, 8, 31), 161.0, D(2021, 10, 22))],
    }
    ins = instants_all(make_facts_multi(rows), "finance_lease")
    by = {(r.end, r.filed): round(r.val, 6) for r in ins.itertuples()}
    assert by[(D(2020, 8, 31), D(2020, 10, 22))] == 168.212  # original components only
    assert by[(D(2020, 8, 31), D(2021, 10, 22))] == 168.0    # restated components


def test_instant_sum_holds_last_known_visible_component():
    rows = {
        "FinanceLeaseLiabilityCurrent": [(None, D(2024, 3, 31), 10.0, D(2024, 4, 25)),
                                          (None, D(2024, 6, 30), 12.0, D(2024, 7, 25))],
        "FinanceLeaseLiabilityNoncurrent": [(None, D(2024, 3, 31), 90.0, D(2024, 4, 25))],
    }
    ins = instants_all(make_facts_multi(rows), "finance_lease")
    by = {(r.end, r.filed): r.val for r in ins.itertuples()}
    assert by[(D(2024, 6, 30), D(2024, 7, 25))] == 102.0  # holds 90.0 (step rule), no NaN


def test_intangibles_use_excludinggoodwill_indefinite_lived_tag():
    rows = {
        "FiniteLivedIntangibleAssetsNet": [(None, D(2024, 12, 31), 400.0, D(2025, 2, 20))],
        "IndefiniteLivedIntangibleAssetsExcludingGoodwill": [(None, D(2024, 12, 31), 600.0, D(2025, 2, 20))],
    }
    ins = instants_all(make_facts_multi(rows), "intangibles")
    assert list(ins["val"]) == [1000.0]  # IndefiniteLivedIntangibleAssetsNet never resolves


# --- quarters_all: predecessor visibility, revision choice, dual tagging ---------

def test_quarter_predecessor_must_be_visible_when_filed():
    # AMD 2010 shape: Q2-2010 10-Q's 6M YTD exists, but the Q1 YTD fact only appears
    # a year later as a comparative -> no Q2 value may claim visibility at 2010-08-04.
    rows = [
        (D(2009, 12, 27), D(2010, 3, 27), 23, D(2011, 5, 10)),    # filed AFTER Q2's 10-Q
        (D(2009, 12, 27), D(2010, 6, 26), -75, D(2010, 8, 4)),    # 6M YTD
        (D(2009, 12, 27), D(2010, 6, 26), -75, D(2011, 8, 10)),   # re-filed
    ]
    q = quarters_all(durations_all(make_facts(rows), "cfo"))
    by = {(r.end, r.filed): r.value for r in q.itertuples()}
    assert (D(2010, 6, 26), D(2010, 8, 4)) not in by          # future-leak row gone
    assert by[(D(2010, 6, 26), D(2011, 8, 10))] == -98.0      # once visible: -75 - 23


def test_quarter_uses_latest_visible_predecessor_revision():
    rows = [
        (D(2024, 1, 1), D(2024, 3, 31), 10, D(2024, 4, 25)),   # Q1 original
        (D(2024, 1, 1), D(2024, 3, 31), 12, D(2024, 6, 10)),   # Q1 restated before Q2's 10-Q
        (D(2024, 1, 1), D(2024, 6, 30), 25, D(2024, 7, 25)),   # 6M YTD
    ]
    q = quarters_all(durations_all(make_facts(rows), "cfo"))
    by = {(r.end, r.filed): r.value for r in q.itertuples()}
    assert by[(D(2024, 6, 30), D(2024, 7, 25))] == 13.0  # 25 - 12 (restated), not 25 - 10


def test_dual_three_and_six_month_tagging_prefers_ytd_diff():
    # Same 10-Q tags a 3M column AND the 6M YTD; values disagree by 1 -> the
    # YTD-differenced value must win (same basis as Q2 derived from Q1), one row.
    rows = [
        (D(2024, 1, 1), D(2024, 3, 31), 10, D(2024, 4, 25)),
        (D(2024, 1, 1), D(2024, 6, 30), 26, D(2024, 7, 25)),    # 6M YTD -> Q2 = 16
        (D(2024, 4, 1), D(2024, 6, 30), 15, D(2024, 7, 25)),    # 3M column says 15
    ]
    q = quarters_all(durations_all(make_facts(rows), "cfo"))
    q2 = q[q["end"] == D(2024, 6, 30)]
    assert len(q2) == 1 and q2["value"].iloc[0] == 16.0


# --- ttm: near-duplicate quarter ends ---------------------------------------------

def test_ttm_collapses_near_duplicate_quarter_ends():
    # Best Buy/Mosaic/NVIDIA shape: the same fiscal quarter re-tagged with an end a
    # few weeks off. Both rows visible -> TTM must count the quarter ONCE.
    rows = ytd_pattern(base=(10, 11, 12, 13))                  # ends Mar/Jun/Sep/Dec 2024
    rows += [
        # re-tagged duplicate of the Dec-2024 quarter, filed later with an end 28d off
        (D(2024, 1, 1), D(2024, 12, 3), 46, D(2025, 3, 1)),
    ]
    q = quarters_all(durations_all(make_facts(rows), "cfo"))
    s = ttm_series(q, D(2025, 6, 1))
    assert not s.empty
    assert s["value"].iloc[-1] == 46.0  # not 46 + (46-33) double-counted


# --- fiscal_years window -----------------------------------------------------------

def test_fiscal_years_53week_passes_transition_excluded():
    rows = {
        "NetCashProvidedByUsedInOperatingActivities": [
            (D(2023, 1, 1), D(2023, 12, 31), 40.0, D(2024, 2, 20)),   # 364d
            (D(2025, 1, 1), D(2026, 1, 6), 50.0, D(2026, 2, 20)),     # 370d (53-week)
            (D(2024, 1, 1), D(2024, 6, 30), 20.0, D(2024, 7, 25)),    # 181d transition: excluded
        ],
    }
    fy = fiscal_years(durations_all(make_facts_multi(rows), "cfo"))
    assert set(fy["end"]) == {D(2023, 12, 31), D(2026, 1, 6)}
    assert set(fy["value"]) == {40.0, 50.0}


# --- 53-week quarters & Q1 without predecessor (window sanity) --------------------

def test_quarter_gap_window_handles_53week_and_drifted_ends():
    # 52/53-week filer: Q3 ends Sep 28, 98-day Q4 (53-week year) ends Jan 3
    rows = [
        (D(2024, 1, 1), D(2024, 4, 6), 10, D(2024, 4, 25)),
        (D(2024, 1, 1), D(2024, 7, 6), 21, D(2024, 7, 25)),
        (D(2024, 1, 1), D(2024, 9, 28), 33, D(2024, 10, 25)),
        (D(2024, 1, 1), D(2025, 1, 4), 46, D(2025, 2, 20)),    # 98-day gap
    ]
    q = quarters_all(durations_all(make_facts(rows), "cfo"))
    assert [round(v) for v in q["value"]] == [10, 11, 12, 13]


def test_quarters_all_no_derivable_rows_returns_typed_frame():
    # durations exist but none derivable (YTD >130d with no predecessor) -> the
    # empty frame must still carry columns (downstream _as_known indexes by name)
    rows = [(D(2024, 1, 1), D(2024, 12, 31), 40, D(2025, 2, 20))]  # FY only, no chain
    q = quarters_all(durations_all(make_facts(rows), "cfo"))
    assert q.empty and list(q.columns) == ["end", "filed", "value"]
    assert ttm_series(q, D(2025, 6, 1)).empty
