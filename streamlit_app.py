"""Bank Nifty options-buying strategy backtester.

Paste a Python strategy (see the Help tab for the interface), pick a data
source, and run a bar-by-bar backtest with a TradingView-style results
report: equity curve, drawdown, a candlestick chart with entry/exit
markers, and a full trade list.
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_ace import st_ace

from backtest_engine import StrategyError, load_strategy, run_backtest
from data_utils import DataError, generate_synthetic_demo_data, load_csv, load_yfinance_banknifty
from sample_strategies import EXAMPLES

st.set_page_config(
    page_title="Bank Nifty backtester",
    page_icon=":material/show_chart:",
    layout="wide",
)

"""
# :material/show_chart: Bank Nifty strategy backtester

Paste your Python strategy, load some Bank Nifty candles, and get a full
backtest report — equity curve, drawdown, trade-by-trade P&L, and a
candlestick chart with your entries and exits marked.
"""

st.caption(
    "Backtest only — this does not place real orders. Results depend entirely "
    "on the data and assumptions you configure; they are not a guarantee of "
    "live performance. See the **Help & interface** tab for exactly how "
    "fills, stop-losses, and targets are simulated."
)

# --------------------------------------------------------------------------
# Session state defaults
# --------------------------------------------------------------------------
if "strategy_code" not in st.session_state:
    st.session_state.strategy_code = EXAMPLES["SMA crossover"]
if "editor_nonce" not in st.session_state:
    st.session_state.editor_nonce = 0
if "data_df" not in st.session_state:
    st.session_state.data_df = None
if "data_label" not in st.session_state:
    st.session_state.data_label = None
if "result" not in st.session_state:
    st.session_state.result = None
if "run_error" not in st.session_state:
    st.session_state.run_error = None

# --------------------------------------------------------------------------
# Sidebar — data source + backtest settings
# --------------------------------------------------------------------------
with st.sidebar:
    st.subheader("1. Data", divider="gray")

    source = st.radio(
        "Data source",
        ["Synthetic demo data", "Upload CSV", "Yahoo Finance (Bank Nifty index, daily)"],
        help=(
            "Options premium data has no free public API. Upload a CSV from "
            "your broker/vendor (Kite, TrueData, Sensibull, a TradingView "
            "chart export, ...) for a real options-buying backtest."
        ),
    )

    if source == "Upload CSV":
        st.caption(
            "Needs columns for date/time, open, high, low, close "
            "(volume optional) — most broker/vendor exports work as-is."
        )
        uploaded = st.file_uploader("OHLC(V) CSV", type=["csv"])
        if uploaded is not None:
            try:
                st.session_state.data_df = load_csv(uploaded)
                st.session_state.data_label = uploaded.name
            except DataError as exc:
                st.error(str(exc))

    elif source == "Yahoo Finance (Bank Nifty index, daily)":
        st.caption(
            "Free, but **daily index-level data only** — no options premium "
            "or intraday history. Good for prototyping signal logic, not for "
            "a realistic options-buying backtest."
        )
        period = st.selectbox("History length", ["1y", "2y", "5y", "10y"], index=1)
        if st.button("Fetch data", icon=":material/cloud_download:"):
            try:
                st.session_state.data_df = load_yfinance_banknifty(period=period, interval="1d")
                st.session_state.data_label = f"^NSEBANK daily ({period})"
            except DataError as exc:
                st.error(str(exc))

    else:  # Synthetic demo data
        st.caption(
            "Randomly generated premium-like candles — for trying out the "
            "app and the strategy interface with zero setup. Not real "
            "market data; don't draw trading conclusions from it."
        )
        n_days = st.slider("Number of trading days", 20, 250, 90)
        interval_minutes = st.selectbox("Candle interval (minutes)", [1, 3, 5, 15], index=2)
        start_price = st.number_input("Starting premium (₹)", 10.0, 2000.0, 250.0, step=10.0)
        n_candles = n_days * (375 // interval_minutes)
        if st.button("Generate demo data", icon=":material/casino:"):
            st.session_state.data_df = generate_synthetic_demo_data(
                n_candles=n_candles, start_price=start_price, interval_minutes=interval_minutes
            )
            st.session_state.data_label = f"Synthetic demo ({n_days} days, {interval_minutes}m)"

    if st.session_state.data_df is not None:
        df = st.session_state.data_df
        st.success(
            f"Loaded **{st.session_state.data_label}** — {len(df):,} candles, "
            f"{df['datetime'].min():%d %b %Y} to {df['datetime'].max():%d %b %Y}",
            icon=":material/check_circle:",
        )

    st.subheader("2. Backtest settings", divider="gray")

    capital = st.number_input("Starting capital (₹)", 10_000, 100_000_000, 200_000, step=10_000)
    lot_size = st.number_input(
        "Lot size (units per lot)",
        1,
        10_000,
        30,
        help=(
            "NSE revised the Bank Nifty lot size from 35 to 30 effective the "
            "January 2026 contract series — confirm the current figure on "
            "the NSE circular before relying on this for live sizing."
        ),
    )
    brokerage = st.number_input("Brokerage per order (₹)", 0.0, 1000.0, 20.0, step=5.0)
    slippage = st.number_input("Slippage (premium points per fill)", 0.0, 100.0, 0.5, step=0.5)
    eod_squareoff = st.checkbox(
        "Force-close open position at end of each day", value=True,
        help="Common for intraday options-buying strategies that don't carry positions overnight.",
    )

    st.divider()
    run_clicked = st.button(
        "Run backtest", type="primary", width="stretch", icon=":material/play_arrow:"
    )

# --------------------------------------------------------------------------
# Main area
# --------------------------------------------------------------------------
tab_code, tab_results, tab_help = st.tabs(["Strategy code", "Results", "Help & interface"])

with tab_code:
    top = st.container(horizontal=True, vertical_alignment="center")
    example_choice = top.selectbox("Load an example", list(EXAMPLES.keys()), label_visibility="collapsed")
    if top.button("Load example", icon=":material/download:"):
        st.session_state.strategy_code = EXAMPLES[example_choice]
        st.session_state.editor_nonce += 1
        st.rerun()

    code_value = st_ace(
        value=st.session_state.strategy_code,
        language="python",
        theme="tomorrow_night",
        keybinding="vscode",
        min_lines=28,
        font_size=14,
        tab_size=4,
        show_gutter=True,
        auto_update=True,
        key=f"ace_{st.session_state.editor_nonce}",
    )
    if code_value:
        st.session_state.strategy_code = code_value

    with st.expander("Quick interface reference"):
        st.markdown(
            "Your code must define `on_candle(ctx, candle, history)`, and may "
            "optionally define `initialize(ctx)`, run once before the first candle.\n\n"
            "**Trading calls on `ctx`:**\n"
            "- `ctx.buy(lots=1, sl_points=None, target_points=None)` — go long\n"
            "- `ctx.sell(lots=1, sl_points=None, target_points=None)` — go short "
            "(for option-selling strategies — margin isn't modelled)\n"
            "- `ctx.exit_position(reason=\"signal\")` — close the open position\n"
            "- `ctx.set_stop_loss(price=None, points=None)` / "
            "`ctx.set_target(price=None, points=None)`\n\n"
            "**Read-only state:** `ctx.position`, `ctx.avg_price`, `ctx.cash`, "
            "`ctx.equity`, `ctx.params` (dict), `ctx.state` (dict, yours to use), "
            "`ctx.log(msg)`.\n\n"
            "`candle` is the current OHLCV row; `history` is a DataFrame of all "
            "candles up to and including the current one. See the **Help & "
            "interface** tab for the full contract and execution assumptions."
        )

with tab_results:
    if run_clicked:
        if st.session_state.data_df is None:
            st.error("Load some data in the sidebar first.", icon=":material/error:")
        else:
            try:
                strategy_ns = load_strategy(st.session_state.strategy_code)
                result = run_backtest(
                    st.session_state.data_df,
                    strategy_ns,
                    capital=capital,
                    lot_size=int(lot_size),
                    brokerage_per_order=brokerage,
                    slippage_points=slippage,
                    eod_squareoff=eod_squareoff,
                )
                st.session_state.result = result
                st.session_state.run_error = None
            except StrategyError as exc:
                st.session_state.result = None
                st.session_state.run_error = str(exc)

    if st.session_state.run_error:
        st.error(st.session_state.run_error, icon=":material/error:")

    result = st.session_state.result
    if result is None:
        st.info(
            "Load data and paste a strategy, then click **Run backtest** in the sidebar.",
            icon=":material/info:",
        )
    else:
        m = result.metrics
        cols = st.columns(6)
        cols[0].metric("Net P&L", f"₹{m['net_pnl']:,.0f}", f"{m['net_pnl_pct']:.1f}%")
        cols[1].metric("Win rate", f"{m['win_rate']:.1f}%")
        pf_display = "∞" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
        cols[2].metric("Profit factor", pf_display)
        cols[3].metric("Max drawdown", f"₹{m['max_drawdown']:,.0f}", f"{m['max_drawdown_pct']:.1f}%")
        cols[4].metric("Total trades", f"{m['total_trades']}")
        cols[5].metric("Sharpe (ann.)", f"{m['sharpe']:.2f}")

        st.space("small")

        eq = result.equity_curve
        eq_chart = (
            alt.Chart(eq)
            .mark_line()
            .encode(alt.X("datetime:T", title=None), alt.Y("equity:Q", title="Equity (₹)", scale=alt.Scale(zero=False)))
            .properties(height=280, title="Equity curve")
        )
        st.altair_chart(eq_chart, width="stretch")

        eq = eq.copy()
        eq["drawdown"] = eq["equity"] - eq["equity"].cummax()
        dd_chart = (
            alt.Chart(eq)
            .mark_area(opacity=0.6)
            .encode(
                alt.X("datetime:T", title=None),
                alt.Y("drawdown:Q", title="Drawdown (₹)"),
                color=alt.value("#F87171"),
            )
            .properties(height=160, title="Drawdown")
        )
        st.altair_chart(dd_chart, width="stretch")

        st.space("small")
        st.markdown("##### Price chart with entries & exits")
        df = st.session_state.data_df
        fig = go.Figure()
        fig.add_trace(
            go.Candlestick(
                x=df["datetime"], open=df["open"], high=df["high"], low=df["low"], close=df["close"],
                name="Price", increasing_line_color="#34D399", decreasing_line_color="#F87171",
            )
        )
        trades = result.trades
        if not trades.empty:
            fig.add_trace(
                go.Scatter(
                    x=trades["entry_time"], y=trades["entry_price"], mode="markers", name="Entry",
                    marker=dict(symbol="triangle-up", size=11, color="#60A5FA", line=dict(width=1, color="white")),
                )
            )
            exits = trades.dropna(subset=["exit_time"])
            if not exits.empty:
                exit_colors = exits["pnl"].apply(lambda p: "#34D399" if p >= 0 else "#F87171")
                fig.add_trace(
                    go.Scatter(
                        x=exits["exit_time"], y=exits["exit_price"], mode="markers", name="Exit",
                        marker=dict(symbol="triangle-down", size=11, color=exit_colors, line=dict(width=1, color="white")),
                    )
                )
        fig.update_layout(
            height=480,
            xaxis_rangeslider_visible=False,
            template="plotly_dark",
            margin=dict(l=10, r=10, t=10, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
        )
        st.plotly_chart(fig, width="stretch")

        st.space("small")
        st.markdown("##### Trade list")
        if trades.empty:
            st.info("No trades were taken — the strategy never entered a position on this data.")
        else:
            st.dataframe(
                trades,
                width="stretch",
                hide_index=True,
                column_config={
                    "entry_time": st.column_config.DatetimeColumn("Entry time"),
                    "exit_time": st.column_config.DatetimeColumn("Exit time"),
                    "entry_price": st.column_config.NumberColumn("Entry price", format="₹%.2f"),
                    "exit_price": st.column_config.NumberColumn("Exit price", format="₹%.2f"),
                    "pnl": st.column_config.NumberColumn("P&L", format="₹%.2f"),
                    "pnl_pct": st.column_config.NumberColumn("P&L %", format="%.2f%%"),
                    "side": "Side",
                    "lots": "Lots",
                    "quantity": "Qty",
                    "sl": st.column_config.NumberColumn("Stop-loss", format="₹%.2f"),
                    "target": st.column_config.NumberColumn("Target", format="₹%.2f"),
                    "exit_reason": "Exit reason",
                },
            )

            dl_cols = st.columns(2)
            dl_cols[0].download_button(
                "Download trades CSV", trades.to_csv(index=False).encode(), "trades.csv", "text/csv",
                icon=":material/download:",
            )
            dl_cols[1].download_button(
                "Download equity curve CSV", result.equity_curve.to_csv(index=False).encode(),
                "equity_curve.csv", "text/csv", icon=":material/download:",
            )

        if result.logs:
            with st.expander(f"Run log ({len(result.logs)} lines)"):
                st.code("\n".join(result.logs), language=None)

with tab_help:
    st.markdown(
        """
