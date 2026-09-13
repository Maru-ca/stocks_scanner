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


# ------------------------------------------------------ chain + analytics ------
def _full_chain(put_rows, call_rows):
    class _Chain:
        def __init__(self):
            self.puts = pd.DataFrame(put_rows)
            self.calls = pd.DataFrame(call_rows)

    class _Tk:
        def __init__(self, t):
            pass

        def option_chain(self, expiry):
            return _Chain()

    return _Tk


def test_fetch_option_chain_normalizes_puts_and_calls(monkeypatch):
    tk = _full_chain(
        [_puts_row(95, 0.80, 0.90, oi=10, iv=0.4)],
        [{"strike": 105, "bid": 1.1, "ask": 1.2, "lastPrice": 1.15,
          "openInterest": 7, "impliedVolatility": 0.35, "volume": 3}],
    )
    monkeypatch.setattr(MD.yf, "Ticker", tk)
    ch = MD.fetch_option_chain("X", TODAY)
    assert list(ch["puts"]["strike"]) == [95]
    assert list(ch["calls"]["strike"]) == [105]
    assert ch["puts"].iloc[0]["side"] == "put"
    assert ch["calls"].iloc[0]["side"] == "call"


def test_pick_nearest_strike_and_bid_zero_weak(monkeypatch):
    df = pd.DataFrame([
        {"side": "put", "strike": 90, "bid": 2.0, "ask": 2.2, "last": 2.1,
         "oi": 1, "iv": 0.3, "volume": 0, "in_the_money": True},
        {"side": "put", "strike": 95, "bid": 0.0, "ask": 1.0, "last": 0.9,
         "oi": 2, "iv": 0.3, "volume": 0, "in_the_money": False},
    ])
    q = MD.pick_nearest_strike(df, 100.0, 0.95)
    assert q["strike"] == 95
    assert q["premium"] == pytest.approx(0.50)
    assert q["weak_quote"] is True


def test_contract_analytics_breakeven():
    p = MD.contract_analytics("put", 100.0, 95.0, 1.50, 30, 0.3)
    assert p["breakeven"] == pytest.approx(93.50)
    assert p["cash_pct"] == pytest.approx(1.50 / 95)
    assert p["intrinsic"] == pytest.approx(0.0)   # 95-put vs 100 spot is OTM
    assert p["extrinsic"] == pytest.approx(1.50)
    assert p["strike_vs_spot"] == pytest.approx(-0.05)
    c = MD.contract_analytics("call", 100.0, 105.0, 2.00, 30, 0.3)
    assert c["breakeven"] == pytest.approx(107.00)
    assert c["intrinsic"] == pytest.approx(0.0)
    assert c["extrinsic"] == pytest.approx(2.00)
    itm = MD.contract_analytics("put", 100.0, 105.0, 6.00, 30, 0.3)
    assert itm["intrinsic"] == pytest.approx(5.0)
    assert itm["extrinsic"] == pytest.approx(1.0)


def test_black_scholes_delta_signs_atm():
    call = MD.black_scholes_greeks("call", 100.0, 100.0, 30, 0.20, rate=0.0)
    put = MD.black_scholes_greeks("put", 100.0, 100.0, 30, 0.20, rate=0.0)
    assert 0.45 < call["delta"] < 0.60
    assert -0.55 < put["delta"] < -0.40
    assert call["gamma"] == pytest.approx(put["gamma"], rel=1e-6)
    assert MD.black_scholes_greeks("call", 100.0, 100.0, 30, None) == {}
    assert MD.black_scholes_greeks("put", 100.0, 100.0, 0, 0.2)["delta"]  # 0 DTE still returns


# ------------------------------------------------------ CSP lens (v2 panel) ----
def test_contract_analytics_csp_lens_put():
    a = MD.contract_analytics("put", 100.0, 95.0, 2.0, 30, 0.30, rate=0.05)
    assert a["breakeven"] == pytest.approx(93.0)
    assert a["cushion_pct"] == pytest.approx(0.07)          # (spot-93)/100
    assert a["pay_vs_cushion"] == pytest.approx(2.0 / 7.0)  # premium per 1% cushion
    assert a["cash_pct"] == pytest.approx(2.0 / 95.0)
    assert 0.0 < a["assignment_risk"] < 1.0                 # |delta| present with IV


def test_contract_analytics_through_be_and_call_side():
    deep = MD.contract_analytics("put", 90.0, 95.0, 2.0, 30, 0.30)
    assert deep["cushion_pct"] == pytest.approx(-3.0 / 90.0)   # already through BE
    assert "pay_vs_cushion" not in deep
    c = MD.contract_analytics("call", 100.0, 105.0, 2.0, 30, 0.30)
    assert c["breakeven"] == pytest.approx(107.0)
    assert c["cushion_pct"] == pytest.approx(0.07)
    assert c["pay_vs_cushion"] == pytest.approx(2.0 / 7.0)
    assert 0.0 < c["assignment_risk"] < 1.0
