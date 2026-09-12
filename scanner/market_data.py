"""Market data: weekly price history (SELF windows + momentum), options, earnings dates.

Doc 08 §1/§4: yfinance with auto_adjust (split/dividend-consistent), polite pacing;
options and earnings are best-effort display inputs for the overlay subset.
Momentum uses the weekly grid (P(t-4w)/P(t-52w) = the doc 04 12m-1m construction:
iloc[-5] is 4 weekly bars before the last bar, iloc[-53] is 52 bars before it, so the
numerator/denominator gap is 48 weeks = 12m minus the skipped month).
"""
from __future__ import annotations

import datetime as dt
import time

import numpy as np
import pandas as pd
import yfinance as yf

from . import config

_CHUNK = 40


def _ticker_frame(raw: pd.DataFrame, ticker: str, single: bool) -> pd.DataFrame | None:
    """One ticker's OHLCV sub-frame from a yf.download result, or None.

    Multi-ticker downloads give MultiIndex (ticker, field) columns with
    group_by='ticker', but single-ticker downloads return flat field-only
    columns in several yfinance versions -- attribute a flat frame to the
    ticker only when the chunk really had exactly one, so a lone ticker is
    never silently dropped. Returns the frame with NaN-close rows removed.
    """
    if isinstance(raw.columns, pd.MultiIndex):
        if ticker not in raw.columns.get_level_values(0):
            return None
        sub = raw[ticker]
    elif single:
        sub = raw  # flat columns: the whole frame IS the one ticker
    else:
        return None  # flat frame for a multi-ticker chunk: not attributable
    if not isinstance(sub, pd.DataFrame) or "Close" not in sub.columns:
        return None
    sub = sub.dropna(subset=["Close"])
    return None if sub.empty else sub


def download_weekly(tickers: list[str], years: int = 6, max_retries: int = 2) -> pd.DataFrame:
    """Long frame [ticker, week, close, volume] for the SELF window and momentum."""
    frames = []
    for i in range(0, len(tickers), _CHUNK):
        chunk = tickers[i : i + _CHUNK]
        raw = None
        for attempt in range(max_retries + 1):
            try:
                raw = yf.download(
                    chunk, period=f"{years}y", interval="1wk", auto_adjust=True,
                    group_by="ticker", progress=False, threads=True,
                )
                if raw is not None and not raw.empty:
                    break
            except Exception:
                raw = None
            if attempt < max_retries:
                time.sleep(2)  # backoff before the retry (doc 08 §1 politeness)
        if raw is None or raw.empty:
            print(f"[market] weekly download failed for {len(chunk)} ticker(s) after "
                  f"{max_retries + 1} attempts: {', '.join(chunk[:5])}"
                  f"{' ...' if len(chunk) > 5 else ''}")
            time.sleep(0.5)
            continue
        produced = []
        for t in chunk:
            try:
                sub = _ticker_frame(raw, t, single=len(chunk) == 1)
            except Exception:
                sub = None
            if sub is None:
                continue
            idx = pd.to_datetime(sub.index)
            if getattr(idx, "tz", None) is not None:
                idx = idx.tz_localize(None)  # yfinance weekly stamps may be tz-aware
            frames.append(pd.DataFrame({
                "ticker": t,
                "week": idx.normalize(),
                "close": sub["Close"].astype(float),
                "volume": sub["Volume"].astype(float) if "Volume" in sub.columns else np.nan,
            }))
            produced.append(t)
        missed = [t for t in chunk if t not in produced]
        if missed:
            print(f"[market] no weekly data for {len(missed)} ticker(s): {', '.join(missed[:5])}"
                  f"{' ...' if len(missed) > 5 else ''}")
        time.sleep(0.5)
    cols = ["ticker", "week", "close", "volume"]
    if not frames:
        return pd.DataFrame(columns=cols)
    out = pd.concat(frames, ignore_index=True).sort_values(["ticker", "week"], kind="stable")
    out = out.groupby(["ticker", "week"], as_index=False).last()  # dedupe any repeated weeks
    return out


