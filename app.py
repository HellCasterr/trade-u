from __future__ import annotations

import json
import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from tradeu.analysis import analyze_market
from tradeu.charts import market_chart
from tradeu.models import AnalysisConfig, InstrumentSpec
from tradeu.option_pricing import option_scenarios
from tradeu.providers import DemoProvider, DhanProvider, ProviderError, UpstoxProvider

load_dotenv()
st.set_page_config(page_title="Trade-U", page_icon="📈", layout="wide")

st.markdown(
    """
    <style>
      .block-container {padding-top: 1.4rem; max-width: 1500px;}
      [data-testid="stMetric"] {background:#111827; border:1px solid #253047; padding:14px; border-radius:14px;}
      .hero {padding:22px 26px; border:1px solid #28354f; border-radius:18px;
             background:linear-gradient(120deg,#111827 0%,#172554 60%,#0f172a 100%); margin-bottom:18px;}
      .hero h1 {margin:0; font-size:2.1rem;}
      .hero p {margin:6px 0 0; color:#bdc9dd;}
      .verdict {padding:16px 20px; border-radius:14px; font-weight:700; font-size:1.22rem; margin:6px 0 16px;}
      .go {background:#063d2c; border:1px solid #10b981; color:#a7f3d0;}
      .wait {background:#451a2d; border:1px solid #fb7185; color:#fecdd3;}
      .small-note {color:#94a3b8; font-size:.9rem;}
    </style>
    <div class="hero">
      <h1>Trade-U</h1>
      <p>Explainable Indian-market chart analysis · live Upstox/Dhan data · risk-first trade filtering</p>
    </div>
    """,
    unsafe_allow_html=True,
)


def _load_csv(uploaded) -> pd.DataFrame:
    frame = pd.read_csv(uploaded)
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    aliases = {
        "date": "timestamp",
        "datetime": "timestamp",
        "time": "timestamp",
        "vol": "volume",
        "open_interest": "oi",
    }
    frame = frame.rename(columns={column: aliases.get(column, column) for column in frame.columns})
    return frame


def _format(value: float | None, suffix: str = "") -> str:
    return "—" if value is None else f"{value:,.2f}{suffix}"