### Strategy interface

Your pasted code must define:

```python
def on_candle(ctx, candle, history):
    ...
```

and may optionally define:

```python
def initialize(ctx):
    ...  # runs once, before the first candle — good place for ctx.params defaults
```

`candle` is the current OHLCV row (`candle["close"]`, `candle["datetime"]`, ...).
`history` is a `pandas.DataFrame` of every candle up to and including the
current one — use `history["close"].rolling(20).mean()` etc. for indicators.
"""
    )
    st.markdown(
        """
### Trading API (`ctx`)

- `ctx.buy(lots=1, sl=None, target=None, sl_points=None, target_points=None)`
  — open (or add to) a long position, sized in lots of `ctx.lot_size` units.
- `ctx.sell(lots=1, sl=None, target=None, sl_points=None, target_points=None)`
  — open a short position. Only meaningful for option-selling/writing
  strategies — margin is **not** modelled, so treat P&L here as indicative
  only.
- `ctx.exit_position(reason="signal")` — close the entire open position at
  this candle's close.
- `ctx.set_stop_loss(price=None, points=None)` / `ctx.set_target(price=None, points=None)`
  — update the SL/target on the open position. `points` is measured from
  the entry price.

### State & info (`ctx`)

- `ctx.position` — signed open quantity (+units long, -units short, 0 flat).
- `ctx.avg_price` — entry price of the current open position (0 if flat).
- `ctx.cash` — cash balance after all fills so far.
- `ctx.equity` — cash + mark-to-market value of the open position.
- `ctx.params` — dict of parameters you can read (set via `initialize(ctx)`).
- `ctx.state` — a plain dict that persists across candles, for your own
  indicators/lookback state instead of globals.
