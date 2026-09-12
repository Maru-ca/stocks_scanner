# 04 — Sentiment, Momentum & Non-Financial Filters

Version 0.4 (2026-09-11). Status: draft. Changes: `days_to_earnings` split into two opposite badges (`numbers_stale` / `event_window`) for the equity vs. put-overlay views (doc 07).

These filters measure **what the market believes** (price action, analyst positioning,
crowding) — independent of what the business does. They are never used alone: their
purpose is to be **compared against** the quality metrics of doc 03. That comparison is
the whole product (doc 05).

## 4.1 Price action / momentum

| ID | Metric | Definition | Default use | Priority |
|---|---|---|---|---|
| `mom_12_1` | 12m momentum, skip last month | P(t−21)/P(t−252) − 1 | flag input only — `beaten_but_delivering`; never scored (v0.3) | MVP |
| `mom_6m` / `mom_3m` | shorter momentum | standard | inflection detection | P1 |
| `rs_12m` | Relative strength vs. S&P 500 | stock 12m return − index 12m return | context/display only (collinear with `mom_12_1`) | MVP |
| `dd_52w` | Distance from 52w high | price / 52w high − 1 | display + de-rating context | MVP |
| `vol_1y` | Realized volatility | std of daily returns, 1y | context/risk | P1 |
| `beta` | Beta vs. S&P 500, 2y weekly | standard | context | P1 |

- Skipping the most recent month in `mom_12_1` is the standard construction: the last
  month is short-term reversal noise, not sentiment.
- All return metrics use dividend- and split-adjusted closes (total-return basis, doc 06 §2)
  — raw prices make dividend payers look like chronic underperformers.
- `dd_52w` is descriptive, not a filter: big drawdown + intact fundamentals is exactly
  the pattern the scanner hunts, but drawdown alone means nothing.

## 4.2 Analyst sentiment

| ID | Metric | Definition | Default use | Priority |
|---|---|---|---|---|
| `eps_rev_breadth_3m` | Revision breadth | net analyst actions last 90d: (rating upgrades + target raises) − (downgrades + lowers), yfinance upgrades/downgrades feed, point-in-time by event date (v0.5.3; was FMP consensus-EPS proxy — owner barred paid plans and the free proxy needed 90d of vintage warm-up) | flag input (`beaten_but_delivering` only) | MVP\* |
| `rec_trend_6m` | Recommendation trend | Δ(mean analyst rec, 1=strong buy..5=sell) over 6m, negative = improving | context | P1 |
| `surprise_streak` | Beat streak | # of last 4 quarterly EPS reports with positive surprise | context (≥ 3 = delivering) | P1 |
| `pt_upside` | Target upside | mean price target / price − 1 | display only — targets lag price | P1 |

Revision breadth is the most information-dense analyst metric: it's forward-looking,
bounded [−1, +1], and reacts faster than recommendation changes. Targets are decorative;
they mostly describe yesterday's price.

MVP\*: breadth feeds only `beaten_but_delivering`; if the free estimates source proves
patchy it drops to P1 without blocking anything — the flagship preset depends on no
estimates data at all (doc 05 §3). Positive revisions deliberately do **not** gate the
flagship: the Adobe hunt window often has *down* revisions. And momentum never enters a
score (v0.3): "price falling" double-counts with `ev_fcf` SELF and rewards falling
knives — sentiment lives in flags, not in ranking.

## 4.3 Positioning / crowding

| ID | Metric | Definition | Default use | Priority |
|---|---|---|---|---|
| `short_float` | Short interest | shares short / float | context; extreme = contested thesis | P1 |
| `short_dtc` | Days to cover | short interest / avg daily volume | context | P2 |
| `short_chg_3m` | Short interest change | Δ short interest over 3m | crowd capitulation signal | P2 |

Short interest publishes bi-weekly and lags — it is a slow signal and is never a gate.

## 4.4 Non-financial / structural

| ID | Metric | Definition | Default use | Priority |
|---|---|---|---|---|
| `adv_usd` | Liquidity | dollar volume, 3m average | gate: ≥ $20M/day | MVP |
| `mktcap` | Market cap band | market cap | gate: ≥ $10B (MVP) | MVP |
| `index_tenure` | Time in index | years since added to S&P 500 | gate: ≥ 1y (skip add-in pops) | MVP |
| `days_to_earnings` | Earnings proximity | trading days to next scheduled earnings date | two badges, opposite meaning per book: `numbers_stale` < 10 days (equity view — estimates/filings lag) · `event_window` < 10 days (put overlay — the premium window, doc 07) | P1 |
| `insider_net_90d` | Insider net buying | Σ Form 4 open-market buys − sells, 90d | context (small numbers, high signal) | P2 |
| `inst_delta_13f` | Institutional Δ | aggregate 13F position change, quarterly | context | P2 |
| `news_sentiment` | News tone | NLP sentiment on rolling news window | display; methodology needed | P2 |
| `options_skew` | Put/call + IV skew | OI-weighted put/call, 30d IV vs. 1y range | display; data cost | P2 |

Note: the `mktcap` gate is near-redundant — S&P 500 membership already implies roughly
$10B+. It stays as a safety net against stale universes, not as a real filter.

## P2 backlog rationale

Insider buying and news sentiment are the highest-value non-financial signals for this
product (they explain *why* the divergence exists), but both need extra plumbing
(Form 4 aggregation / an NLP pipeline or news API). They are spec'd now so the data layer
reserves a place for them, and revisited after the MVP proves out.

## Caveats

- Momentum is regime-dependent: "beaten down + improving revisions" works in recoveries
  and bleeds in sustained bears. The evaluation harness (doc 05 §5) exists precisely to
  measure this per preset instead of assuming it.
- Analyst coverage is thin for mega-caps' blind spots and dense where it's useless —
  always report revision **counts**, not just breadth, so 3-revision breadth is visibly
  different from 30-revision breadth.
- Everything in this doc can be gamed or crowded; nothing here is a standalone buy signal.
  The only sanctioned use is as inputs to the divergence logic of doc 05.
