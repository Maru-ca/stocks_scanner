# Review request — stocks_scanner methodology (specs v0.2)

You are reviewing the specification of a stock scanner before it gets built. Your job is
to challenge the methodology decisions, not the prose.

## Context and goal

We are building a scanner over the **S&P 500** with one core thesis:

**Find stocks whose fundamentals are good — profitable, consistent, and growing — whose
price is mispriced (cheap vs. its own history, vs. its sector, and in absolute terms),
and toward which market sentiment is bad (beaten down, out of favor, negative narrative) —
where that negative narrative is NOT confirmed by the numbers.**

Canonical example of the pattern we hunt: **Adobe and Oracle** in recent years — business
quality intact (high ROIC, high FCF margins, consistent cash flow, growth decelerating but
healthy), while the multiple de-rated to multi-year lows on narrative fears (AI disruption,
growth scare). We want to detect that pattern systematically, and equally important, avoid
its failure modes (value traps, falling knives, cheap-for-a-reason).

What this is NOT: not a general stock-tip generator, not intraday, not a black box. Every
ranked stock must be traceable to its triggering metrics.

## The documents to read (in order)

1. `/home/rperez/ZCodeProject/stocks_scanner/README.md` — overview + roadmap
2. `/home/rperez/ZCodeProject/stocks_scanner/docs/specs/01-universe.md` — universe, data sources, as-of rules
3. `/home/rperez/ZCodeProject/stocks_scanner/docs/specs/02-filters-valuation.md` — valuation filters (ABS/SECT/SELF modes)
4. `/home/rperez/ZCodeProject/stocks_scanner/docs/specs/03-filters-quality.md` — quality filters
5. `/home/rperez/ZCodeProject/stocks_scanner/docs/specs/04-filters-sentiment.md` — sentiment/momentum/positioning filters
6. `/home/rperez/ZCodeProject/stocks_scanner/docs/specs/05-screening-logic.md` — scores, flags, presets, evaluation harness
7. `/home/rperez/ZCodeProject/stocks_scanner/docs/specs/06-definitions.md` — canonical formulas
8. `/home/rperez/ZCodeProject/stocks_scanner/docs/specs/OPEN_QUESTIONS.md` — pending decisions Q1–Q9

## The decisions we made — challenge each one

1. **Two independent scores**: QualityScore (measured with zero price inputs) and
   MispricingScore (valuation + sentiment divergence), combined 50/50, gated by a hard
   quality floor (QualityScore ≥ 60) to block value traps.
2. **Three valuation modes**: ABS (absolute threshold), SECT (percentile within GICS
   sector), SELF (percentile vs. the stock's own 5-year history). SELF is the de-rating
   detector and the heart of the product.
3. **SBC-adjusted FCF** (`fcf_adj` = CFO − capex − stock comp) as the scored basis for all
   FCF metrics; plain FCF stored and displayed (open question Q8).
4. **`rs_12m` demoted to context** — collinear with `mom_12_1`. (Did we miss other
   collinearity? e.g., `pe_ttm` SELF vs. `ev_fcf` SELF both in MispricingScore.)
5. **Sentiment inputs are**: 12-1 month momentum (inverted) and 3m analyst EPS revision
   breadth. Short interest is context-only; insider buying, 13F deltas, news sentiment,
   options skew are P2.
6. **Divergence flags are the core detections**: `derated_quality` (QualityScore ≥ 70 AND
   EV/FCF SELF ≤ 20th pct AND FCF TTM ≥ 0.9× 5y avg), `beaten_but_delivering` (momentum in
   bottom sector quartile AND revision breadth > 0 AND QualityScore ≥ 60), `crowd_exiting`
   (P2). Presets require flags, not percentiles alone.
7. **Quality is levels, not acceleration**: profitability 40%, consistency 20%, accounting
   quality 20%, balance sheet 10%, growth only 10% as floors. Decelerating-but-healthy
   names (the Adobe case) stay eligible; deterioration is caught by TTM-vs-5y checks.
8. **Absolute anchor**: FCF yield and EBIT/EV spread vs. the 10Y Treasury guard against
   "everything cheap vs. history" after a rate-regime break.
9. **Sector-relative scoring within GICS**; financials, utilities and real estate excluded
   in MVP (their ratios break ROIC/EV logic).
10. **Evaluation harness**: every preset backtested point-in-time against three baselines —
    index, sector, and a matched-cheapness basket WITHOUT the quality floor (the one that
    proves the thesis).

## What we want back

1. **Per-decision verdict** — a table: decision | keep / change / drop | your reasoning.
2. **Methodology attack**: where would this scanner be systematically wrong? Does inverted
   momentum reward falling knives? Can value traps clear the 60 floor? Do sector
   percentiles distort in concentrated sectors? Any look-ahead or survivorship holes left?
   Any threshold that smells overfit?
3. **Fit-for-purpose test**: walk through whether `derated_quality` would have caught the
   Adobe/Oracle-type situations, and — just as important — what junk it would have flagged
   (intel-style deteriorating names, 2009 financials, etc.).
4. **Missing signals**: anything material we omitted for THIS thesis (residual-value
   regression of multiples on quality, quality-minus-junk factor construction, earnings
   yield vs. bond-proxy competition, guidance vs. estimates, capex-cycle signals…). Judge
   by marginal value for the mispricing+bad-sentiment+good-fundamentals goal, not by
   general completeness.
5. **Open questions**: answer Q1–Q9 with a recommendation and a one-line rationale each.
6. **Top 5 changes**, ranked by expected impact on scan quality, each with the concrete
   alternative (metric, formula, or threshold) we should adopt.

## Rules of engagement

- Be specific: quote the doc and section, propose the exact replacement, not vague direction.
- Challenge assumptions; do not just polish wording.
- Do not redesign the product wholesale, and stay out of FE/BE architecture and code —
  methodology only.
- If a decision is right, say "keep" briefly and spend your words where it matters.
- Assume data constraints: US large caps; free/cheap sources only (SEC EDGAR XBRL,
  yfinance, FRED, freemium FMP/SimFin). Anything needing expensive data must be labeled
  "requires paid data".
- End with the per-decision table and the top-5 list so we can act on it directly.
