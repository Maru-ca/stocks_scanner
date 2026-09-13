"""Streamlit UI for stocks_scanner — reads scan artifacts (data/scans/<date>/).

Two pages: **Ideas** (the default) and **Data manager** (ops). Ideas is one
scrolling page for the manual cash-secured-put workflow: the hunt map (quality
vs residual — cheaper-than-quality to the right), tagged cards for the
highlights, and a company detail that appears once you pick a ticker.
Cheapness on this page means the valuation residual: log(EV/FCF) minus the
multiple this ROIC/industry usually gets; the highlights require the quality
floor, residual < 0, ROIC ≥ 10% and FCF yield ≥ max(4%, 10Y). SELF (vs own 5y
history) stays a chart, never a rank. Every number is display-only — the
scanner ranks, the human decides.

Run: streamlit run dashboard/app.py   (or ./control.sh start)
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import market_data as MD  # noqa: E402  (live put quotes only — never ranks)
SCANS = ROOT / "data" / "scans"

st.set_page_config(page_title="stocks_scanner", layout="wide")

# Menu cleanup: hide the built-in Print / Record-screencast entries (useless here;
# each item carries data-testid="stMainMenuItem-<key>" in this Streamlit version).
st.markdown(
    """
    <style>
      [data-testid="stMainMenuItem-print"],
      [data-testid="stMainMenuItem-recordScreencast"] {
        display: none !important;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

# Column hover docs for the compact table. Mirrors scanner/config.py + docs 05/06 —
# if a threshold changes there, fix this text.
COLUMN_DOCS = {
    "quality_score": (
        "Weighted blend of peer-percentile components: profitability 45% (ROIC, FCF margin, "
        "gross margin) · consistency 25% (of the last 5y: years with ROIC above bar, years "
        "with positive FCF) · accounting 20% (FCF ÷ net income) · balance 10% (net debt/EBITDA, "
        "lower = better). The top 20% of this score = the quality floor."
    ),
    "residual": (
        "log(EV/FCF) minus the multiple this ROIC and industry group usually get "
        "(regression fit). More negative = cheaper than the quality deserves. "
        "The zone cut is residual < 0."
    ),
    "fcf_yield": "TTM adjusted FCF ÷ enterprise value — the cash you'd earn if you owned it.",
    "ev_fcf": "EV / TTM adjusted FCF (SBC-expensed).",
    "price": "Last weekly close.",
    "yield at −5%": "FCF yield you'd lock in if put the stock at −5% (from the options chain).",
}


@st.cache_data(show_spinner=False)
def load_scan(scan_dir: Path, _stamp: float):
    """_stamp = config.json mtime, so a re-scan into the same date folder invalidates
    the cache and the UI picks up new artifacts without a restart."""
    metrics = pd.read_csv(scan_dir / "metrics.csv")
    cfg = json.loads((scan_dir / "config.json").read_text())
    hist = pd.read_parquet(scan_dir / "history.parquet") if (scan_dir / "history.parquet").exists() else pd.DataFrame()
    overlay = pd.read_csv(scan_dir / "overlay.csv") if (scan_dir / "overlay.csv").exists() else pd.DataFrame()
    return metrics, cfg, hist, overlay


def _latest_scan() -> Path | None:
    dirs = sorted(p for p in SCANS.iterdir() if p.is_dir()) if SCANS.exists() else []
    return dirs[-1] if dirs else None


# ------------------------------------------------------------ live put quotes ---
# Cached so slider/select reruns don't hammer yfinance; keys carry today so the
# cache rolls over at midnight. Live quotes never touch the highlights.
@st.cache_data(ttl=90, show_spinner=False)
def _live_expiries(ticker: str, today: str) -> list[str]:
    return [d.isoformat() for d in MD.listed_expiries(ticker, dt.date.fromisoformat(today))]


@st.cache_data(ttl=90, show_spinner=False)
def _live_put(ticker: str, spot: float, expiry: str, target_pct: float, today: str) -> dict | None:
    q = MD.put_quote(ticker, spot, dt.date.fromisoformat(expiry), target_pct,
                     dt.date.fromisoformat(today))
    if q is not None:  # dt.date objects don't survive st.cache_data cleanly
        q["expiry"] = q["expiry"].isoformat()
    return q


@st.cache_data(ttl=3600, show_spinner=False)
def _next_earnings(ticker: str, today: str) -> str | None:
    d = MD.next_earnings_date(ticker, dt.date.fromisoformat(today))
    return d.isoformat() if d else None


def _put_panel(sel: str, spot, gs10, ov_row) -> None:
    """Live standing put on the selected ticker — plus an event quote when the
    next earnings print is 0-5 days out. Display-only: never hides or ranks."""
    st.markdown("**Put premium (live)**")
    st.caption("Quotes are live from the option chain — not scan-as-of. "
               "Cash % = premium ÷ strike.")
    today = dt.date.today()
    exps = _live_expiries(sel, today.isoformat())
    if not exps:
        st.caption("No option chain available right now.")
        return
    dte_of = {e: (dt.date.fromisoformat(e) - today).days for e in exps}
    ge21 = [e for e in exps if dte_of[e] >= 21]
    default = (min(exps, key=lambda e: abs(dte_of[e] - 35)) if ge21
               else max(exps, key=lambda e: dte_of[e]))
    label_of = {f"{dt.date.fromisoformat(e):%a %b %d} · {dte_of[e]} DTE": e for e in exps}
    label = st.selectbox("Expiry", list(label_of.keys()),
                         index=list(label_of).index(next(l for l, e in label_of.items() if e == default)))
    expiry = label_of[label]
    dte = dte_of[expiry]

    ned = None
    ned_s = _next_earnings(sel, today.isoformat())
    if ned_s:
        ned = dt.date.fromisoformat(ned_s)
    elif ov_row is not None and pd.notna(ov_row.get("days_to_earnings")):
        ned = today + dt.timedelta(days=int(ov_row["days_to_earnings"]))
    event_days = (ned - today).days if ned else None
    in_event_window = event_days is not None and 0 <= event_days <= 5

    def _quote_row(role, exp_str, target_pct, annualize_ok, notes=""):
        q = _live_put(sel, float(spot), exp_str, target_pct, today.isoformat())
        if q is None:
            return {"": role, "expiry": dt.date.fromisoformat(exp_str), "DTE": dte_of[exp_str],
                    "strike": "—", "bid": "—", "mid": "—", "cash % of strike": "no quote",
                    "annualized": "—", "OI": "—", "notes": notes or "no chain for this leg"}
        p = MD.premium_pct_of_strike(q)
        a = MD.annualized_premium(p, q["dte"]) if annualize_ok else None
        return {"": role, "expiry": dt.date.fromisoformat(exp_str), "DTE": q["dte"],
                "strike": f"${q['strike']:,.1f}",
                "bid": f"{q['bid']:.2f}" if q["bid"] is not None else "—",
                "mid": f"{q['mid']:.2f}" if q["mid"] is not None else "—",
                "cash % of strike": f"{p:.2%}" if p is not None else "no quote",
                "annualized": f"{a:.0%}" if a is not None else "—",
                "OI": f"{q['oi']:,.0f}" if q["oi"] is not None else "—",
                "notes": notes, "_a": a, "_weak": bool(q.get("weak_quote"))}

    rows = []
    if in_event_window:  # expiry on/before the print, ATM, raw cash % only
        cand = [e for e in exps if dt.date.fromisoformat(e) <= ned]
        e_exp = cand[-1] if cand else exps[0]
        rows.append(_quote_row("Event", e_exp, 1.00, annualize_ok=False,
                               notes="earnings in window · not annualized (event premium)"))

    notes = []
    row = _quote_row("Standing", expiry, MD.strike_target_pct(dte), annualize_ok=True)
    if row.get("_a") is not None and gs10 and row["_a"] < gs10:
        notes.append("low premium")
    if row.get("_weak"):
        notes.append("weak quote")
    if ned and dt.date.fromisoformat(expiry) >= ned and not in_event_window:
        notes.append("earnings in window")
    if dte < 21:
        notes.append("short DTE — cash % shown raw, not annualized")
    row["notes"] = " · ".join(notes) if notes else row["notes"]
    rows.append(row)
    for r in rows:
        r.pop("_a", None), r.pop("_weak", None)

    st.dataframe(pd.DataFrame(rows)[["", "expiry", "DTE", "strike", "bid", "mid",
                                     "cash % of strike", "annualized", "OI", "notes"]],
                 width="stretch", hide_index=True)
    st.caption("Annualized = cash % × 365/DTE, shown only from 21 DTE. `low premium` = "
               "annualized below the 10Y Treasury — a note, never a filter.")


def _fmt(v, spec: str) -> str:
    """NaN-safe number formatting — row.get(...) on a Series yields NaN, not None."""
    try:
        return spec.format(v) if v is not None and pd.notna(v) else "—"
    except (TypeError, ValueError):
        return "—"


def _gated(metrics: pd.DataFrame) -> pd.DataFrame:
    """Scored names that pass gates + coverage — the population the page ranks
    (needs a QualityScore; the zone additionally needs a residual)."""
    return metrics[
        metrics["quality_score"].notna()
        & metrics["gates_pass"].fillna(False)
        & (metrics["input_coverage"] >= 0.8)
    ]


def _highlighted(metrics: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """The highlights: gated + quality floor + residual < 0 + absolute holdability —
    ROIC ≥ 10% (5y avg) and FCF yield ≥ max(4%, 10Y). Missing ROIC or yield fails.
    Ranked by residual ascending: most under-priced vs its quality first."""
    gs10 = cfg.get("gs10") if cfg.get("gs10") is not None else 0.0
    bar = max(0.04, gs10 or 0)
    g = _gated(metrics)
    hi = g[
        g["quality_floor_pass"].fillna(False)
        & g["residual"].notna() & (g["residual"] < 0)
        & g["roic"].notna() & (g["roic"] >= 0.10)
        & g["fcf_yield"].notna() & (g["fcf_yield"] >= bar)
    ]
    return hi.sort_values("residual", ascending=True)


def _row_tags(row) -> list:
    """Chips for one metrics row: sentiment tag + holdability warnings."""
    tags = []
    if bool(row.get("flag_beaten_but_delivering") or False):
        tags.append(("beaten", "#4e79a7"))
    if any(pd.notna(row.get(k)) and float(row.get(k)) < t for k, t in [
        ("brake_gm", 0.97), ("brake_fcf_margin", 0.95), ("brake_roic", 0.90),
    ]):
        tags.append(("⚠ margins", "#b07d2b"))
    roic = row.get("roic")
    if pd.notna(roic) and float(roic) < 0.10:
        tags.append(("⚠ ROIC < 10%", "#8ea0b5"))
    if str(row.get("sector") or "") in ("Energy", "Materials"):
        tags.append(("⚠ cycle", "#76b7b2"))
    return tags


def _chips_html(tags: list) -> str:
    return "".join(
        f"<span style='font-size:.62em;padding:1px 7px;border-radius:9px;"
        f"color:{c};border:1px solid {c};margin-left:4px;white-space:nowrap'>{t}</span>"
        for t, c in tags
    )


# ------------------------------------------------------------- card graphics ---
def _spark_svg(values: list, w: int = 190, h: int = 42) -> str:
    """Tiny inline SVG line of a series with the last point marked. Lower line =
    cheaper, so green when the current point sits in the cheap half of its range."""
    vs = [float(v) for v in values if v is not None and pd.notna(v)]
    if len(vs) < 3:
        return f"<div style='height:{h}px;font-size:.75em;opacity:.4'>no history</div>"
    lo, hi = min(vs), max(vs)
    rng = (hi - lo) or 1.0
    n = len(vs)
    xs = [3 + i * (w - 8) / (n - 1) for i in range(n)]
    ys = [h - 5 - (v - lo) / rng * (h - 12) for v in vs]
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    color = "#59a14f" if vs[-1] <= lo + rng / 2 else "#d9a03c"
    return (
        f"<svg width='100%' height='{h}' viewBox='0 0 {w} {h}' "
        f"preserveAspectRatio='none' style='display:block'>"
        f"<polyline points='{pts}' fill='none' stroke='{color}' stroke-width='1.7' "
        f"stroke-linejoin='round' stroke-linecap='round' vector-effect='non-scaling-stroke'/>"
        f"<circle cx='{xs[-1]:.1f}' cy='{ys[-1]:.1f}' r='2.8' fill='{color}'/>"
        f"</svg>"
    )


def _score_bar(label: str, value, color: str) -> str:
    v = 0.0 if value is None or pd.isna(value) else float(value)
    return (
        f"<div style='display:flex;align-items:center;gap:8px;margin:3px 0'>"
        f"<span style='width:70px;font-size:.72em;opacity:.7'>{label}</span>"
        f"<div style='flex:1;background:rgba(128,128,128,.18);border-radius:4px;height:8px'>"
        f"<div style='width:{max(0.0, min(100.0, v)):.0f}%;background:{color};"
        f"border-radius:4px;height:8px'></div></div>"
        f"<span style='width:26px;text-align:right;font-size:.72em;opacity:.8'>{v:.0f}</span></div>"
    )


def _name_cards(df: pd.DataFrame, hist: pd.DataFrame, ov_by_t: pd.DataFrame,
                tags: dict, n: int = 8) -> None:
    """Graphical cards for the zone names, most negative residual first: 5y EV/FCF
    sparkline (chart only — SELF never ranks), quality bar, and the cash figures."""
    cards = []
    for _, r in df.head(n).iterrows():
        y: list = []
        if not hist.empty and "ticker" in hist.columns:
            g = hist[(hist["ticker"] == r["ticker"]) & hist["ev_fcf"].notna()].sort_values("week")
            y = g["ev_fcf"].tail(260).tolist()
        med = float(pd.Series(y).median()) if y else None
        price = r.get("price")
        price_s = f"${price:,.0f}" if pd.notna(price) else "—"
        fy = r.get("fcf_yield")
        fy_col = ("#2e7d32" if pd.notna(fy) and fy >= 0.05
                  else "#b07d2b" if pd.notna(fy) and fy >= 0.03 else "#888888")
        fy_s = f"{fy:.1%}" if pd.notna(fy) else "—"
        ev = r.get("ev_fcf")
        ev_s = f"{ev:.1f}x" if pd.notna(ev) else "—"
        med_s = f" · 5y median {med:.1f}x" if med else ""
        res = r.get("residual")
        res_s = f"residual {res:.2f}" if pd.notna(res) else ""
        t = r["ticker"]
        y95 = None
        if not ov_by_t.empty and t in ov_by_t.index:
            v = ov_by_t.loc[t, "fcf_yield_strike_95"]
            y95 = f"{v:.1%}" if pd.notna(v) else None
        assign = f" · at −5%: {y95}" if y95 else ""
        sector = str(r.get("sector") or "")[:20]
        name = str(r.get("name") or "")[:22]
        cards.append(
            f"<div style='background:rgba(128,128,128,.07);border:1px solid rgba(128,128,128,.22);"
            f"border-radius:12px;padding:12px 14px'>"
            f"<div style='font-size:1.05em'><b>{t}</b> "
            f"<span style='opacity:.65;font-size:.85em'>{name}</span>{_chips_html(tags.get(t, []))}</div>"
            f"<div style='font-size:.72em;opacity:.6;margin-bottom:6px'>{sector} · {price_s}</div>"
            f"{_spark_svg(y)}"
            f"<div style='margin-top:6px'>{_score_bar('Quality', r.get('quality_score'), '#59a14f')}</div>"
            f"<div style='font-size:.75em;margin-top:6px'>"
            f"<span style='color:{fy_col};font-weight:600'>FCF yield {fy_s}</span>"
            f"<span style='opacity:.7'> · {res_s} · EV/FCF {ev_s}{med_s}{assign}</span></div>"
            f"</div>"
        )
    st.markdown(
        "<div style='display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));"
        f"gap:12px'>{''.join(cards)}</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------- the charts ---
def _hunt_map(metrics: pd.DataFrame, hi: pd.DataFrame, cfg: dict, selected: str | None) -> None:
    """Quality (y) × residual (x, plotted as −residual so cheaper sits RIGHT): grey =
    scored names with a residual, orange = the highlights (floor + residual < 0
    + ROIC ≥ 10% + FCF yield ≥ max(4%, 10Y)). The selected ticker is starred.
    The selected ticker is starred."""
    pts = metrics[metrics["quality_score"].notna() & metrics["residual"].notna()].copy()
    pts["x"] = -pts["residual"]          # cheaper (more negative residual) -> right
    member = set(hi["ticker"]) if not hi.empty else set()
    fig = go.Figure()
    rest = pts[~pts["ticker"].isin(member)]
    zone = pts[pts["ticker"].isin(member)]
    for sub, color, name, size, op in [
        (rest, "#8ea0b5", "scored names", 8, 0.45),
        (zone, "#e4572e", "in the hunt zone", 12, 0.95),
    ]:
        if sub.empty:
            continue
        hover = sub["ticker"] if "name" not in sub.columns else sub["ticker"] + " — " + sub["name"]
        is_zone = name == "in the hunt zone"
        fig.add_trace(go.Scatter(
            x=sub["x"], y=sub["quality_score"],
            mode="markers+text" if is_zone and len(sub) <= 12 else "markers",
            text=sub["ticker"] if is_zone and len(sub) <= 12 else None,
            textposition="top center", textfont=dict(size=9),
            name=name, hovertext=hover, hoverinfo="text",
            marker=dict(size=size, color=color, opacity=op, line=dict(width=1, color="white")),
        ))
    if selected and not pts.empty and (pts["ticker"] == selected).any():
        s = pts[pts["ticker"] == selected].iloc[0]
        fig.add_trace(go.Scatter(
            x=[s["x"]], y=[s["quality_score"]],
            mode="markers+text", text=[selected], textposition="bottom center",
            textfont=dict(size=11, color="#e4572e"),
            showlegend=False, hoverinfo="skip",
            marker=dict(size=18, color="#e4572e", symbol="star",
                        line=dict(width=2, color="white")),
        ))
    thr = cfg.get("quality_floor_threshold")
    if not pts.empty:
        pad = (pts["x"].max() - pts["x"].min()) * 0.05 or 0.1
        x0, x1 = float(pts["x"].min() - pad), float(pts["x"].max() + pad)
        if thr is not None and pd.notna(thr):
            fig.add_hline(y=thr, line_dash="dot", line_color="#666",
                          annotation_text=f"quality floor — top 20% (≈{thr:.0f})")
            # the zone: above the floor AND residual < 0 (x = −residual > 0)
            fig.add_shape(type="rect", x0=0, x1=x1, y0=thr, y1=108,
                          fillcolor="rgba(228,87,46,0.07)", line_width=0)
            fig.add_annotation(x=(0 + x1) / 2, y=107, text="the hunt zone", showarrow=False,
                               font=dict(color="#e4572e", size=11))
        fig.update_layout(xaxis_range=[x0, x1])
    fig.update_layout(
        height=520,
        xaxis_title="cheaper than the ROIC-implied multiple → (−residual)",
        yaxis_title="higher quality ↑ (QualityScore)",
        title=f"Quality vs price-for-quality — {len(zone)} names in the highlights",
        hovermode="closest",
    )
    st.plotly_chart(fig, width="stretch")
    st.caption(
        "Grey = scored names with a residual; the shaded region is residual < 0 above the "
        "quality floor. Orange is the tighter highlights list: floor + residual < 0 + "
        "ROIC ≥ 10% + FCF yield ≥ max(4%, 10Y) — cash-cheap and viable, not just "
        "under-priced vs peers. Those names continue below, most negative residual first."
    )


def _detail_charts(h: pd.DataFrame, sel: str) -> None:
    """The four history charts from the persisted weekly frame (no scanner changes)."""
    g1, g2 = st.columns(2)
    with g1:
        p = h["price"].dropna()
        if not p.empty:
            hi52 = float(p.tail(52).max())
            last = float(p.iloc[-1])
            dd = last / hi52 - 1 if hi52 > 0 else float("nan")
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=h.loc[p.index, "week"], y=p, name="price",
                                     line=dict(color="#4e79a7")))
            fig.add_hline(y=hi52, line_dash="dot", line_color="#666",
                          annotation_text=f"52w high ${hi52:,.0f}")
            fig.add_trace(go.Scatter(x=[h.loc[p.index[-1], "week"]], y=[last], mode="markers",
                                     showlegend=False, marker=dict(size=10, color="#e4572e"),
                                     hovertemplate=f"<b>{sel} ${last:,.2f} ({dd:.0%} off high)</b><extra></extra>"))
            fig.update_layout(height=300, title=f"Price — {dd:.0%} off the 52-week high",
                              yaxis_title="$")
            st.plotly_chart(fig, width="stretch")
        else:
            st.caption("No price history for this name.")
    with g2:
        e = h["ev_fcf"].dropna()
        if not e.empty:
            med = float(e.median())
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=h.loc[e.index, "week"], y=e, name="EV/FCF",
                                     line=dict(color="#76b7b2")))
            fig.add_hline(y=med, line_dash="dot", annotation_text=f"5y median {med:.1f}x")
            fig.add_hline(y=0.8 * med, line_dash="dash", line_color="#2ca02c",
                          annotation_text="0.8× median")
            cur = e.iloc[-1]
            fig.add_trace(go.Scatter(x=[h.loc[e.index[-1], "week"]], y=[cur], mode="markers",
                                     showlegend=False, marker=dict(size=10, color="#e4572e", symbol="diamond"),
                                     hovertemplate=f"<b>{sel} now: {cur:.1f}x</b><extra></extra>"))
            fig.update_layout(height=300, title="EV/FCF vs its own 5y history", yaxis_title="×")
            st.plotly_chart(fig, width="stretch")
        else:
            st.caption("No valid EV/FCF history for this name.")
    g3, g4 = st.columns(2)
    with g3:
        fy = (h["fcf_adj_ttm"] / h["ev"]).where(h["ev"] > 0).dropna()
        if not fy.empty:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=h.loc[fy.index, "week"], y=fy, name="FCF yield",
                                     line=dict(color="#59a14f")))
            fig.add_hline(y=float(fy.iloc[-1]), line_dash="dot", line_color="#666",
                          annotation_text=f"now {fy.iloc[-1]:.1%}")
            fig.update_layout(height=300, title="FCF yield over time (TTM FCF ÷ EV)",
                              yaxis_tickformat=".0%")
            st.plotly_chart(fig, width="stretch")
        else:
            st.caption("No FCF-yield history for this name.")
    with g4:
        if h["fcf_adj_ttm"].notna().any():
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=h["week"], y=h["fcf_adj_ttm"] / 1e9, name="FCF (adj, TTM)"))
            fig.add_trace(go.Scatter(x=h["week"], y=h["ni_ttm"] / 1e9, name="Net income (TTM)"))
            fig.update_layout(height=300, title="Cash vs earnings (as-known TTM)", yaxis_title="$B")
            st.plotly_chart(fig, width="stretch")
        else:
            st.caption("No cash-flow history for this name.")


