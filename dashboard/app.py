"""Streamlit dashboard for stocks_scanner — reads scan artifacts (data/scans/<date>/).

Two pages (top-right menu / sidebar navigation): the Dashboard (preset tables,
quadrant scatter, drill-down, put overlay) and the Data manager (cache visibility +
graphical refresh). The dashboard always serves the LATEST scan; the header shows
when it was last refreshed. Older scans remain inspectable from the Data manager.

Run: streamlit run dashboard/app.py   (or ./control.sh start)
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
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

PRESETS = {
    "Quality at a Discount (flagship)": "presets/flagship.csv",
    "Beaten but Delivering": "presets/beaten_but_delivering.csv",
    "Compounders on Sale": "presets/compounders_on_sale.csv",
}

FLOOR_KEY = "Quality floor passers (wide)"

# Column hover docs for the preset table (st.column_config help tooltips).
# Mirrors scanner/config.py weights + docs 05/06 — if a threshold changes, fix this text.
COLUMN_DOCS = {
    "quality_score": (
        "Weighted blend of peer-percentile components: profitability 45% (ROIC, FCF margin, "
        "gross margin) · consistency 25% (of the last 5y: years with ROIC above bar, years "
        "with positive FCF) · accounting 20% (FCF ÷ net income) · balance 10% (net debt/EBITDA, "
        "lower = better). Percentiles are computed vs an industry-group → sector → market "
        "ladder. The top 20% of this score = the quality floor (the gate)."
    ),
    "cheapness_score": (
        "0–100 blend: own-history EV/FCF percentile 40% (ranked against the market "
        "cross-section) · peer-ladder EV/EBIT percentile 30% · FCF yield on a fixed 0–10% "
        "scale 30%. Higher = cheaper."
    ),
    "residual": (
        "log(EV/FCF) minus what the regression says the market pays for that ROIC and "
        "industry group. Negative = cheaper than its fundamentals predict."
    ),
    "residual_pct": (
        "Percentile of the residual among quality-floor passers (midrank). Low = most "
        "under-priced vs peers; the flagship ranks by this."
    ),
    "ev_ebit": "EV / TTM EBIT — peer-multiple view of cheapness.",
    "ev_fcf": "EV / TTM adjusted FCF (SBC-expensed).",
    "fcf_yield": "TTM adjusted FCF ÷ enterprise value.",
    "ev_fcf_self_pct": (
        "EV/FCF percentile within the stock's OWN 5y weekly history (0 = cheapest ever, "
        "100 = most expensive). Flag arm: ≤ 20."
    ),
    "ev_fcf_self_z": (
        "z-score of current EV/FCF vs its own 5y history. Flag arm: ≤ −1.0."
    ),
    "brake_gm": "TTM gross margin ÷ its 5y average. Flag brake: ≥ 0.97.",
    "brake_fcf_margin": "TTM adjusted-FCF margin ÷ its 5y average. Flag brake: ≥ 0.95.",
    "brake_roic": "TTM ROIC ÷ its 5y average. Flag brake: ≥ 0.90.",
    "composite": "Display-only blend of the two scores — nothing is ranked by it (doc 05 §2.3).",
    "price": "Last weekly close.",
}

SCORE_DOC = """
**QualityScore** — the gate (top 20% passes):

| component | weight | inputs |
|---|---|---|
| profitability | 45% | ROIC, FCF margin, gross margin (peer percentiles) |
| consistency | 25% | of last 5y: years ROIC above bar, years FCF positive |
| accounting | 20% | FCF ÷ net income |
| balance | 10% | net debt / EBITDA (lower = better) |

Every input is winsorized and percentile-ranked against a peer ladder:
industry group (≥ 8 names) → sector (≥ 8) → market. Missing components
renormalize the weights (recorded per stock, never silently filled).

**CheapnessScore** — the rank (higher = cheaper): own-history EV/FCF
percentile 40% · peer EV/EBIT 30% · FCF yield (0–10% fixed scale) 30%.

**Valuation residual** — the flagship's ranking key: log(EV/FCF) minus what
a regression says the market pays for that ROIC + industry group. Low
residual percentile = cheap vs peers.

