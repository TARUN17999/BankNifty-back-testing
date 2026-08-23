# Bank Nifty strategy backtester

A local Streamlit app: paste a Python strategy, load Bank Nifty candles, and
get a backtest report — equity curve, drawdown, a candlestick chart with
entry/exit markers, and a trade list.

This is **step 1** of building toward a fully automated Bank Nifty
options-buying algo (entry, exit, and stop-loss all hands-off). This app is
the backtesting/validation stage only — it does not place real orders.

## Running it

```bash
cd BankNifty-Backtester
.venv\Scripts\python -m streamlit run streamlit_app.py
```

Then open the URL it prints (usually `http://localhost:8501`).

If you ever need to reinstall dependencies:

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

## Getting real data

There is no free, legal, programmatic source of historical Bank Nifty
option-premium data. In the app's sidebar you can:

- **Upload a CSV** — export OHLC(V) data from your broker (Zerodha Kite
  Connect's historical API is included with the base subscription if you
  already have an account), a paid vendor (TrueData, Sensibull, Global
  Datafeeds), or TradingView's manual chart export. Any CSV with
  date/time + open/high/low/close (volume optional) columns works — common
  header spellings are auto-detected.
- **Yahoo Finance (free)** — daily Bank Nifty **index** data only, no
  options/futures/intraday. Fine for prototyping signal logic, not for a
  realistic options-buying backtest.
- **Synthetic demo data** — instant, no setup, purely for trying out the
  app and the strategy interface. Not real market data.

## Writing a strategy

See the **Help & interface** tab in the app for the full contract. Short
version — your code must define:

```python
def on_candle(ctx, candle, history):
    if ctx.position == 0 and <your entry condition>:
        ctx.buy(lots=1, sl_points=15, target_points=30)
    elif ctx.position > 0 and <your exit condition>:
        ctx.exit_position(reason="signal")
```

and may optionally define `initialize(ctx)` to set default `ctx.params`.
Two ready-made examples (SMA crossover, RSI mean-reversion) are loadable
from the "Strategy code" tab.

## Known limitations (read before trusting the numbers)

- Fills, stop-loss/target checks, and end-of-day square-off follow simple,
  documented assumptions (see the Help tab) — not a live-market simulation.
- No bid/ask spread, liquidity, or market-impact modelling beyond the
  slippage-points and brokerage-per-order you configure.
- Option margin is not modelled — the engine is built for **buying**
  premium (`ctx.buy()`), not for sizing a selling/writing strategy.
- Bank Nifty's lot size was revised from 35 to 30 starting the January 2026
  contract series — the app defaults to 30 but always confirm the current
  NSE circular before using this for real position sizing.

## Next step (not built yet)

Once a strategy backtests the way you want, the next phase is wiring the
same `on_candle` logic to a broker API (e.g. Kite Connect) for live,
automatic order placement, exits, and stop-losses — that's a separate,
higher-stakes build with its own review needed before it touches real
money.