def _margin_snapshot(row) -> None:
    """brake_* ratios ARE TTM ÷ 5y average — a one-row snapshot, not a time series."""
    rows = []
    for label, key, floor in [
        ("Gross margin", "brake_gm", 0.97),
        ("FCF margin", "brake_fcf_margin", 0.95),
        ("ROIC", "brake_roic", 0.90),
    ]:
        v = row.get(key)
        ok = pd.notna(v) and float(v) >= floor
        rows.append({
            "margin": label,
            "TTM vs 5y avg": _fmt(v, "{:.2f}×"),
            "floor": f"{floor:.2f}×",
            "": "✓" if ok else ("⚠ below floor" if pd.notna(v) else "— no data"),
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    st.caption(
        "Each ratio is this year's margin ÷ its 5-year average; below the floor is what the "
        "⚠ margins chip on the card means — look before you leap."
    )


def _company_detail(sel: str, row, hist: pd.DataFrame, overlay: pd.DataFrame,
                    ov_by_t: pd.DataFrame, tags: dict, gs10) -> None:
    h = (hist[(hist["ticker"] == sel)].sort_values("week")
         if not hist.empty and "ticker" in hist.columns else pd.DataFrame())
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Quality (0–100)", _fmt(row.get("quality_score"), "{:.0f}"),
              "top 20% passes the gate")
    m2.metric("Residual", _fmt(row.get("residual"), "{:.2f}"),
              "more negative = cheaper for the quality")
    m3.metric("FCF yield", _fmt(row.get("fcf_yield"), "{:.2%}"))
    m4.metric("EV/FCF vs own history", _fmt(row.get("ev_fcf_self_pct"), "{:.0f}"),
              "0 = cheapest ever — chart, not a score")
    if tags.get(sel):
        st.markdown("This name: " + _chips_html(tags.get(sel, [])).replace(
            "font-size:.62em", "font-size:.8em"), unsafe_allow_html=True)

    st.markdown("**The assignment test** — would you own it at the strike?")
    if not ov_by_t.empty and sel in ov_by_t.index:
        o = ov_by_t.loc[sel]
        p = o.get("price")
        y95, y90 = o.get("fcf_yield_strike_95"), o.get("fcf_yield_strike_90")
        d1, d2, d3 = st.columns(3)
        d1.metric("Own it at −5%?", _fmt(y95, "{:.1%}"),
                  f"strike ≈ ${p * 0.95:,.0f}" if pd.notna(p) else None)
        d2.metric("Own it at −10%?", _fmt(y90, "{:.1%}"),
                  f"strike ≈ ${p * 0.90:,.0f}" if pd.notna(p) else None)
        ned_s = _next_earnings(sel, dt.date.today().isoformat())
        de = ((dt.date.fromisoformat(ned_s) - dt.date.today()).days if ned_s
              else o.get("days_to_earnings"))
        d3.metric("Days to earnings", _fmt(de, "{:.0f}"),
                  "event premium window" if pd.notna(de) and de <= 5 else None)
        if pd.notna(o.get("iv_atm")):
            st.caption(f"IV {_fmt(o.get('iv_atm'), '{:.0%}')} vs 30d HV {_fmt(o.get('hv_30d'), '{:.0%}')} "
                       "(scan snapshot — the live panel below has today's chain).")
    else:
        st.info("Options were not pulled for this name (the overlay covers the top "
                "quality-floor names only).")

    _put_panel(sel, row.get("price"), gs10, ov_by_t.loc[sel] if (not ov_by_t.empty and sel in ov_by_t.index) else None)

    if not h.empty:
        _detail_charts(h, sel)
    else:
        st.info("No weekly history for this name in this scan.")
    st.markdown("**Margins — still intact?**")
    _margin_snapshot(row)


# --------------------------------------------------------------- ideas page ---
def ideas_page():
    scan_dir = _latest_scan()
    if scan_dir is None or not (scan_dir / "config.json").exists():
        st.error("No scans on disk yet. Open **Data manager** and run a scan.")
        return
    cfg_path = scan_dir / "config.json"
    refreshed_at = dt.datetime.fromtimestamp(cfg_path.stat().st_mtime)
    metrics, cfg, hist, overlay = load_scan(scan_dir, cfg_path.stat().st_mtime)

    hi = _highlighted(metrics, cfg)
    tags = {r["ticker"]: _row_tags(r) for _, r in metrics.iterrows()}
    ov_by_t = overlay.set_index("ticker") if not overlay.empty else pd.DataFrame()

    st.title("Ideas")
    sub = cfg.get("subset") or {}
    if sub.get("tickers"):
        sub_txt = f"subset of {len(sub['tickers'])} tickers"
    elif sub.get("limit"):
        sub_txt = f"subset: first {sub['limit']} names"
    else:
        sub_txt = "full standard group"
    st.caption(
        f"Scan as-of **{cfg.get('asof', scan_dir.name)}** · data as known at that date · "
        f"{sub_txt} · caches and refresh live on the Data manager page"
    )

    floor_n = int(_gated(metrics)["quality_floor_pass"].fillna(False).sum())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Data as of", cfg.get("asof", scan_dir.name))
    c2.metric("Last refresh", f"{refreshed_at:%Y-%m-%d %H:%M}")
    c3.metric("10Y Treasury — the yield bar", _fmt(cfg.get("gs10"), "{:.2%}"))
    c4.metric("Names in the highlights", len(hi), f"of {floor_n} above the quality floor")

    # 1 — the map
    _hunt_map(metrics, hi, cfg, st.session_state.get("idea_pick"))

    if hi.empty:
        st.info(
            "No names clear the highlights bar in this scan — quality floor, residual < 0, "
            "ROIC ≥ 10% and FCF yield ≥ max(4%, 10Y). Run a fresh scan from the Data manager page."
        )
        return

    # 2 — the cards + the compact table (the browse surface)
    st.markdown(
        f"**The highlights, cheapest for their quality first** — {len(hi)} names "
        "(quality floor · residual < 0 · ROIC ≥ 10% · FCF yield ≥ max(4%, 10Y)). "
        "`beaten` = sector-laggard price action with analyst support · "
        "`⚠ margins` = a margin ratio slipped below its floor · "
        "`⚠ ROIC < 10%` = thin returns in absolute terms · "
        "`⚠ cycle` = Energy or Materials — earnings follow the cycle."
    )
    _name_cards(hi, hist, ov_by_t, tags, n=8)

    table = hi.reset_index(drop=True)
    show = table[["ticker", "name", "sector", "price", "quality_score",
                  "residual", "fcf_yield", "ev_fcf"]].copy()
    show["yield at −5%"] = table["ticker"].map(
        lambda t: f"{ov_by_t.loc[t, 'fcf_yield_strike_95']:.1%}"
        if (not ov_by_t.empty and t in ov_by_t.index
            and pd.notna(ov_by_t.loc[t, "fcf_yield_strike_95"])) else "—")
    show["tags"] = table["ticker"].map(lambda t: " · ".join(x for x, _ in tags.get(t, [])) or "—")
    for c, f in {"price": "{:.2f}", "quality_score": "{:.0f}", "residual": "{:.2f}",
                 "fcf_yield": "{:.2%}", "ev_fcf": "{:.1f}"}.items():
        show[c] = show[c].map(lambda v, f=f: f.format(v) if pd.notna(v) else "—")
    col_cfg = {c: st.column_config.Column(label=c, help=COLUMN_DOCS.get(c)) for c in show.columns}
    event = st.dataframe(show, column_config=col_cfg, width="stretch", height=300,
                         hide_index=True, on_select="rerun", selection_mode="single-row",
                         key="ideas_table")

    picked = None
    try:
        rows = event.selection.rows
        if rows:
            picked = table.loc[rows[0], "ticker"]
    except Exception:
        picked = None
    tickers = table["ticker"].tolist()
    if picked in tickers:
        st.session_state["idea_pick"] = picked
    if st.session_state.get("idea_pick") not in tickers:
        st.session_state["idea_pick"] = tickers[0]
    sel = st.selectbox("Inspect a company", tickers, key="idea_pick")

    # 3 — the detail, under everything, only once a name is picked
    row = table[table["ticker"] == sel].iloc[0]
    st.divider()
    st.subheader(f"{sel} — {row.get('name') or ''}")
    _company_detail(sel, metrics[metrics["ticker"] == sel].iloc[0], hist, overlay, ov_by_t,
                    tags, cfg.get("gs10"))


# --------------------------------------------------------- data manager page --
def manager_page():
    import data_manager
    data_manager.render()


# ------------------------------------------------------------------ routing --
pg = st.navigation([
    st.Page(ideas_page, title="Ideas", icon="💡", default=True, url_path="ideas"),
    st.Page(manager_page, title="Data manager", icon="🗂️", url_path="data"),
])
pg.run()