with st.sidebar:
    st.header("Market input")
    source = st.selectbox("Data source", ["Demo", "CSV upload", "Upstox", "Dhan"])
    kind = st.selectbox("Instrument type", ["CASH", "FUTURE", "OPTION"])
    symbol = st.text_input("Display symbol", "NIFTY 50")
    interval = st.selectbox("Candle interval", ["15minute", "5minute", "1minute", "60minute", "day"])
    today = date.today()
    default_days = 20 if interval != "day" else 550
    from_date = st.date_input("From", today - timedelta(days=default_days))
    to_date = st.date_input("To", today)
    lot_size = st.number_input("Lot size", min_value=1, value=1, step=1)
    tick_size = st.number_input("Tick size", min_value=0.01, value=0.05, step=0.01)

    instrument_id = symbol
    provider_args: dict = {}
    uploaded = None
    use_live_quote = False
    access_token = ""
    client_id = ""

    if source == "CSV upload":
        uploaded = st.file_uploader("OHLCV CSV", type=["csv"])
        st.caption("Columns: timestamp, open, high, low, close, volume; oi is optional.")
    elif source == "Upstox":
        access_token = st.text_input("Upstox access token", os.getenv("UPSTOX_ACCESS_TOKEN", ""), type="password")
        instrument_id = st.text_input("Upstox instrument key", "NSE_INDEX|Nifty 50")
        use_live_quote = st.checkbox("Use current Upstox quote", True)
        st.caption("Example keys: NSE_EQ|INE002A01018 or NSE_INDEX|Nifty 50")
    elif source == "Dhan":
        client_id = st.text_input("Dhan client ID", os.getenv("DHAN_CLIENT_ID", ""))
        access_token = st.text_input("Dhan access token", os.getenv("DHAN_ACCESS_TOKEN", ""), type="password")
        instrument_id = st.text_input("Dhan security ID", "13")
        segment = st.selectbox("Dhan exchange segment", ["IDX_I", "NSE_EQ", "NSE_FNO", "BSE_EQ", "BSE_FNO"])
        default_instrument = (
            "INDEX"
            if segment == "IDX_I"
            else "EQUITY"
            if segment.endswith("EQ")
            else ("FUTIDX" if kind == "FUTURE" else "OPTIDX")
        )
        dhan_instrument = st.text_input("Dhan instrument code", default_instrument)
        provider_args = {"exchange_segment": segment, "instrument_type": dhan_instrument, "oi": kind != "CASH"}
        use_live_quote = st.checkbox("Use current Dhan quote", True)

    expiry = None
    strike = None
    option_type = None
    if kind in {"FUTURE", "OPTION"}:
        expiry = st.date_input("Expiry", today + timedelta(days=7))
    if kind == "OPTION":
        strike = st.number_input("Strike", min_value=0.0, value=22500.0, step=50.0)
        option_type = st.selectbox("Option type", ["CE", "PE"])

    st.header("Risk controls")
    risk_budget = st.number_input("Maximum risk per trade (₹)", min_value=100.0, value=5000.0, step=500.0)
    minimum_rr = st.number_input("Minimum reward:risk", min_value=0.5, value=1.30, step=0.1)
    max_spread = st.number_input("Maximum spread (%)", min_value=0.01, value=0.50, step=0.05)
    cost_pct = st.number_input("Estimated round-trip costs (%)", min_value=0.0, value=0.08, step=0.01)
    with st.expander("Research controls"):
        analogue_count = st.slider("Historical analogues", 20, 80, 30)
        minimum_analogues = st.slider("Minimum analogue sample", 10, 50, 20)
        forward_horizon = st.slider("Forward horizon (bars)", 5, 50, 15)
        entry_zone_atr = st.slider("Maximum entry distance (ATR)", 0.4, 2.5, 1.2, 0.1)
    analyze_clicked = st.button("Analyze setup", type="primary", width="stretch")


config = AnalysisConfig(
    risk_budget_inr=float(risk_budget),
    minimum_reward_risk=float(minimum_rr),
    maximum_spread_pct=float(max_spread),
    estimated_cost_pct=float(cost_pct),
    analogue_count=int(analogue_count),
    minimum_analogues=int(minimum_analogues),
    forward_horizon=int(forward_horizon),
    entry_zone_atr=float(entry_zone_atr),
)
instrument = InstrumentSpec(
    symbol=symbol,
    kind=kind,
    lot_size=int(lot_size),
    tick_size=float(tick_size),
    expiry=expiry,
    strike=float(strike) if strike is not None else None,
    option_type=option_type,
)


if analyze_clicked:
    quote_warning = None
    try:
        if source == "Demo":
            provider = DemoProvider()
            candles = provider.historical(instrument_id, from_date, to_date, interval)
            quote = provider.quote(instrument_id, last_price=float(candles["close"].iloc[-1]))
        elif source == "CSV upload":
            if uploaded is None:
                raise ValueError("Choose a CSV file first.")
            provider = None
            candles = _load_csv(uploaded)
            quote = None
        elif source == "Upstox":
            provider = UpstoxProvider(access_token)
            candles = provider.historical(instrument_id, from_date, to_date, interval)
            try:
                quote = provider.quote(instrument_id) if use_live_quote else None
            except ProviderError as exc:
                quote = None
                quote_warning = str(exc)
        else:
            provider = DhanProvider(client_id, access_token)
            candles = provider.historical(instrument_id, from_date, to_date, interval, **provider_args)
            try:
                quote = provider.quote(instrument_id, **provider_args) if use_live_quote else None
            except ProviderError as exc:
                quote = None
                quote_warning = str(exc)
        analysis, enriched = analyze_market(candles, instrument, quote, config)
        st.session_state["result"] = (analysis, enriched, quote, source, instrument_id, provider_args)
        st.session_state["provider_credentials"] = {"access_token": access_token, "client_id": client_id}
        st.session_state["quote_warning"] = quote_warning
    except (ProviderError, ValueError, KeyError, TypeError) as exc:
        st.error(str(exc))