def price_features(weekly: pd.DataFrame) -> pd.DataFrame:
    """Momentum / drawdown / liquidity per ticker on the weekly grid (doc 04 §4.1).

    mom_12_1 = P(t-4w)/P(t-52w) - 1 (iloc[-5] / iloc[-53]): the weekly-grid version
    of the doc's P(t-21d)/P(t-252d) -- a 48-week gap, i.e. 12m skipping the last
    month. ret_12m = P(t)/P(t-52w) - 1. dd_52w = price / 52w high - 1 with
    min_periods=40 (tolerates up to 12 missing weeks inside the window).
    adv_usd pairs each week's volume with that week's close (13-week mean of
    weekly dollar volume / 5): valuing 3-month-old volume at the latest close
    would overstate liquidity after a rally and understate it after a sell-off.
    Tickers with < 60 weekly bars are skipped -- the windows above need >= 53.
    """
    rows = []
    for t, g in weekly.groupby("ticker"):
        g = g.sort_values("week").reset_index(drop=True)
        if len(g) < 60:
            continue
        close = g["close"]
        last = float(close.iloc[-1])
        c_1m = close.iloc[-5]   # 4 weekly bars back = the skipped month
        c_12m = close.iloc[-53]  # 52 weekly bars back
        ok12 = pd.notna(c_12m) and c_12m > 0
        mom_12_1 = (float(c_1m) / float(c_12m) - 1.0) if ok12 and pd.notna(c_1m) else np.nan
        ret_12m = (last / float(c_12m) - 1.0) if ok12 else np.nan
        roll_max = close.rolling(52, min_periods=40).max().iloc[-1]
        dd_52w = last / roll_max - 1.0 if pd.notna(roll_max) and roll_max > 0 else np.nan
        wk = g.tail(13)
        adv = float((wk["volume"] * wk["close"]).mean() / 5.0)  # avg weekly $ volume / trading days
        rows.append({
            "ticker": t, "price": last,
            "mom_12_1": mom_12_1, "dd_52w": dd_52w, "ret_12m": ret_12m,
            "adv_usd": adv,
            "hi_52w": float(roll_max) if pd.notna(roll_max) else np.nan,
        })
    return pd.DataFrame(rows, columns=["ticker", "price", "mom_12_1", "dd_52w",
                                       "ret_12m", "adv_usd", "hi_52w"])


def shares_outstanding(ticker: str) -> float | None:
    """Current shares outstanding from yfinance — last-resort fallback for filers
    whose share tags don't survive companyfacts (STZ-style A/B classes dimension
    even the weighted-average tags). Best-effort; None on failure."""
    try:
        tk = yf.Ticker(ticker)
        for getter in (lambda: tk.fast_info.get("shares"),
                       lambda: tk.get_info().get("sharesOutstanding")):
            try:
                v = getter()
                if v and float(v) > 0:
                    return float(v)
            except Exception:
                continue
        return None
    except Exception:
        return None


def hv_30d(ticker: str, months: int = 3) -> float | None:
    """Annualized 30d realized vol: log-return std x sqrt(252) on the last 30
    daily closes (auto_adjust=True, consistent with the weekly grid). None on
    any failure."""
    try:
        df = yf.Ticker(ticker).history(period=f"{months}mo", interval="1d", auto_adjust=True)
        if df is None or len(df) < 25:
            return None
        c = pd.to_numeric(df["Close"], errors="coerce")
        r = np.log(c[c > 0]).diff().dropna().tail(30)  # drop non-positive closes: log(0) = -inf
        if len(r) < 20:
            return None
        s = r.std()
        return float(s * np.sqrt(252)) if pd.notna(s) else None
    except Exception:
        return None


