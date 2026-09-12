# 02 — Valuation Filters

Version 0.5 (2026-09-11). Search methodology frozen (v0.4). v0.5 hygiene: SECT mode wording aligned to the peer ladder.

Price-vs-fundamentals filters. These answer: *"is this stock cheap?"* — nothing more.
Whether it's cheap **for its quality** is handled in 05-screening-logic.

## Filter modes

Every valuation filter can be applied in one of three modes. This is the core mechanic of
the catalog — the same metric means different things per mode:

| Mode | Definition | Example |
|---|---|---|
| `ABS` | absolute threshold on the raw ratio | EV/EBIT < 12 |
| `SECT` | percentile of the ratio within the peer ladder (industry group → sector → market, doc 05 §2) at scan date | EV/EBIT in the bottom quartile of its peer set |
| `SELF` | percentile of the ratio within the stock's **own** trailing 5y distribution (weekly observations, TTM fundamentals) | EV/FCF cheaper than 80% of its own history |

`SELF` mode is what detects **multiple de-rating** — the Adobe pattern: fundamentals flat,
multiple at a decade low. `SECT` mode is what keeps comparisons honest across sectors.

`SELF` mode validity rules (enforced in doc 06 §9): minimum 3 years of valid observations
in the window, else null; only valid observations enter the distribution (FCF-loss
quarters are excluded from an EV/FCF history, not treated as infinite multiples); fewer
than 60% of the window valid → null.

## Catalog

| ID | Metric | Definition | Default | Modes | Priority |
|---|---|---|---|---|---|
| `ev_ebit` | EV / EBIT | (mkt cap + total debt − cash) / TTM EBIT | SECT ≤ 30th pct | ABS/SECT/SELF | MVP |
| `ev_fcf` | EV / FCF | EV / TTM `fcf_adj` (SBC-adjusted, doc 06 §7) | SELF ≤ 20th pct | ABS/SECT/SELF | MVP |
| `fcf_yield` | FCF yield | TTM `fcf_adj` / EV | ≥ 4% | ABS | MVP |
| `pe_ttm` | P/E trailing | price / TTM EPS | display (v0.3: out of score) | ABS/SECT/SELF | MVP |
| `pe_fwd` | P/E forward | price / consensus next-FY EPS | context only | SECT | P1 |
| `p_fcf` | Price / FCF | mkt cap / TTM FCF | — | SECT/SELF | P1 (redundant with `ev_fcf`) |
| `ev_ebitda` | EV / EBITDA | standard | SECT ≤ 30th pct | ABS/SECT/SELF | P1 |
| `ps` / `ev_sales` | Sales multiples | mkt cap or EV / TTM revenue | only with margin context | SECT/SELF | P1 |
| `pb` | Price / book | mkt cap / total equity | restricted group only | SECT | P1 |
| `div_yield` | Dividend yield | TTM dividends / price | display + income presets | ABS | P1 |
| `buyback_yield` | Buyback yield | −Δshares outstanding / 1y | ≥ 1% | ABS | P1 |
| `sh_yield` | Shareholder yield | div_yield + buyback_yield | context (v0.3: policy, not cheapness) | ABS | P1 |
| `ey_spread` | Earnings yield spread | (TTM EBIT/EV) − 10Y Treasury (FRED) | display (v0.3: clone once ranked) | ABS | MVP |

Definitions/inputs:
- **EV, FCF, TTM and every other input** follow the canonical formulas in
  [06-definitions.md](06-definitions.md) — the single source of truth. Docs reference it;
  implementations must match it.
- **Why SBC-adjusted FCF for FCF metrics**: plain CFO − capex adds stock comp back as
  non-cash, inflating FCF for heavy-SBC companies — and our target universe is full of
  them (large-cap tech). Plain `fcf` is stored and displayed alongside; scored metrics
  use `fcf_adj` (decision: OPEN_QUESTIONS Q8).
- Negative denominators (EBIT < 0, FCF < 0): metric is `null` for that stock (see 01, missing data rule).

**Score usage (v0.3, doc 05 §2.2):** only `ev_ebit` (SECT), `ev_fcf` (SELF) and
`fcf_yield` (fixed scale) feed CheapnessScore. `pe_ttm`, `ey_spread` and `sh_yield` are
display/context: with GS10 constant on a scan date, ranking `ey_spread` within a sector is
algebraically identical to ranking inverted `ev_ebit` (the anchor cancels), and
`sh_yield` measures capital-return policy, not cheapness. All catalog IDs remain usable
in the **Custom** builder.

**ABS scoring rule (v0.3):** absolute yields are scored on a fixed economic scale
(`fcf_yield` → clip(0, 10%)/10%, doc 06 §9), never cross-sectionally ranked — ranking a
yield against peers just reproduces the inverted multiple's ranking and silently deletes
the absolute anchor (the v0.2 bug).

## Notes & caveats

- **EV multiples over P/E**: capital-structure neutral and comparable across the standard
  group. P/E stays in the catalog because it's familiar, but presets default to EV metrics.
- **The absolute anchor is `fcf_yield` on the fixed scale** (plus the flag's
  yield/residual OR, doc 05 §3). Relative cheapness (SECT/SELF) can survive an entire
  sector being expensive; the fixed-scale yield — not `ey_spread`, which cancels once
  ranked — is what says "cheap" in cash terms.
- **`buyback_yield` from share count**, not announced buybacks — announcements are
  intentions, share reduction is fact. Data: shares outstanding history (SEC / yfinance).
- **Self-history window**: 5y default (covers at least one full rate/regime cycle);
  10y optional but old regimes can mislead. See OPEN_QUESTIONS.
- **Rate-regime caveat**: a SELF percentile says "cheap vs. its own history" — but across
  a regime break, *everything* can look cheap vs. history at once (2021→2023 repricing).
  That is why CheapnessScore anchors on the fixed-scale `fcf_yield`, why the flag carries
  the yield/residual OR (doc 05 §3 — the residual's industry dummies absorb sector-wide
  crushes, so regime events must clear the yield arm), and why percentiles alone never
  fire the flagship.
- **Watchlist trap**: a low `ev_sales` with bad margins is a value trap, not a bargain.
  Sales multiples are never used as standalone filter inputs — only inside the quality-conditioned
  logic of doc 05.