if "result" not in st.session_state:
    st.info(
        "Select a data source and press **Analyze setup**. Demo mode works immediately and requires no credentials."
    )
    st.markdown(
        """
        This application treats **NO TRADE** as a valid result. A setup is blocked when structure, liquidity,
        risk/reward, expiry risk, position risk, or historical-pattern confidence does not pass the configured gates.
        It is decision support—not a promise of returns and not an order-execution system.
        """
    )
    st.stop()


analysis, data, live_quote, result_source, result_instrument_id, result_provider_args = st.session_state["result"]
verdict_class = "go" if analysis.verdict != "NO TRADE" else "wait"
candidate = analysis.metadata.get("candidate_direction")
candidate_note = f" · candidate {candidate}" if analysis.verdict == "NO TRADE" and candidate != "NO TRADE" else ""
st.markdown(
    f'<div class="verdict {verdict_class}">{analysis.verdict}{candidate_note} · {analysis.trend.title()} structure</div>',
    unsafe_allow_html=True,
)
if st.session_state.get("quote_warning"):
    st.warning("History loaded, but live quote failed: " + st.session_state["quote_warning"])

top = st.columns(6)
top[0].metric("Entry", _format(analysis.entry))
top[1].metric("Stop", _format(analysis.stop), _format(analysis.stop_loss_pct, "% loss"))
top[2].metric("Target", _format(analysis.target), _format(analysis.target_pct, "% profit"))
top[3].metric("Net target", _format(analysis.net_target_pct, "%"))
top[4].metric("Net stop loss", _format(analysis.net_stop_loss_pct, "%"))
top[5].metric("Reward : risk", _format(analysis.reward_risk, "R"))

st.plotly_chart(market_chart(data, analysis, instrument.symbol), width="stretch")

summary_tab, checks_tab, live_tab, option_tab, data_tab = st.tabs(
    ["Decision summary", "No-trade checks", "Live feed", "Option lens", "Data audit"]
)

with summary_tab:
    left, right = st.columns([1.15, 1])
    with left:
        st.subheader("Why this result")
        for reason in analysis.reasons:
            st.write("✓", reason)
        for warning in analysis.warnings:
            st.write("⚠", warning)
    with right:
        st.subheader("Historical analogue evidence")
        stats = analysis.analogues
        m1, m2 = st.columns(2)
        m1.metric(
            "Target-first estimate", _format((stats.win_probability or 0) * 100, "%") if stats.sample_size else "—"
        )
        interval_text = (
            "—" if stats.wilson_low is None else f"{stats.wilson_low * 100:.1f}% – {stats.wilson_high * 100:.1f}%"
        )
        m2.metric("95% Wilson interval", interval_text)
        st.caption(
            f"n={stats.sample_size}; target first={stats.target_first}; stop first={stats.stop_first}; "
            f"timed out={stats.timeout}. Similarity uses normalized return shapes and excludes the current window."
        )
        if analysis.risk_per_lot_inr is not None:
            st.write(f"**Risk per lot:** ₹{analysis.risk_per_lot_inr:,.2f}")
            st.write(f"**Reward per lot:** ₹{analysis.reward_per_lot_inr:,.2f}")
            st.write(f"**Risk-budget position cap:** {analysis.position_lots} lot(s)")
        patterns = analysis.metadata.get("candle_patterns") or []
        st.write("**Latest candle labels:** " + (", ".join(patterns) if patterns else "No named pattern detected"))

with checks_tab:
    gate_frame = pd.DataFrame(
        [{"Check": name, "Result": "PASS" if passed else "BLOCK"} for name, passed in analysis.gates.items()]
    )
    st.dataframe(
        gate_frame.style.map(lambda value: "color:#34d399" if value == "PASS" else "color:#fb7185", subset=["Result"]),
        width="stretch",
        hide_index=True,
    )
    st.caption(
        "Every gate must pass before Trade-U displays a LONG or SHORT verdict. Adjusting a threshold changes policy, not market evidence."
    )

