"""Live put panel (v1) — pure logic + mocked chains, no network.

Covers: DTE-dependent strike targets, expiry filtering 0..60 (0 allowed),
premium % of strike, annualize iff DTE >= 21, bid=0 -> weak quote with mid
fallback, and best-effort None on chain failures.
"""
import datetime as dt

import pandas as pd
import pytest

from scanner import market_data as MD

TODAY = dt.date(2026, 9, 13)


def _chain(rows):
    """Fake yfinance chain: .puts DataFrame with the columns put_quote reads."""
    class _Puts(pd.DataFrame):
        pass

    class _Chain:
        puts = pd.DataFrame(rows)

    class _Tk:
        def __init__(self, chain):
            self._c = chain

        def option_chain(self, expiry):
            return self._c

    return _Tk(_Chain())


def _puts_row(strike, bid, ask, last= None, oi=100, iv=0.3):
    return {"strike": strike, "bid": bid, "ask": ask,
            "lastPrice": last if last is not None else bid, "openInterest": oi,
            "impliedVolatility": iv}


# ------------------------------------------------------------- strike targets --
def test_strike_target_buckets():
    assert MD.strike_target_pct(0) == 1.00      # 0 DTE allowed, ATM
    assert MD.strike_target_pct(1) == 1.00
    assert MD.strike_target_pct(6) == 1.00
    assert MD.strike_target_pct(7) == 0.97
    assert MD.strike_target_pct(20) == 0.97
    assert MD.strike_target_pct(21) == 0.95
    assert MD.strike_target_pct(35) == 0.95
    assert MD.strike_target_pct(60) == 0.95


# ---------------------------------------------------------- listed expiries ----
def test_listed_expiries_window_includes_zero_dte(monkeypatch):
    class _Tk:
        def __init__(self, t):
            pass

        options = ("2026-09-13", "2026-09-18", "2026-10-16", "2026-12-18", "2026-08-15")
    monkeypatch.setattr(MD.yf, "Ticker", _Tk)
    out = MD.listed_expiries("X", TODAY, max_dte=60)
    assert out == [dt.date(2026, 9, 13), dt.date(2026, 9, 18), dt.date(2026, 10, 16)]
    assert (out[0] - TODAY).days == 0          # 0 DTE is in the window


def test_listed_expiries_failure_returns_empty(monkeypatch):
    class _Tk:
        def __init__(self, t):
            raise RuntimeError("no chain")
    monkeypatch.setattr(MD.yf, "Ticker", _Tk)
    assert MD.listed_expiries("X", TODAY) == []


# ------------------------------------------------------------------ quotes -----
def test_put_quote_picks_nearest_strike_and_positive_bid(monkeypatch):
    tk = _chain([_puts_row(90, 1.10, 1.20), _puts_row(95, 0.80, 0.90),
                 _puts_row(100, 0.40, 0.50)])
    monkeypatch.setattr(MD.yf, "Ticker", lambda t: tk)
    q = MD.put_quote("X", 100.0, TODAY + dt.timedelta(days=30), 0.95, TODAY)
    assert q["strike"] == 95
    assert q["bid"] == pytest.approx(0.80)
    assert q["mid"] == pytest.approx(0.85)
    assert q["premium"] == pytest.approx(0.80)  # positive bid wins
    assert q["weak_quote"] is False
    assert q["dte"] == 30


def test_put_quote_bid_zero_falls_back_to_mid_and_flags_weak(monkeypatch):
    tk = _chain([_puts_row(95, 0.0, 1.00, last=0.98)])
    monkeypatch.setattr(MD.yf, "Ticker", lambda t: tk)
    q = MD.put_quote("X", 100.0, TODAY + dt.timedelta(days=30), 0.95, TODAY)
    assert q["bid"] == 0.0
    assert q["mid"] == pytest.approx(0.50)
    assert q["premium"] == pytest.approx(0.50)  # NOT 0: bid=0 is not premium=0
    assert q["weak_quote"] is True


def test_put_quote_no_usable_price_is_none_premium(monkeypatch):
    tk = _chain([_puts_row(95, 0.0, None, last=None)])
    monkeypatch.setattr(MD.yf, "Ticker", lambda t: tk)
    q = MD.put_quote("X", 100.0, TODAY + dt.timedelta(days=30), 0.95, TODAY)
    assert q is not None
    assert q["premium"] is None
    assert MD.premium_pct_of_strike(q) is None


def test_put_quote_chain_failure_returns_none(monkeypatch):
    class _Tk:
        def __init__(self, t):
            pass

        def option_chain(self, e):
            raise RuntimeError("gone")
    monkeypatch.setattr(MD.yf, "Ticker", _Tk)
    assert MD.put_quote("X", 100.0, TODAY, 0.95, TODAY) is None


# ------------------------------------------------------ premium math -----------
def test_premium_pct_of_strike():
    q = {"premium": 0.85, "strike": 95.0}
    assert MD.premium_pct_of_strike(q) == pytest.approx(0.85 / 95)
    assert MD.premium_pct_of_strike(None) is None
    assert MD.premium_pct_of_strike({"premium": None, "strike": 95}) is None
    assert MD.premium_pct_of_strike({"premium": 1.0, "strike": 0}) is None


def test_annualize_only_from_21_dte():
    assert MD.annualized_premium(None, 35) is None
    assert MD.annualized_premium(0.01, 20) is None          # short DTE: suppressed
    assert MD.annualized_premium(0.01, 7) is None
    assert MD.annualized_premium(0.01, 21) == pytest.approx(0.01 * 365 / 21)
    assert MD.annualized_premium(0.0089, 28) == pytest.approx(0.0089 * 365 / 28)
