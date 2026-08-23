"""Fetch real historical OHLCV data from Zerodha Kite Connect and save it as
a CSV in exactly the shape the backtester app expects (datetime, open, high,
low, close, volume) — just upload the resulting file in the app's sidebar.

Setup (one-time)
-----------------
1. You need a Zerodha demat account + a Kite Connect API subscription
   (https://developers.kite.trade -- a small one-time + recurring fee).
2. Create an app there. You'll get an `api_key` and `api_secret`. Set the
   app's "Redirect URL" to something simple like http://127.0.0.1 -- you
   won't run a server there, you'll just copy the token out of the URL bar.
3. NEVER hardcode api_key/api_secret in this file or commit them. Set them
   as environment variables before running:

     Windows (PowerShell):
       $env:KITE_API_KEY = "your_api_key"
       $env:KITE_API_SECRET = "your_api_secret"

     Windows (cmd):
       set KITE_API_KEY=your_api_key
       set KITE_API_SECRET=your_api_secret

Kite's access token is only valid until ~7:30am the next trading day, so
you'll do a one-time login each day you use this. The script walks you
through it and caches the token in .kite_session.json (gitignored) so you
only log in once per day even if you run the script multiple times.

Usage examples
--------------
Bank Nifty INDEX, 2 years of daily candles (long history, for signal logic):

    python fetch_kite_data.py --symbol "NIFTY BANK" --exchange NSE \\
        --from 2023-08-01 --to 2025-08-23 --interval day \\
        --out banknifty_index_daily.csv

Bank Nifty FUTURES, 5-minute candles for the last 60 days:

    python fetch_kite_data.py --symbol "BANKNIFTY25AUGFUT" --exchange NFO \\
        --from 2025-06-24 --to 2025-08-23 --interval 5minute \\
        --out banknifty_fut_5min.csv

A specific weekly OPTION contract, 5-minute candles for its ~1-week life
(get the exact tradingsymbol from Kite's instrument dump or your broker's
option chain, e.g. BANKNIFTY25AUG50000CE):

    python fetch_kite_data.py --symbol "BANKNIFTY25AUG50000CE" --exchange NFO \\
        --from 2025-08-18 --to 2025-08-21 --interval 5minute \\
        --out bn_50000ce_sample.csv

Run `python fetch_kite_data.py --help` for all options. Run with
`--list-instruments BANKNIFTY` to search for exact tradingsymbols instead of
guessing them.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from kiteconnect import KiteConnect

SESSION_CACHE = Path(__file__).parent / ".kite_session.json"
INSTRUMENTS_CACHE_DIR = Path(__file__).parent / ".kite_cache"

# Kite's documented historical-data lookback caps differ by interval; 60 days
# is the tightest (per-minute) one, so chunking everything at 60 days is a
# safe default that works for every interval without special-casing.
CHUNK_DAYS = 60


def _get_kite() -> KiteConnect:
    api_key = os.environ.get("KITE_API_KEY")
    api_secret = os.environ.get("KITE_API_SECRET")
    if not api_key or not api_secret:
        sys.exit(
            "Set KITE_API_KEY and KITE_API_SECRET as environment variables first "
            "(see the setup instructions at the top of this file)."
        )

    kite = KiteConnect(api_key=api_key)

    cached = _load_cached_session()
    if cached and cached.get("date") == str(date.today()):
        kite.set_access_token(cached["access_token"])
        try:
            kite.profile()  # cheap call to confirm the token still works
            return kite
        except Exception:
            pass  # fall through to a fresh login

    print("Opening the Kite login page in your browser...")
    login_url = kite.login_url()
    print(login_url)
    try:
        webbrowser.open(login_url)
    except Exception:
        pass
    print(
        "\nLog in, then Kite will redirect you to your app's Redirect URL with "
        "a `request_token=...` query parameter. Paste either the FULL redirected "
        "URL or just the token value below."
    )
    raw = input("Redirect URL or request_token: ").strip()
    request_token = raw.split("request_token=")[-1].split("&")[0] if "request_token=" in raw else raw

    session = kite.generate_session(request_token, api_secret=api_secret)
    kite.set_access_token(session["access_token"])
    _save_cached_session(session["access_token"])
    return kite


def _load_cached_session() -> dict | None:
    if SESSION_CACHE.exists():
        try:
            return json.loads(SESSION_CACHE.read_text())
        except Exception:
            return None
    return None


def _save_cached_session(access_token: str) -> None:
    SESSION_CACHE.write_text(json.dumps({"date": str(date.today()), "access_token": access_token}))


def _load_instruments(kite: KiteConnect, exchange: str) -> pd.DataFrame:
    INSTRUMENTS_CACHE_DIR.mkdir(exist_ok=True)
    cache_file = INSTRUMENTS_CACHE_DIR / f"{exchange}_{date.today()}.csv"
    if cache_file.exists():
        return pd.read_csv(cache_file)
    print(f"Downloading {exchange} instrument list from Kite (cached for today after this)...")
    df = pd.DataFrame(kite.instruments(exchange))
    df.to_csv(cache_file, index=False)
    # Clean up older days' cache files for this exchange so the folder doesn't grow forever.
    for old in INSTRUMENTS_CACHE_DIR.glob(f"{exchange}_*.csv"):
        if old != cache_file:
            old.unlink(missing_ok=True)
    return df


def resolve_instrument_token(kite: KiteConnect, exchange: str, tradingsymbol: str) -> int:
    instruments = _load_instruments(kite, exchange)
    match = instruments[instruments["tradingsymbol"].str.upper() == tradingsymbol.upper()]
    if match.empty:
        raise SystemExit(
            f"Couldn't find '{tradingsymbol}' on {exchange}. Try "
            f"--list-instruments \"{tradingsymbol[:9]}\" to search for the exact tradingsymbol."
        )
    return int(match.iloc[0]["instrument_token"])


def fetch_historical(kite: KiteConnect, token: int, from_dt: datetime, to_dt: datetime, interval: str) -> pd.DataFrame:
    chunks = []
    start = from_dt
    while start < to_dt:
        end = min(start + timedelta(days=CHUNK_DAYS), to_dt)
        print(f"  Fetching {start.date()} to {end.date()} ({interval})...")
        data = kite.historical_data(token, start, end, interval)
        if data:
            chunks.append(pd.DataFrame(data))
        start = end
        time.sleep(0.35)  # stay well under Kite's rate limit
    if not chunks:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    df = pd.concat(chunks, ignore_index=True)
    df = df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", help="Exact Kite tradingsymbol, e.g. 'NIFTY BANK', 'BANKNIFTY25AUGFUT', 'BANKNIFTY25AUG50000CE'")
    parser.add_argument("--exchange", default="NFO", choices=["NSE", "NFO"], help="NSE for the index, NFO for futures/options (default: NFO)")
    parser.add_argument("--from", dest="from_date", help="Start date, YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", help="End date, YYYY-MM-DD")
    parser.add_argument(
        "--interval", default="5minute",
        choices=["minute", "3minute", "5minute", "15minute", "30minute", "60minute", "day"],
        help="Candle size (default: 5minute)",
    )
    parser.add_argument("--out", help="Output CSV path")
    parser.add_argument("--list-instruments", metavar="SEARCH", help="Search NFO tradingsymbols containing SEARCH and exit (no data fetched)")
    args = parser.parse_args()

    kite = _get_kite()

    if args.list_instruments:
        instruments = _load_instruments(kite, "NFO")
        hits = instruments[instruments["tradingsymbol"].str.contains(args.list_instruments.upper(), na=False)]
        cols = ["tradingsymbol", "instrument_token", "expiry", "strike", "instrument_type"]
        print(hits[cols].to_string(index=False) if not hits.empty else "No matches.")
        return

    if not (args.symbol and args.from_date and args.to_date and args.out):
        parser.error("--symbol, --from, --to and --out are required (unless using --list-instruments)")

    token = resolve_instrument_token(kite, args.exchange, args.symbol)
    from_dt = datetime.strptime(args.from_date, "%Y-%m-%d")
    to_dt = datetime.strptime(args.to_date, "%Y-%m-%d")

    print(f"Fetching {args.symbol} ({args.exchange}, token {token}), {args.interval} candles...")
    df = fetch_historical(kite, token, from_dt, to_dt, args.interval)

    if df.empty:
        sys.exit("No data returned -- check the date range and that the contract was listed/trading in that window.")

    df.to_csv(args.out, index=False)
    print(f"Saved {len(df):,} candles to {args.out} -- upload this directly in the app's sidebar (Upload CSV).")


if __name__ == "__main__":
    main()