with live_tab:
    if result_source not in {"Upstox", "Dhan", "Demo"}:
        st.info("WebSocket streaming is available for Upstox and Dhan. CSV mode is intentionally static.")
    else:
        st.write(
            "Start a read-only stream for the analyzed instrument. The latest broker payload is shown as received."
        )
        start_col, stop_col, refresh_col = st.columns(3)
        if start_col.button("Start live stream", width="stretch"):
            try:
                credentials = st.session_state.get("provider_credentials", {})
                if result_source == "Upstox":
                    active_provider = UpstoxProvider(credentials.get("access_token", ""))
                    handle = active_provider.stream(result_instrument_id, mode="full")
                elif result_source == "Dhan":
                    active_provider = DhanProvider(
                        credentials.get("client_id", ""), credentials.get("access_token", "")
                    )
                    handle = active_provider.stream(
                        result_instrument_id,
                        exchange_segment=result_provider_args.get("exchange_segment", "NSE_EQ"),
                        mode="quote",
                    )
                else:
                    handle = DemoProvider().stream(result_instrument_id)
                handle.start()
                st.session_state["live_stream"] = handle
            except ProviderError as exc:
                st.error(str(exc))
        if stop_col.button("Stop live stream", width="stretch"):
            handle = st.session_state.get("live_stream")
            if handle:
                handle.stop()
                st.session_state.pop("live_stream", None)
        refresh_col.button("Refresh latest payload", width="stretch")
        handle = st.session_state.get("live_stream")
        if handle:
            st.write(f"Status: **{handle.status}**")
            latest = handle.latest()
            st.code(json.dumps(latest, indent=2, default=str) if latest else "Waiting for first tick…", language="json")
        else:
            st.caption("Stream stopped. Broker market-data subscriptions and exchange hours still apply.")

with option_tab:
    if instrument.kind != "OPTION":
        st.info("Choose OPTION as the instrument type to use the volatility and time-decay scenario lens.")
    else:
        st.warning(
            "This is a Black–Scholes scenario, not a premium forecast. Indian index options are European-style; IV and slippage can dominate short-horizon outcomes."
        )
        col1, col2, col3 = st.columns(3)
        underlying_spot = col1.number_input("Current underlying spot", min_value=0.01, value=22500.0)
        target_spot = col2.number_input("Underlying target", min_value=0.01, value=22700.0)
        stop_spot = col3.number_input("Underlying stop", min_value=0.01, value=22350.0)
        iv = st.slider("Implied volatility (%)", 5.0, 100.0, 18.0, 0.5)
        holding_days = st.slider("Expected holding period (days)", 0.1, 10.0, 1.0, 0.1)
        if instrument.expiry and instrument.strike and instrument.option_type:
            scenarios = option_scenarios(
                analysis.entry,
                underlying_spot,
                instrument.strike,
                instrument.expiry,
                instrument.option_type,
                target_spot,
                stop_spot,
                iv,
                holding_days,
            )
            st.dataframe(scenarios.style.format(precision=2), width="stretch", hide_index=True)

with data_tab:
    st.write(f"**Rows used:** {len(data):,}")
    if live_quote:
        q1, q2, q3, q4 = st.columns(4)
        q1.metric("Live LTP", _format(live_quote.ltp))
        q2.metric("Best bid", _format(live_quote.bid))
        q3.metric("Best ask", _format(live_quote.ask))
        q4.metric("Spread", _format(live_quote.spread_pct, "%"))
    st.dataframe(data.tail(100), width="stretch", hide_index=True)
    st.download_button(
        "Download enriched candles",
        data.to_csv(index=False).encode("utf-8"),
        file_name=f"{instrument.symbol.replace(' ', '_')}_tradeu_analysis.csv",
        mime="text/csv",
    )

st.divider()
st.caption(
    "Research/education only. No orders are placed. Verify instrument identity, contract lot size, charges, liquidity, corporate actions, and broker data before acting."
)
