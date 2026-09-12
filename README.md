# stocks_scanner

S&P 500 scanner that looks for stocks whose fundamentals are strong but whose price
doesn't reflect them (quality–price divergence), plus a general filter engine for
standard screening (valuation, quality, sentiment) and a cash-secured-put overlay.

**Status: implemented.** Search methodology frozen at spec v0.4; data contract v0.5
(docs 01–08). All phases built: data layer, feature/self-history engine, scoring,
flags, presets, put overlay, scan artifacts, tests, Streamlit dashboard.

## Quick start

```bash
./control.sh start      # dashboard on http://localhost:8501 (venv auto-created first run)
./control.sh status     # pid, health, served scan
./control.sh restart    # pick up code changes / fresh artifacts
./control.sh stop

PORT=9000 ./control.sh start   # custom port
```

Run a scan: from the dashboard's **Data manager** page ("Re-download everything & re-scan" — forces fresh universe, DGS10 and SEC caches), or:

```bash
.venv/bin/python -m scanner.scan                 # full standard group
.venv/bin/python -m scanner.scan --tickers ADBE,ORCL --skip-options
```

Manual equivalent:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# smoke run (subset, no options calls)
.venv/bin/python -m scanner.scan --tickers ADBE,ORCL,MSFT,NVDA,CRM --skip-options

# full standard-group scan (~380 names; first run downloads all SEC companyfacts,
# ~20–40 min; subsequent runs reuse the cache)
.venv/bin/python -m scanner.scan

# dashboard
.venv/bin/streamlit run dashboard/app.py

# tests
.venv/bin/python -m pytest tests/ -q
```

Optional: set `SCANNER_UA` in `.env` (SEC asks for a self-identifying User-Agent with
contact info). No API keys are needed anywhere — analyst revisions come from yfinance's
analyst-action feed (v0.5.3, replaced the FMP free-tier proxy).

## CLI

```
python -m scanner.scan [--limit N] [--tickers A,B,...] [--skip-options]
                       [--refresh-sec] [--no-fmp] [--asof YYYY-MM-DD]
```

Each run writes scan artifacts to `data/scans/<date>/` (doc 05 §4b): `config.json`
(hashed thresholds), `metrics.csv` (per-stock metrics, scores, flags, evidence),
`presets/*.csv` (flagship / beaten_but_delivering / compounders_on_sale),
`overlay.csv` (put overlay, display-only), `history.parquet` (weekly SELF timelines),
`coverage.csv` (unscored names + reasons), `summary.json`.

## What it computes (spec summary — full detail in docs/specs/)

- **Quality is the gate**: QualityScore (profitability/consistency/accounting/balance,
  peer-ladder percentiles) with a fixed top-20% floor. No price inputs.
- **Cheapness is the rank**: CheapnessScore (EV/FCF vs own 5y history, EV/EBIT vs
  industry-group peers, FCF yield on a fixed 0–10% scale) and the flagship rank key —
  the valuation residual `log(EV/FCF) ~ log(ROIC) + industry group`.
- **Sentiment is the flag**: `derated_quality` (depressed-multiple OR-arm incl.
  median-ratio, yield/residual OR, margin-slope brakes), `beaten_but_delivering`.
- **Data honesty**: filing-date visibility (no look-ahead), YTD→TTM differencing with
  revision history preserved, vintaged-scan artifacts, SEC XBRL as the single
  fundamentals source (doc 08).

## Layout

```
scanner/            package (config = every spec threshold)
  universe.py       Wikipedia constituents, GICS IG map, share-class dedupe
  sec_edgar.py      companyfacts fetch/cache, tag map, YTD→TTM assembly
  market_data.py    yfinance weekly prices, options ATM, earnings dates
  rates_fx.py       FRED DGS10
  metrics.py        doc 06 formulas (EV/IC/NOPAT/FCF variants/SELF stats)
  features.py       per-stock features + weekly SELF timeline (as-known fundamentals)
  scoring.py        peer ladder, QualityScore/CheapnessScore, floor, residual
  flags.py          derated_quality / beaten_but_delivering with evidence
  overlay.py        put overlay (strike-implied FCF yields, DTE badges, IV/HV, gate)
  fmp.py            vintaged consensus snapshots (revision proxy)
  scan.py           orchestration + CLI + artifacts
dashboard/app.py    Streamlit UI (presets, quadrant, drill-down, overlay)
tests/              assembly / metrics / scoring+flags (incl. Adobe-pass, PayPal-fail)
data/reference/     GICS sub-industry → industry-group map
docs/specs/         the frozen specifications (01–08 + decision log)
```

## Notes & known edges

- Payment processors (PYPL, V, MA…) are excluded automatically: GICS moved them to
  Financials in 2023 (restricted group, doc 01).
- Names with sparse SEC history (e.g. XOM in some environments) land in `coverage.csv`
  as unscored rather than being scored on partial data — null policy, doc 06 §10.
- `fcf_adj` (SBC-adjusted) drives multiples/margins; plain `fcf` drives positivity
  counts and CAGR gates (Q8).
- Thresholds live in `scanner/config.py` with spec references; changing one is a
  methodology change — log it in `docs/specs/OPEN_QUESTIONS.md`.