def next_earnings_date(ticker: str, today: dt.date | None = None) -> dt.date | None:
    """Next scheduled earnings date as a plain dt.date (doc 08 §1: patchy ->
    None when unknown). limit=8 spans ~4 quarters ahead, always enough for the
    next report; unknown/failed -> None. `today` threads the scan's as-of date
    (doc 08 §4) instead of wall-clock time."""
    try:
        edf = yf.Ticker(ticker).get_earnings_dates(limit=8)
        if edf is None or len(edf) == 0:
            return None
        idx = pd.to_datetime(edf.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        today = today or dt.date.today()
        future = [d.date() for d in idx if d.date() >= today]
        return min(future) if future else None
    except Exception:
        return None


def breadth_from_actions(events: pd.DataFrame | None, asof: dt.date,
                         window_days: int = 90) -> dict | None:
    """Net analyst-action breadth over the trailing window (pure, testable).

    yfinance upgrades/downgrades rows carry a rating Action (up/down/init/reit/main)
    and a priceTargetAction (Raises/Lowers/Keeps). We count rating ups/downs AND
    target raises/lowers — a target change is a revision even when the rating holds.
    Everything else is ignored. Events after `asof` are invisible (point-in-time,
    doc 08 §4). Returns {"net": ups - downs, "ups": n, "downs": n} or None when
    the frame has no usable in-window events.
    """
    if events is None or len(events) == 0:
        return None
    idx = pd.to_datetime(events.index, utc=True, errors="coerce")
    asof_ts = pd.Timestamp(asof, tz="UTC") + pd.Timedelta(days=1)  # same-day events count
    lo = asof_ts - pd.Timedelta(days=window_days)
    act = events["Action"].astype("string").str.lower() if "Action" in events.columns else None
    pta = (events["priceTargetAction"].astype("string").str.lower()
           if "priceTargetAction" in events.columns else None)
    ups = downs = 0
    for i, ts in enumerate(idx):
        if pd.isna(ts) or not (lo < ts <= asof_ts):
            continue
        a = act.iloc[i] if act is not None else None
        p = pta.iloc[i] if pta is not None else None
        if a == "up" or p == "raises":
            ups += 1
        elif a == "down" or p == "lowers":
            downs += 1
    if ups == 0 and downs == 0:
        return None
    return {"net": ups - downs, "ups": ups, "downs": downs}


def analyst_revision_breadth(ticker: str, asof: dt.date) -> dict | None:
    """yfinance analyst-action history -> breadth_from_actions. Best-effort:
    None on any failure (the beaten flag then simply cannot fire for the name)."""
    try:
        ev = yf.Ticker(ticker).upgrades_downgrades
        return breadth_from_actions(ev, asof)
    except Exception:
        return None


def _atm_strike(strikes, spot: float) -> float | None:
    """Strike closest to spot; NaN/None strikes ignored."""
    ks = [float(s) for s in strikes if s is not None and pd.notna(s)]
    return min(ks, key=lambda s: abs(s - spot)) if ks else None


def options_atm(ticker: str, spot: float, today: dt.date | None = None) -> dict | None:
    """ATM IV row (nearest expiry >= OPTIONS_MIN_DTE (21), strike closest to
    spot, IV = mean of the put and call IV at that strike) + liquidity gate on
    the ~35 DTE expiry (OI >= 500, (ask-bid)/mid <= 10%). Best-effort; None on
    failure. The two rows are extracted independently so one bad chain leg
    doesn't kill the other. Doc 08 §4."""
    today = today or dt.date.today()
    if spot is None or not (float(spot) > 0):
        return None
    try:
        tk = yf.Ticker(ticker)
        expiries = [dt.date.fromisoformat(e) for e in tk.options]
        ok = [e for e in expiries if (e - today).days >= config.OPTIONS_MIN_DTE]
        if not expiries or not ok:
            return None
        out: dict = {}

        try:  # --- IV row: nearest expiry >= 21 DTE -------------------------
            exp = min(ok)
            oc = tk.option_chain(exp.isoformat())
            k = _atm_strike(oc.puts["strike"].tolist(), float(spot))
            if k is None:
                k = _atm_strike(oc.calls["strike"].tolist(), float(spot))
            if k is not None:
                prow = oc.puts[oc.puts["strike"] == k]
                crow = oc.calls[oc.calls["strike"] == k]
                if len(prow) and len(crow):
                    ivs = [float(v) for v in
                           (prow["impliedVolatility"].iloc[0], crow["impliedVolatility"].iloc[0])
                           if pd.notna(v)]
                    if ivs:  # average whichever side quotes an IV (NaN on stale legs)
                        out["iv"] = {
                            "iv_expiry": exp.isoformat(), "iv_strike": float(k),
                            "iv_atm": sum(ivs) / len(ivs),
                            "iv_dte": (exp - today).days,
                        }
        except Exception:
            pass

        try:  # --- liquidity gate: 30-45 DTE window, nearest to 35 (doc 08 §4) -----
            near35 = None
            gate_ok = [e for e in ok if 30 <= (e - today).days <= 45]
            if gate_ok:
                near35 = min(gate_ok, key=lambda e: abs((e - today).days - config.OPTIONS_GATE_TARGET_DTE))
                oc35 = tk.option_chain(near35.isoformat())
                k35 = _atm_strike(oc35.puts["strike"].tolist(), float(spot))
            else:
                k35 = None  # nothing listed in the 30-45 window -> no gate row, honestly
            if near35 is not None and k35 is not None:
                p = oc35.puts[oc35.puts["strike"] == k35].iloc[0]
                bid, ask = float(p["bid"]), float(p["ask"])
                mid = (bid + ask) / 2.0
                oi = float(p["openInterest"]) if pd.notna(p["openInterest"]) else 0.0
                spread = (ask - bid) / mid if mid > 0 else None  # mid<=0 (bid=ask=0, crossed) -> no spread
                out["gate"] = {
                    "gate_expiry": near35.isoformat(), "gate_strike": float(k35),
                    "gate_oi": oi, "gate_bid": bid, "gate_ask": ask,
                    "gate_spread_pct": spread,
                    "gate_pass": bool(
                        mid > 0
                        and oi >= config.OPTIONS_GATE_MIN_OI
                        and spread is not None
                        and spread <= config.OPTIONS_GATE_MAX_SPREAD
                    ),
                }
        except Exception:
            pass

        return out or None
    except Exception:
        return None
