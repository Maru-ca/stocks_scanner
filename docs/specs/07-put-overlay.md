# 07 — Put-Selling Overlay (Cash-Secured Puts on the Flagship List)

Version 0.5 (2026-09-11). Overlay rules frozen (v0.4). v0.5: ATM/expiry rule and gate numbers pinned in doc 08 §4.

The flagship list (`derated_quality`, ranked by valuation residual) doubles as the
**assignable universe** for cash-secured puts: names you would be happy to own at the
strike, where elevated vol is how you get paid to stand there. This doc specifies the
overlay — display columns and liquidity gates on that list. **The overlay never ranks,
scores, or flags.**

## Trade A vs. Trade B — one symbol list, two different books

| | Trade A — cash-secured put (in scope) | Trade B — short-dated earnings crush (out of scope) |
|---|---|---|
| Question | would I want the shares at this strike? | will IV die faster than the stock moves? |
| Assignment | acceptable — that's the bid | failure |
| What quality does | makes the stock holdable | does not cap the gap |
| What the premium is | payment for standing at a price you'd buy | payment for the left tail |
| Rank | valuation residual (equity thesis) | IV/HV — must never be used on Trade A |

Highest IV = most contested narrative into the print. Ranking Trade A by premium selects
exactly the names whose assignment outcome is worst — the juiciest weekly is the print you
least want to be assigned through. If Trade B is ever built, it is a separate product
evaluated as short-vol P&L — never mixed into the 05 §5 equity harness (mixing the books
will "prove" whichever one was accidentally encoded).

## Overlay columns (nightly, free data)

| ID | Definition | Use |
|---|---|---|
| `days_to_earnings` | trading days to next report | `event_window` badge (< 10 days): the premium window — the opposite reading of the equity view's `numbers_stale` badge on the same number (doc 04 §4.4) |
| `fcf_yield_strike_m5` / `_m10` | `fcf_adj / EV_strike` at price × 0.95 / × 0.90 (formula: doc 06 §7) | **the assignment test**: if I'm put the stock there, what's the yield? |
| `iv_atm_30d` vs `hv_30d` | ATM IV vs. 30d realized vol from the yfinance chain | display only; patchy — nightly snapshots build an IV history the same way fundamentals accrue |
| `put_oi_spread` | OI and bid-ask on the ~30–45 DTE ATM put | **overlay gate**: equity `adv_usd` says nothing about whether the weekly put is tradeable |

Numbers pinned in doc 08 §4: IV row = nearest expiry with DTE ≥ 21, strike closest to
spot; option gate = the ~30–45 DTE (nearest 35) ATM put with **OI ≥ 500 contracts** and
**(ask − bid)/mid ≤ 10%**.

## Rules

- **No overlay metric enters CheapnessScore, any flag, or any ranking.** Rank stays the
  valuation residual — the ownership thesis.
- `days_to_earnings < 10` never drops a name from the overlay; it changes the badge.
- Strike yields use the same (possibly stale) TTM `fcf_adj` as everything else — the
  `numbers_stale` badge covers them too.
- Full IV surfaces, skew, expected-move models: requires paid data — blocked, not MVP.
- Concentration warning (trading rule, not scanner feature): a put book over a list of
  derated quality names is concentrated duration/narrative risk; size and sector caps
  belong to the trader, not the scanner.

## Anti-goals

- No second fundamental model for options — the flagship list IS the assignable universe.
- No auto-trading, no intraday chains, no Trade B in Phases 1–3.