**Thesis:** quality filters, cheapness ranks, sentiment flags. Hover any
column header in the preset table for its definition.
"""

# Plain-language preset contract, mirroring scanner/config.py + scanner/flags.py.
# If a threshold below ever disagrees with config.py, fix this text.
PRESET_INFO = {
    "Quality at a Discount (flagship)": {
        "filters": [
            "passes the trading gates (ADV ≥ $20M, tenure ≥ 1y) with ≥80% of score inputs",
            "QualityScore in the **top 20%** of the market (the quality floor)",
            "multiple de-rated vs its **own 5y history** — any of: SELF percentile ≤ 20 · z-score ≤ −1.0 · EV/FCF ≤ 0.8× its 5y median",
            "cheap vs **peers** — any of: FCF yield ≥ max(4%, 10Y Treasury) · valuation residual ≤ 20th percentile",
            "fundamentals intact (brakes): gross margin ≥ 97% · FCF margin ≥ 95% · ROIC ≥ 90% of their 5y averages",
        ],
        "rank": "valuation residual ascending — most under-priced vs peers first",
        "note": ("The tight thesis screen (the Adobe/Oracle hunt). Single-digit counts are it "
                 "working as designed: it only fires when quality is demonstrably intact *and* "
                 "the multiple has clearly de-rated."),
    },
    "Beaten but Delivering": {
        "filters": [
            "QualityScore in the **top 20%** of the market (the quality floor)",
            "12-1m price momentum in the **bottom quartile of its sector** — the “beaten” part",
            "analyst revision breadth **positive** over the last 90 days — more upgrades and price-target raises than downgrades and lowers — the “delivering” part",
        ],
        "rank": "CheapnessScore descending",
        "note": ("Revision breadth comes from yfinance's analyst-action feed (free, no API "
                 "key). v0.5.3 replaced the FMP consensus-EPS proxy with these rating/target "
                 "events: observable immediately, no 90-day vintage warm-up."),
    },
    "Compounders on Sale": {
        "filters": [
            "consistency quality component ≥ **80/100**",
            "EV/FCF in the **bottom 30% of its own 5y range** (self-history discount)",
            "plus the flagship's quality floor, margin brakes and cheap-vs-peers arm",
        ],
        "rank": "CheapnessScore descending",
        "note": ("Deliberately the strictest screen — the v0.4 review added the flagship brakes "
                 "because without them this was “the junk list with a nice title”."),
    },
    FLOOR_KEY: {
        "filters": [
            "passes the trading gates (ADV ≥ $20M, tenure ≥ 1y) with ≥80% of score inputs",
            "QualityScore in the **top 20%** of the market (the quality gate)",
        ],
        "rank": "CheapnessScore descending — cheapest high-quality names first",
        "note": ("The wide hunt list. No de-rating or brake requirements: every quality-gated "
                 "name with its cheapness rank. Browse here first, then use Drill-down to ask "
                 "why a name is cheap."),
    },
}


@st.cache_data(show_spinner=False)
def load_scan(scan_dir: Path, _stamp: float):
    """_stamp = config.json mtime, so a re-scan into the same date folder invalidates
    the cache and the UI picks up new artifacts without a restart."""
    metrics = pd.read_csv(scan_dir / "metrics.csv")
    cfg = json.loads((scan_dir / "config.json").read_text())
    hist = pd.read_parquet(scan_dir / "history.parquet") if (scan_dir / "history.parquet").exists() else pd.DataFrame()
    presets = {name: pd.read_csv(scan_dir / rel) for name, rel in PRESETS.items() if (scan_dir / rel).exists()}
    overlay = pd.read_csv(scan_dir / "overlay.csv") if (scan_dir / "overlay.csv").exists() else pd.DataFrame()
    return metrics, cfg, hist, presets, overlay


def _latest_scan() -> Path | None:
    dirs = sorted(p for p in SCANS.iterdir() if p.is_dir()) if SCANS.exists() else []
    return dirs[-1] if dirs else None


def _fmt(v, spec: str) -> str:
    """NaN-safe number formatting — row.get(...) on a Series yields NaN, not None."""
    try:
        return spec.format(v) if v is not None and pd.notna(v) else "—"
    except (TypeError, ValueError):
        return "—"


def _gated(metrics: pd.DataFrame) -> pd.DataFrame:
    """Scored names that pass gates + coverage — the population every preset draws from."""
    return metrics[
        metrics["quality_score"].notna()
        & metrics["cheapness_score"].notna()
        & metrics["gates_pass"].fillna(False)
        & (metrics["input_coverage"] >= 0.8)
    ]


def _floor_passers(metrics: pd.DataFrame) -> pd.DataFrame:
    g = _gated(metrics)
    return g[g["quality_floor_pass"].fillna(False)].sort_values("cheapness_score", ascending=False)


def _funnel_counts(metrics: pd.DataFrame) -> tuple[int, int, int, int]:
    g = _gated(metrics)
    floor = int(g["quality_floor_pass"].fillna(False).sum())
    flags = int(g["flag_derated_quality"].fillna(False).sum()) if "flag_derated_quality" in g else 0
    return len(metrics), len(g), floor, flags


def _funnel_fig(metrics: pd.DataFrame) -> go.Figure:
    total, scored, floor, flags = _funnel_counts(metrics)
    fig = go.Figure(go.Funnel(
        y=["Analyzed", "Scored & liquid", "Above the quality floor", "The hunt (flagship)"],
        x=[total, scored, floor, flags],
        textinfo="value",
        marker={"color": ["#8ea0b5", "#4e79a7", "#59a14f", "#e4572e"]},
        connector={"line": {"color": "rgba(128,128,128,0.25)"}},
    ))
    fig.update_layout(height=240, margin=dict(l=8, r=8, t=4, b=4), showlegend=False)
    return fig


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


def _name_cards(df: pd.DataFrame, hist: pd.DataFrame, preset_name: str, n: int = 6) -> None:
    """Graphical preset cards: 5y EV/FCF sparkline + score bars + headline stats."""
    info = PRESET_INFO.get(preset_name, {})
    st.caption(
        f"Top {min(n, len(df))} of **{preset_name}** — ranked by {info.get('rank', '—')}. "
        "The line is 5 years of EV/FCF: falling means the market pays less for the same cash flow."
    )
    cards = []
    for _, r in df.head(n).iterrows():
        y: list = []
        med = None
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
        sector = str(r.get("sector") or "")[:20]
        name = str(r.get("name") or "")[:22]
        cards.append(
            f"<div style='background:rgba(128,128,128,.07);border:1px solid rgba(128,128,128,.22);"
            f"border-radius:12px;padding:12px 14px'>"
            f"<div style='font-size:1.05em'><b>{r['ticker']}</b> "
            f"<span style='opacity:.65;font-size:.85em'>{name}</span></div>"
            f"<div style='font-size:.72em;opacity:.6;margin-bottom:6px'>{sector} · {price_s}</div>"
            f"{_spark_svg(y)}"
            f"<div style='margin-top:6px'>"
            f"{_score_bar('Quality', r.get('quality_score'), '#59a14f')}"
            f"{_score_bar('Cheapness', r.get('cheapness_score'), '#4e79a7')}</div>"
            f"<div style='font-size:.75em;margin-top:6px'>"
            f"<span style='color:{fy_col};font-weight:600'>FCF yield {fy_s}</span>"
            f"<span style='opacity:.7'> · EV/FCF {ev_s}{med_s}</span></div>"
            f"</div>"
        )
    st.markdown(
        "<div style='display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));"
        f"gap:12px'>{''.join(cards)}</div>",
        unsafe_allow_html=True,
    )


def _hunt_map(metrics: pd.DataFrame, df: pd.DataFrame, cfg: dict, preset_name: str) -> None:
    """Quadrant scatter: everything scored, current preset highlighted, hunt zone shaded."""
    pts = metrics[metrics["quality_score"].notna() & metrics["cheapness_score"].notna()].copy()
    member = set(df["ticker"]) if not df.empty else set()
    fig = go.Figure()
    for sub, color, name, size, op in [
        (pts[~pts["ticker"].isin(member)], "#8ea0b5", "the rest of the market", 8, 0.45),
        (pts[pts["ticker"].isin(member)], "#e4572e", preset_name, 12, 0.95),
    ]:
        if sub.empty:
            continue
        hover = sub["ticker"] if "name" not in sub.columns else sub["ticker"] + " — " + sub["name"]
        is_sel = name == preset_name
        fig.add_trace(go.Scatter(
            x=sub["cheapness_score"], y=sub["quality_score"],
            mode="markers+text" if is_sel and len(sub) <= 12 else "markers",
            text=sub["ticker"] if is_sel and len(sub) <= 12 else None,
            textposition="top center", textfont=dict(size=9),
            name=name, hovertext=hover, hoverinfo="text",
            marker=dict(size=size, color=color, opacity=op, line=dict(width=1, color="white")),
        ))
    thr = cfg.get("quality_floor_threshold")
    if thr is not None and pd.notna(thr):
        fig.add_hline(y=thr, line_dash="dot", line_color="#666",
                      annotation_text=f"quality floor — top 20% (≈{thr:.0f})")
        fig.add_shape(type="rect", x0=50, x1=105, y0=thr, y1=108,
                      fillcolor="rgba(228,87,46,0.07)", line_width=0)
        fig.add_annotation(x=77, y=107, text="the hunt zone", showarrow=False,
                           font=dict(color="#e4572e", size=11))
    fig.update_layout(
        height=560, xaxis_range=[0, 105],
        xaxis_title="cheaper → (CheapnessScore, 100 = cheapest)",
        yaxis_title="higher quality ↑ (QualityScore)",
        title=f"Every scored name — highlighted: {preset_name}",
        hovermode="closest",
    )
    st.plotly_chart(fig, width="stretch")
    st.caption(
        "Up and to the right is the thesis: great businesses at cheap prices. The dotted line is "
        "the top-20% quality cut; the shaded corner is where the hunt lives. Pick a preset on the "
        "left to highlight its members."
    )


# ------------------------------------------------------------ dashboard page --
def dashboard_page():
    scan_dir = _latest_scan()
    if scan_dir is None or not (scan_dir / "config.json").exists():
        st.error("No scans on disk yet. Open **Data manager** (menu, top right) and run a scan.")
        return
    cfg_path = scan_dir / "config.json"
    refreshed_at = dt.datetime.fromtimestamp(cfg_path.stat().st_mtime)
    metrics, cfg, hist, presets, overlay = load_scan(scan_dir, cfg_path.stat().st_mtime)

    frames = {**presets, FLOOR_KEY: _floor_passers(metrics)}
    labels = {name: f"{name} · {len(df)} names" for name, df in frames.items()}
    with st.sidebar:
        preset_label = st.selectbox("Preset", list(labels.values()))
        preset_name = next(n for n, l in labels.items() if l == preset_label)
        with st.expander("❔ How the scores work"):
            st.markdown(SCORE_DOC)

    st.title("stocks_scanner")
    sub = cfg.get("subset") or {}
    if sub.get("tickers"):
        sub_txt = f"subset of {len(sub['tickers'])} tickers"
    elif sub.get("limit"):
        sub_txt = f"subset: first {sub['limit']} names"
    else:
        sub_txt = "full standard group"
    st.caption(
        f"Scan as-of **{cfg.get('asof', scan_dir.name)}** · data as known at that date · "
        f"{sub_txt} · older scans in the Data manager page"
    )

    total, scored, floor, flags = _funnel_counts(metrics)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Last refresh", f"{refreshed_at:%Y-%m-%d %H:%M}")
    c2.metric("10Y Treasury — the yield bar", _fmt(cfg.get("gs10"), "{:.2%}"))
    c3.metric("Above the quality floor", floor, f"top 20% of {scored} scored")
    c4.metric("In the hunt right now", flags, f"of {floor} floor names")
    st.caption(
        "The Adobe/Oracle pattern: a great business the market has stopped paying up for. "
        "Quality filters, cheapness ranks, sentiment flags."
    )
    st.plotly_chart(_funnel_fig(metrics), width="stretch")

    table_cols = [
        "ticker", "name", "sector", "price", "quality_score", "cheapness_score", "residual",
        "residual_pct", "ev_ebit", "ev_fcf", "fcf_yield", "ev_fcf_self_pct", "ev_fcf_self_z",
        "brake_gm", "brake_fcf_margin", "brake_roic", "composite",
    ]
    fmt = {
        "price": "{:.2f}", "quality_score": "{:.1f}", "cheapness_score": "{:.1f}",
        "residual": "{:.3f}", "residual_pct": "{:.0f}", "ev_ebit": "{:.1f}", "ev_fcf": "{:.1f}",
        "fcf_yield": "{:.2%}", "ev_fcf_self_pct": "{:.0f}", "ev_fcf_self_z": "{:.2f}",
        "brake_gm": "{:.3f}", "brake_fcf_margin": "{:.3f}", "brake_roic": "{:.3f}",
        "composite": "{:.1f}",
    }

    tab_map, tab_cards, tab_detail, tab_overlay, tab_table = st.tabs(
        ["🎯 The hunt map", "🃏 Top names", "🔍 Company detail", "💰 Put overlay (info only)", "🧾 Full table"]
    )
    df = frames.get(preset_name, pd.DataFrame())

    with tab_map:
        _hunt_map(metrics, df, cfg, preset_name)

    with tab_cards:
        info = PRESET_INFO.get(preset_name, {})
        with st.expander("What this preset filters by", expanded=True):
            st.markdown("\n".join(f"- {f}" for f in info.get("filters", [])))
            st.markdown(f"**Ranked by:** {info.get('rank', '—')}")
            if info.get("note"):
                st.markdown(f"*{info['note']}*")
        if df.empty:
            st.info(
                f"No names in **{preset_name}** for this scan. The conditions above are all "
                f"AND-ed — widen your view with “{FLOOR_KEY}” and use Company detail to see "
                f"which condition a candidate name fails."
            )
        else:
            _name_cards(df, hist, preset_name)

    with tab_table:
        if df.empty:
            st.info(f"No names in **{preset_name}** for this scan — see Top names for why.")
        else:
            cols = [c for c in table_cols if c in df.columns]
            show = df[cols].copy()
            for c, f in fmt.items():
                if c in show.columns:
                    show[c] = show[c].map(lambda v, f=f: f.format(v) if pd.notna(v) else "—")
            col_cfg = {
                c: st.column_config.Column(
                    label="composite (display-only)" if c == "composite" else c,
                    help=COLUMN_DOCS.get(c),
                )
                for c in show.columns
            }
            st.dataframe(show, column_config=col_cfg, width="stretch", height=520)
            st.caption(
                "Flagship ranked by valuation residual, other presets by CheapnessScore. The "
                "wide floor-passers view is computed live from metrics.csv with the same rule "
                "the scan uses. Composite is display-only — nothing is ranked by it (doc 05 §2.3)."
            )

    with tab_detail:
        tickers = sorted(metrics["ticker"].unique())
        sel = st.selectbox("Company", tickers, index=tickers.index("ADBE") if "ADBE" in tickers else 0)
        row = metrics[metrics["ticker"] == sel].iloc[0]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Quality (0–100)", _fmt(row.get("quality_score"), "{:.0f}"),
                  "top 20% passes the gate")
        m2.metric("Cheapness (0–100)", _fmt(row.get("cheapness_score"), "{:.0f}"),
                  "100 = cheapest")
        m3.metric("EV/FCF vs own history", _fmt(row.get("ev_fcf_self_pct"), "{:.0f}"),
                  "0 = cheapest ever")
        m4.metric("FCF yield", _fmt(row.get("fcf_yield"), "{:.2%}"))
        if not hist.empty and "ticker" in hist.columns and (hist["ticker"] == sel).any():
            h = hist[hist["ticker"] == sel].sort_values("week")
            g1, g2 = st.columns(2)
            with g1:
                fig1 = go.Figure()
                fig1.add_trace(go.Scatter(x=h["week"], y=h["ev_fcf"], name="EV/FCF (adj)"))
                med = h["ev_fcf"].median()
                if pd.notna(med):
                    fig1.add_hline(y=med, line_dash="dot", annotation_text=f"5y median {med:.1f}x")
                    fig1.add_hline(y=0.8 * med, line_dash="dash", line_color="#2ca02c",
                                   annotation_text="0.8× median (flag arm)")
                cur = h["ev_fcf"].dropna()
                if not cur.empty:
                    fig1.add_trace(go.Scatter(
                        x=[h.loc[cur.index[-1], "week"]], y=[cur.iloc[-1]], mode="markers",
                        name="current", showlegend=False,
                        marker=dict(size=11, color="#e4572e", symbol="diamond",
                                    line=dict(width=2, color="white")),
                        hovertemplate=f"<b>{sel} now: {cur.iloc[-1]:.1f}x</b><extra></extra>",
                    ))
                else:
                    st.caption("No valid EV/FCF history points for this ticker.")
                fig1.update_layout(height=340, title="EV/FCF vs own history (SELF window)",
                                   yaxis_title="×")
                st.plotly_chart(fig1, width="stretch")
            with g2:
                fig2 = go.Figure()
                fig2.add_trace(go.Scatter(x=h["week"], y=h["fcf_adj_ttm"] / 1e9, name="FCF (adj, TTM) $B"))
                fig2.add_trace(go.Scatter(x=h["week"], y=h["ni_ttm"] / 1e9, name="Net income (TTM) $B"))
                fig2.update_layout(height=340, title="Cash vs earnings (as-known TTM)", yaxis_title="$B")
                st.plotly_chart(fig2, width="stretch")
        else:
            st.info(f"No weekly history for {sel} in this scan.")
        ev = row.get("evidence_derated")
        if isinstance(ev, str) and ev:
            with st.expander("Flag evidence (derated_quality)"):
                st.json(json.loads(ev))

    with tab_overlay:
        if overlay.empty:
            st.info("No overlay for this scan (flagship empty or run with --skip-options).")
        else:
            def _badge(v):
                if v is None or (not isinstance(v, (bool, str)) and pd.isna(v)):
                    return "—"
                return "✓" if (v is True or str(v).strip().lower() == "true") else "✗"

            ov_cols = [c for c in [
                "ticker", "price", "fcf_yield_strike_95", "fcf_yield_strike_90",
                "days_to_earnings", "badge_numbers_stale", "badge_event_window",
                "iv_atm", "hv_30d", "iv_vs_hv", "gate_oi", "gate_spread_pct", "gate_pass",
            ] if c in overlay.columns]
            show = overlay[ov_cols].copy()
            for c in ("fcf_yield_strike_95", "fcf_yield_strike_90", "iv_atm", "hv_30d",
                      "iv_vs_hv", "gate_spread_pct"):
                if c in show.columns:
                    show[c] = show[c].map(lambda v: f"{v:.2%}" if pd.notna(v) else "—")
            for c in ("badge_numbers_stale", "badge_event_window", "gate_pass"):
                if c in show.columns:
                    show[c] = show[c].map(_badge)
            st.dataframe(show, width="stretch")
            st.caption(
                "Assignment test: FCF yield if put the stock at −5%/−10%. The two DTE badges read "
                "the same number oppositely: numbers_stale = equity view (beware stale TTM), "
                "event_window = put view (premium window). IV/HV and the gate are display-only — "
                "the overlay never ranks (doc 07)."
            )

    with st.expander("Unscored / gate failures (coverage)"):
        cov = scan_dir / "coverage.csv"
        if cov.exists() and cov.read_text().strip():
            st.dataframe(pd.read_csv(cov), width="stretch")
        else:
            st.write("none")


# --------------------------------------------------------- data manager page --
def manager_page():
    import data_manager
    data_manager.render()


# ------------------------------------------------------------------ routing --
pg = st.navigation([
    st.Page(dashboard_page, title="Dashboard", icon="📊", default=True, url_path="dashboard"),
    st.Page(manager_page, title="Data manager", icon="🗂️", url_path="data"),
])
pg.run()
