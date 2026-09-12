# 01 — Universe & Data Sources

Version 0.5 (2026-09-11). Search methodology frozen (v0.4). v0.5: source map pinned to the doc 08 fetch contract (DGS10, FMP estimates, FX/earnings/options rows); Q2 and pre-tracking wording fixed.

## Universe

- **Base universe**: current S&P 500 constituents (~500 tickers, includes the 3 share-class duplicates).
- **Source**: Wikipedia "List of S&P 500 companies" table (stable, free, includes GICS sector and sub-industry, CIK, and date added).
- **Refresh**: weekly pull. We store index membership history with dates, so scans can be
  reproduced later and churn (additions/deletions) is visible rather than silent.
- **Sector classification**: GICS sector + sub-industry from the same table. Wikipedia
  has **no industry-group column**, and the peer ladder (docs 03/05) and the residual's
  dummies need it — so a static sub-industry → industry-group map is maintained in-repo
  (the GICS taxonomy is stable; reviewed on GICS reclassification announcements).
- **Share classes**: the Wikipedia list contains duplicate rows for dual-class companies
  (GOOGL/GOOG, FOX/FOXA…). One *company* per CIK: market cap, EV and all fundamentals
  aggregate all classes; the UI shows one primary ticker, chosen as the most liquid class
  (`adv_usd`). Definitions: doc 06 §1.

## Sector handling

Standard ratio analysis (ROIC, EV/EBIT, net debt/EBITDA) breaks for financials,
utilities and real estate. Split treatment:

| Group | Sectors (GICS) | Treatment |
|---|---|---|
| Standard | everything except below | full filter catalog applies |
| Restricted | Financials (40), Utilities (55), Real Estate (60) | restricted set: P/E, P/B, dividend yield, momentum, analyst filters. No ROIC/EV-multiples/net-debt ratios. |

**MVP decision (decided, Q2)**: exclude the restricted group entirely (~380 remaining
tickers). The restricted filter set is P1, not MVP.

## Data sources map

| Data | Source | Cost | Refresh | Notes |
|---|---|---|---|---|
| Constituents + GICS | Wikipedia | free | weekly | parse table, diff against stored history |
| Prices, volume, market cap, 52w range | yfinance | free | daily EOD | also beta, basic multiples (patchy quality) |
| Fundamentals, 10y history | SEC EDGAR XBRL `companyfacts` API | free | quarterly, as-reported | authoritative; we compute ratios ourselves. CIK available from Wikipedia table |
| Fundamentals (convenience alternative) | Financial Modeling Prep / SimFin | freemium | daily | pre-normalized ratios; free tiers rate-limited. Decided (Q3): SEC computes every ratio; FMP free tier only for analyst estimates / short interest |
| Analyst estimates, revisions, targets | FMP free tier (decided, Q3) | freemium | nightly consensus snapshot, vintaged (08 §4) | revision proxy derived from our own snapshot history; true revision counts need paid data |
| 10Y Treasury yield | FRED **`DGS10`** (daily; not the monthly GS10) | free | daily | last observation carried on holidays; the nightly scan needs the daily series |
| FX rates | FRED `DEX*` daily series per currency | free | as needed | report-date conversion for non-USD reporters (08 §1/§4) |
| Earnings dates | yfinance calendar | free | nightly | patchy; unknown → null, badge suppressed (08 §4) |
| Options chain | yfinance `option_chain` | free | nightly | display-only overlay; ATM + expiry rule pinned in 08 §4 |
| Short interest | yfinance / exchange bi-weekly reports | free | bi-weekly | lags by design; treat as slow signal |

## Data rules

- **As-of discipline**: every scan stores the data snapshot it ran on. A scan dated T must
  only use data published before T (no look-ahead). This is what makes Phase 4 evaluation honest.
- **Report-date rule (the look-ahead killer)**: a fiscal quarter is usable by a scan only
  after its **filing date** (10-Q/10-K/8-K), not its period-end date — report lag runs
  3–10 weeks. All TTM construction follows this rule (doc 06 §3).
- **Fundamentals cadence**: quarterly as-reported, converted to TTM for all ratios.
- **Currency**: the few non-USD reporters in the index are converted to USD at the
  report-date exchange rate; all metrics are USD.
- **TTM construction**: sum of last 4 quarters; never mix TTM prices with annual fundamentals.
- **Restatements**: decided (Q6) — live scans use latest available figures; the
  evaluation harness runs on vintaged snapshots only and never presents
  backfilled-restated history as point-in-time (05 §5).
- **Missing data**: a metric that can't be computed (negative EBIT, missing XBRL tag, etc.)
  is `null` — the stock is **excluded from that filter/score component**, never coerced to 0
  or a neutral value. Every score reports its own coverage.
- **Caching**: raw API responses cached raw (never re-fetch to recompute); derived metrics
  stored separately so methodology can change without re-hitting APIs. Rate limits, not
  compute, are the binding constraint (~500 tickers × multiple endpoints).

## Data-quality checks

Every nightly load runs sanity checks; a failure quarantines the field (null + flag)
rather than silently poisoning scores:

- market cap within a sane band vs. the last stored value (jump > 2× → flag; usually a
  bad share count or a missed split adjustment)
- share-count change > 20% quarter-over-quarter → flag (split or issuance; verify before use)
- EV > 0 and invested capital > 0 for the standard group
- revenue continuity vs. prior quarter (±50% → flag; segment reclassifications do happen)
- FCF/NI ratio and effective tax rate outside winsorization bounds (doc 06) → null, not
  silently clamped

## Scan artifacts

Each scan run persists: scan id + date, the exact preset/filter config (JSON, hashed),
universe version, and full per-stock output (metric values, percentiles, scores, flags,
evidence). Reproducing a past scan = re-running the stored config on the stored snapshot.
This is the contract that makes the evaluation harness (05 §5) trustworthy.

## Known universe limitations

- **Membership history**: weekly Wikipedia tracking starts when the project starts, and
  that is also when the harness's honesty starts: **there is no pre-tracking harness
  path** — 05 §5 evaluates only on persisted scan snapshots, never on reconstructed
  history. The stored membership history exists to reproduce each scan's universe and to
  make index churn visible from launch. (A historical-constituents source remains a
  possible later add, not MVP.)
