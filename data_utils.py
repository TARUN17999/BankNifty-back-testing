"""Loading and normalising OHLCV data for the backtester.

The engine only needs one shape: a DataFrame sorted ascending by time with
columns `datetime, open, high, low, close, volume`. Everything in this file
exists to get arbitrary CSVs (from a broker, TradingView's manual export,
Sensibull, TrueData, Kite, ...) or a quick Yahoo Finance pull into that shape.
"""

from __future__ import annotations

import io

import numpy as np
import pandas as pd
import streamlit as st

REQUIRED_COLUMNS = ["datetime", "open", "high", "low", "close", "volume"]

# Common column-name spellings seen across brokers/vendors/TradingView exports.
_COLUMN_ALIASES = {
    "datetime": {"datetime", "date", "time", "timestamp", "date/time"},
    "open": {"open", "o"},
    "high": {"high", "h"},
    "low": {"low", "l"},
    "close": {"close", "c", "close price", "ltp"},
    "volume": {"volume", "vol", "v"},
}


class DataError(Exception):
    """Raised when uploaded/loaded data can't be coerced into OHLCV shape."""


def _match_columns(columns: list[str]) -> dict[str, str]:
    lower_map = {c.lower().strip(): c for c in columns}
    resolved: dict[str, str] = {}
    for target, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lower_map:
                resolved[target] = lower_map[alias]
                break
    return resolved


def normalize_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce an arbitrary OHLC(V) DataFrame into the engine's standard shape."""
    resolved = _match_columns(list(df.columns))
    missing = [c for c in ["datetime", "open", "high", "low", "close"] if c not in resolved]
    if missing:
        raise DataError(
            "Could not find columns for: " + ", ".join(missing) + ". "
            f"Columns found in file: {', '.join(df.columns.astype(str))}"
        )

    out = pd.DataFrame()
    out["datetime"] = pd.to_datetime(df[resolved["datetime"]], errors="coerce")
    for col in ["open", "high", "low", "close"]:
        out[col] = pd.to_numeric(df[resolved[col]], errors="coerce")
    if "volume" in resolved:
        out["volume"] = pd.to_numeric(df[resolved["volume"]], errors="coerce").fillna(0)
    else:
        out["volume"] = 0

    n_before = len(out)
    out = out.dropna(subset=["datetime", "open", "high", "low", "close"])
    dropped = n_before - len(out)

    out = out.sort_values("datetime").drop_duplicates(subset="datetime", keep="last")
    out = out.reset_index(drop=True)

    if out.empty:
        raise DataError("No valid OHLC rows left after parsing — check the file's date/number formats.")

    # Sanity-fix rows where high/low are inconsistent with open/close (bad exports happen).
    out["high"] = out[["high", "open", "close"]].max(axis=1)
    out["low"] = out[["low", "open", "close"]].min(axis=1)

    if dropped:
        st.warning(f"Skipped {dropped} row(s) with unparseable dates/prices.")

    return out


def load_csv(uploaded_file) -> pd.DataFrame:
    raw = uploaded_file.read()
    try:
        df = pd.read_csv(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001
        raise DataError(f"Could not read the CSV file: {exc}") from exc
    return normalize_ohlc(df)


@st.cache_data(ttl="6h", show_spinner="Fetching Bank Nifty index data from Yahoo Finance...")
def load_yfinance_banknifty(period: str = "2y", interval: str = "1d") -> pd.DataFrame:
    """Free daily (or coarse intraday, limited history) Bank Nifty INDEX data.

    Yahoo Finance has no Bank Nifty options/futures premium data — this is
    only useful for prototyping index-level strategy logic, not for a real
    options-buying backtest. Use CSV upload for real option premium series.
    """
    import yfinance as yf

    ticker = yf.Ticker("^NSEBANK")
    hist = ticker.history(period=period, interval=interval)
    if hist is None or hist.empty:
        raise DataError("Yahoo Finance returned no data for ^NSEBANK.")
    hist = hist.reset_index()
    hist.columns = [str(c) for c in hist.columns]
    return normalize_ohlc(hist)


def generate_synthetic_demo_data(
    n_candles: int = 3000,
    start_price: float = 250.0,
    interval_minutes: int = 5,
    seed: int = 42,
) -> pd.DataFrame:
    """Random-walk option-premium-like OHLCV series, purely so the app is
    usable instantly with no upload and no internet — for UI exploration
    and interface-testing only. Not real market data.
    """
    rng = np.random.default_rng(seed)
    minutes_per_day = 375  # NSE cash/derivatives session length
    candles_per_day = max(1, minutes_per_day // interval_minutes)

    start = pd.Timestamp("2025-01-01 09:15:00")
    timestamps = []
    t = start
    added = 0
    while added < n_candles:
        day_candles = 0
        while day_candles < candles_per_day and added < n_candles:
            timestamps.append(t)
            t += pd.Timedelta(minutes=interval_minutes)
            day_candles += 1
            added += 1
        # jump to next trading day at 09:15, skipping weekends
        t = (t.normalize() + pd.Timedelta(days=1)) + pd.Timedelta(hours=9, minutes=15)
        while t.weekday() >= 5:
            t += pd.Timedelta(days=1)

    # Option premiums decay and are noisy/mean-reverting-ish; approximate with
    # a mildly mean-reverting random walk plus intraday volatility clusters.
    n = len(timestamps)
    shocks = rng.normal(0, 1, n)
    vol = np.abs(rng.normal(1, 0.3, n))
    price = np.empty(n)
    price[0] = start_price
    theta = 0.01  # mean reversion strength
    mean_level = start_price
    for i in range(1, n):
        drift = theta * (mean_level - price[i - 1])
        price[i] = max(0.5, price[i - 1] + drift + shocks[i] * vol[i] * 1.2)

    opens = price
    closes = np.roll(price, -1)
    closes[-1] = price[-1]
    highs = np.maximum(opens, closes) + np.abs(rng.normal(0, 0.6, n))
    lows = np.maximum(0.05, np.minimum(opens, closes) - np.abs(rng.normal(0, 0.6, n)))
    volumes = rng.integers(500, 5000, n)

    df = pd.DataFrame(
        {
            "datetime": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )
    return normalize_ohlc(df)