- `ctx.log(msg)` — add a line to the run log shown in the Results tab.
"""
    )
    st.markdown(
        """
### Execution assumptions (read this before trusting the numbers)

- A stop-loss/target on an already-open position is checked against the
  **current candle's high/low** before `on_candle()` runs for that candle
  (never on the same candle the position was opened, to avoid look-ahead).
- If a candle's high-low range touches **both** the stop-loss and the
  target, the **stop-loss is assumed to trigger first** — the conservative,
  worst-case assumption.
- Any `ctx.buy()` / `ctx.sell()` / `ctx.exit_position()` you call inside
  `on_candle()` fills at **that same candle's close price**.
- A position still open on the last candle is force-closed at the last
  close (`exit_reason="backtest_end"`).
- Slippage (points) and a flat brokerage-per-order are the only
  transaction-cost modelling — no bid/ask spread, liquidity, or impact
  cost, and **option margin is not modelled**, so this engine is built for
  **buying** premium, not for sizing a writing/selling strategy.
- This is a backtest against historical data you provide. It cannot
  account for slippage under real market stress, broker outages, or
  liquidity gaps — treat results as a strategy-validation signal, not a
  guarantee of live performance.

### Getting real Bank Nifty options data

There's no free, legal, programmatic source of historical option-premium
data. Practical options, roughly cheapest to most complete:

- **Zerodha Kite Connect** — historical data is included with the base
  Kite Connect API subscription if you already have a Zerodha account;
  intraday history is capped at 60 days per request.
- **TradingView** — manual "Export chart data" from a chart (limited bars,
  no bulk/automated export — respect their terms of service).
- **Paid vendors** (TrueData, Global Datafeeds, Sensibull) — full option
  chain history, priced per plan.

Export/download a CSV with datetime + OHLC(V) columns from any of these and
upload it in the sidebar.
"""
    )
