"""Ready-made example strategies shown in the app's "Load example" picker.

These are deliberately simple — they exist to demonstrate the ctx.buy() /
ctx.sell() / ctx.exit_position() / ctx.set_stop_loss() / ctx.set_target()
interface, not to be profitable trading strategies. Replace them with your
own logic.
"""

SMA_CROSSOVER = '''\
"""SMA crossover (long-only) — buy when the fast SMA crosses above the slow
SMA, exit on the opposite cross (or when SL/target is hit first).
Works on whatever price series you loaded (option premium, futures, index).
"""

def initialize(ctx):
    ctx.params.setdefault("fast_period", 9)
    ctx.params.setdefault("slow_period", 21)
    ctx.params.setdefault("sl_points", 15)
    ctx.params.setdefault("target_points", 30)


def on_candle(ctx, candle, history):
    fast_period = ctx.params["fast_period"]
    slow_period = ctx.params["slow_period"]

    if len(history) < slow_period + 1:
        return  # not enough candles yet to compute the slow SMA

    closes = history["close"]
    fast_sma = closes.rolling(fast_period).mean()
    slow_sma = closes.rolling(slow_period).mean()

    crossed_up = fast_sma.iloc[-2] <= slow_sma.iloc[-2] and fast_sma.iloc[-1] > slow_sma.iloc[-1]
    crossed_down = fast_sma.iloc[-2] >= slow_sma.iloc[-2] and fast_sma.iloc[-1] < slow_sma.iloc[-1]

    if ctx.position == 0 and crossed_up:
        ctx.buy(
            lots=1,
            sl_points=ctx.params["sl_points"],
            target_points=ctx.params["target_points"],
        )
        ctx.log(f"BUY signal: fast SMA {fast_sma.iloc[-1]:.2f} crossed above slow SMA {slow_sma.iloc[-1]:.2f}")
    elif ctx.position > 0 and crossed_down:
        ctx.exit_position(reason="sma_cross_down")
        ctx.log("EXIT signal: fast SMA crossed back below slow SMA")
'''

RSI_MEAN_REVERSION = '''\
"""RSI mean-reversion (long-only) — buy on oversold RSI, exit when RSI
recovers past the exit threshold or SL/target is hit first.
"""

def initialize(ctx):
    ctx.params.setdefault("rsi_period", 14)
    ctx.params.setdefault("buy_below", 30)
    ctx.params.setdefault("exit_above", 55)
    ctx.params.setdefault("sl_points", 12)
    ctx.params.setdefault("target_points", 25)


def _rsi(closes, period):
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def on_candle(ctx, candle, history):
    period = ctx.params["rsi_period"]
    if len(history) < period + 2:
        return

    rsi = _rsi(history["close"], period)
    current_rsi = rsi.iloc[-1]
    if current_rsi != current_rsi:  # NaN check without importing math/numpy
        return

    if ctx.position == 0 and current_rsi < ctx.params["buy_below"]:
        ctx.buy(
            lots=1,
            sl_points=ctx.params["sl_points"],
            target_points=ctx.params["target_points"],
        )
        ctx.log(f"BUY signal: RSI {current_rsi:.1f} below {ctx.params['buy_below']}")
    elif ctx.position > 0 and current_rsi > ctx.params["exit_above"]:
        ctx.exit_position(reason="rsi_recovered")
        ctx.log(f"EXIT signal: RSI {current_rsi:.1f} above {ctx.params['exit_above']}")
'''

SMA200_RSI_REGIME = '''\
"""200-SMA regime switch with RSI(4) mean-reversion (long-only).

- Above the 200-period SMA: trend regime — go long and STAY long no matter
  what RSI does, until price falls back below the SMA.
- Below the 200-period SMA: mean-reversion regime — go long only when
  RSI(4) drops under 20, and exit only when RSI(4) rises above 60.

Note: "200 SMA" classically means 200 *daily* candles. If you're backtesting
on intraday candles, either load daily data or lower `sma_period` in
initialize() to match what you actually want (e.g. 200 five-minute candles
is a very different, much faster line than a 200-day SMA).
"""

def initialize(ctx):
    ctx.params.setdefault("sma_period", 200)
    ctx.params.setdefault("rsi_period", 4)
    ctx.params.setdefault("rsi_buy_below", 20)
    ctx.params.setdefault("rsi_sell_above", 60)


def _rsi(closes, period):
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def on_candle(ctx, candle, history):
    sma_period = ctx.params["sma_period"]
    rsi_period = ctx.params["rsi_period"]

    if len(history) < sma_period + 1:
        return  # not enough candles yet for the SMA

    closes = history["close"]
    sma = closes.rolling(sma_period).mean().iloc[-1]
    rsi4 = _rsi(closes, rsi_period).iloc[-1]
    if sma != sma or rsi4 != rsi4:  # NaN guard
        return

    above_sma = candle["close"] > sma

    if ctx.position == 0:
        if above_sma:
            ctx.buy(lots=1)
            ctx.log(f"BUY (trend regime): close {candle['close']:.2f} above SMA{sma_period} {sma:.2f}")
        elif rsi4 < ctx.params["rsi_buy_below"]:
            ctx.buy(lots=1)
            ctx.log(f"BUY (mean-reversion): RSI{rsi_period} {rsi4:.1f} below {ctx.params['rsi_buy_below']}")
    else:  # already long
        if above_sma:
            pass  # trend regime overrides — hold regardless of RSI
        elif rsi4 > ctx.params["rsi_sell_above"]:
            ctx.exit_position(reason="rsi_overbought")
            ctx.log(f"EXIT: RSI{rsi_period} {rsi4:.1f} above {ctx.params['rsi_sell_above']} (below SMA{sma_period})")
'''

_INDEX_VS_PREMIUM_NOTE = (
    "Classically defined on Bank Nifty INDEX/FUTURES price action (that's what "
    "VWAP/CPR/opening-range/gap levels are meant to be computed on), then the "
    "actual trade is a CE/PE option buy when the signal fires. This engine only "
    "sees the one series you load, so: load INDEX/FUTURES data to validate "
    "*signal timing* (P&L will be in index points, not real premium P&L), or "
    "load an option-PREMIUM CSV to get *realistic P&L* (the indicators will "
    "then react to premium noise instead of the index, which is a materially "
    "different, usually choppier signal)."
)

ORB_BREAKOUT = '''\
"""Opening range breakout (long-only, one trade per day).

Documented backtests report win rates roughly in the 48%-71% range depending
on timeframe/config -- treat any "90% win rate" claim you see online for this
setup as unverified until you've run it yourself.

''' + _INDEX_VS_PREMIUM_NOTE + '''
"""

def initialize(ctx):
    ctx.params.setdefault("or_candles", 3)          # candles that form the opening range (3 x 5m = first 15 min)
    ctx.params.setdefault("target_r_multiple", 2.0)  # target = entry + R x (entry - opening-range low)
    ctx.state["current_date"] = None
    ctx.state["or_high"] = None
    ctx.state["or_low"] = None
    ctx.state["or_count"] = 0
    ctx.state["or_done"] = False
    ctx.state["traded_today"] = False


def on_candle(ctx, candle, history):
    today = candle["datetime"].date()
    if ctx.state["current_date"] != today:
        ctx.state["current_date"] = today
        ctx.state["or_high"] = None
        ctx.state["or_low"] = None
        ctx.state["or_count"] = 0
        ctx.state["or_done"] = False
        ctx.state["traded_today"] = False

    or_candles = ctx.params["or_candles"]

    if not ctx.state["or_done"]:
        ctx.state["or_count"] += 1
        ctx.state["or_high"] = candle["high"] if ctx.state["or_high"] is None else max(ctx.state["or_high"], candle["high"])
        ctx.state["or_low"] = candle["low"] if ctx.state["or_low"] is None else min(ctx.state["or_low"], candle["low"])
        if ctx.state["or_count"] >= or_candles:
            ctx.state["or_done"] = True
        return  # still building the opening range -- no trading yet

    if ctx.position == 0 and not ctx.state["traded_today"] and candle["close"] > ctx.state["or_high"]:
        risk = ctx.state["or_high"] - ctx.state["or_low"]
        if risk <= 0:
            return
        ctx.buy(lots=1, sl=ctx.state["or_low"], target_points=risk * ctx.params["target_r_multiple"])
        ctx.state["traded_today"] = True
        ctx.log(f"BUY ORB breakout: close {candle['close']:.2f} above opening-range high {ctx.state['or_high']:.2f}")
'''

VWAP_BOUNCE = '''\
"""VWAP bounce (long-only) -- a favourite intraday setup among Bank Nifty
traders: in an uptrend (price above VWAP), buy when price dips down to touch
VWAP and then closes back above it on a bullish candle; exit on a close back
below VWAP.

''' + _INDEX_VS_PREMIUM_NOTE + '''
"""

def initialize(ctx):
    ctx.params.setdefault("touch_tolerance_pct", 0.15)  # % of VWAP considered a "touch"
    ctx.state["current_date"] = None
    ctx.state["cum_pv"] = 0.0
    ctx.state["cum_vol"] = 0.0


def on_candle(ctx, candle, history):
    today = candle["datetime"].date()
    if ctx.state["current_date"] != today:
        ctx.state["current_date"] = today
        ctx.state["cum_pv"] = 0.0
        ctx.state["cum_vol"] = 0.0

    typical_price = (candle["high"] + candle["low"] + candle["close"]) / 3
    vol = candle["volume"] if candle["volume"] > 0 else 1  # guard against all-zero volume data
    ctx.state["cum_pv"] += typical_price * vol
    ctx.state["cum_vol"] += vol
    vwap = ctx.state["cum_pv"] / ctx.state["cum_vol"]

    tolerance = vwap * ctx.params["touch_tolerance_pct"] / 100
    bullish_candle = candle["close"] > candle["open"]
    touched_vwap = candle["low"] <= vwap + tolerance

    if ctx.position == 0:
        if candle["close"] > vwap and touched_vwap and bullish_candle:
            ctx.buy(lots=1)
            ctx.log(f"BUY VWAP bounce: close {candle['close']:.2f} above VWAP {vwap:.2f}")
    else:
        if candle["close"] < vwap:
            ctx.exit_position(reason="vwap_breakdown")
            ctx.log(f"EXIT: close {candle['close']:.2f} below VWAP {vwap:.2f}")
'''

SUPERTREND_EMA = '''\
"""Supertrend + EMA(5/20) trend-following (long-only). Buy when Supertrend
turns bullish AND the 5-EMA is above the 20-EMA; exit when Supertrend flips
bearish. Bank-Nifty-tuned defaults: ATR period 7-10, multiplier 2.0-2.5 --
the classic ATR-10/multiplier-3 defaults are built for daily charts and lag
badly on 5-minute Bank Nifty data.

''' + _INDEX_VS_PREMIUM_NOTE + '''
"""

def initialize(ctx):
    ctx.params.setdefault("atr_period", 10)
    ctx.params.setdefault("multiplier", 2.5)
    ctx.params.setdefault("ema_fast", 5)
    ctx.params.setdefault("ema_slow", 20)
    ctx.state["tr_list"] = []
    ctx.state["prev_close"] = None
    ctx.state["final_upper"] = None
    ctx.state["final_lower"] = None
    ctx.state["trend"] = 1  # 1 = bullish, -1 = bearish


def on_candle(ctx, candle, history):
    atr_period = ctx.params["atr_period"]
    mult = ctx.params["multiplier"]

    high, low, close = candle["high"], candle["low"], candle["close"]
    prev_close = ctx.state["prev_close"]
    tr = (high - low) if prev_close is None else max(high - low, abs(high - prev_close), abs(low - prev_close))

    ctx.state["tr_list"].append(tr)
    if len(ctx.state["tr_list"]) > atr_period:
        ctx.state["tr_list"].pop(0)
    if len(ctx.state["tr_list"]) < atr_period:
        ctx.state["prev_close"] = close
        return  # not enough candles for the ATR yet

    atr = sum(ctx.state["tr_list"]) / atr_period
    mid = (high + low) / 2
    basic_upper = mid + mult * atr
    basic_lower = mid - mult * atr

    if ctx.state["final_upper"] is None:
        final_upper, final_lower = basic_upper, basic_lower
    else:
        final_upper = basic_upper if (basic_upper < ctx.state["final_upper"] or prev_close > ctx.state["final_upper"]) else ctx.state["final_upper"]
        final_lower = basic_lower if (basic_lower > ctx.state["final_lower"] or prev_close < ctx.state["final_lower"]) else ctx.state["final_lower"]

    trend = ctx.state["trend"]
    if trend == 1 and close < final_lower:
        trend = -1
    elif trend == -1 and close > final_upper:
        trend = 1

    ctx.state["final_upper"], ctx.state["final_lower"], ctx.state["trend"] = final_upper, final_lower, trend
    ctx.state["prev_close"] = close

    ema_fast_n, ema_slow_n = ctx.params["ema_fast"], ctx.params["ema_slow"]
    if len(history) < ema_slow_n + 1:
        return
    closes = history["close"]
    ema_fast = closes.ewm(span=ema_fast_n, adjust=False).mean().iloc[-1]
    ema_slow = closes.ewm(span=ema_slow_n, adjust=False).mean().iloc[-1]

    if ctx.position == 0:
        if trend == 1 and ema_fast > ema_slow:
            ctx.buy(lots=1)
            ctx.log(f"BUY: Supertrend bullish, EMA{ema_fast_n} {ema_fast:.2f} > EMA{ema_slow_n} {ema_slow:.2f}")
    else:
        if trend == -1:
            ctx.exit_position(reason="supertrend_flip")
            ctx.log("EXIT: Supertrend flipped bearish")
'''

CPR_BREAKOUT = '''\
"""Central Pivot Range (CPR) breakout (long-only, one trade per day).
Computes yesterday's CPR (pivot / top-central / bottom-central) from
`history`, and only takes the breakout-above-TC trade on days where the CPR
is "narrow" (a well-known heuristic: a narrow CPR tends to precede a
trending/breakout day; a wide CPR tends to precede range-bound chop).

''' + _INDEX_VS_PREMIUM_NOTE + '''
"""

def initialize(ctx):
    ctx.params.setdefault("narrow_cpr_pct", 0.15)  # CPR is "narrow" if width < this % of prev close
    ctx.params.setdefault("target_level", "r2")     # "r1" or "r2"
    ctx.state["current_date"] = None
    ctx.state["levels"] = None
    ctx.state["is_narrow"] = False
    ctx.state["traded_today"] = False


def _cpr_levels(history, today):
    day_dates = history["datetime"].dt.date
    prev_days = history[day_dates < today]
    if prev_days.empty:
        return None
    last_day = prev_days["datetime"].dt.date.iloc[-1]
    day_data = prev_days[prev_days["datetime"].dt.date == last_day]
    h, l, c = day_data["high"].max(), day_data["low"].min(), day_data["close"].iloc[-1]
    pivot = (h + l + c) / 3
    bc = (h + l) / 2
    tc = 2 * pivot - bc
    return {
        "pivot": pivot, "tc": max(tc, bc), "bc": min(tc, bc),
        "r1": 2 * pivot - l, "r2": pivot + (h - l), "prev_close": c,
    }


def on_candle(ctx, candle, history):
    today = candle["datetime"].date()
    if ctx.state["current_date"] != today:
        ctx.state["current_date"] = today
        ctx.state["traded_today"] = False
        levels = _cpr_levels(history, today)
        ctx.state["levels"] = levels
        ctx.state["is_narrow"] = (
            levels is not None
            and (levels["tc"] - levels["bc"]) / levels["prev_close"] * 100 < ctx.params["narrow_cpr_pct"]
        )

    levels = ctx.state["levels"]
    if levels is None or not ctx.state["is_narrow"] or ctx.state["traded_today"]:
        return

    if ctx.position == 0 and candle["close"] > levels["tc"]:
        target = levels["r2"] if ctx.params["target_level"] == "r2" else levels["r1"]
        ctx.buy(lots=1, sl=levels["bc"], target=target)
        ctx.state["traded_today"] = True
        ctx.log(f"BUY CPR breakout: close {candle['close']:.2f} above TC {levels['tc']:.2f} (narrow CPR day)")
'''

GAP_FADE = '''\
"""Gap-down fade / gap-fill (long-only, one trade per day). On a gap-down
open beyond a minimum size, buy on the first bullish reversal candle,
targeting a fill back to yesterday's close.

''' + _INDEX_VS_PREMIUM_NOTE + '''
"""

def initialize(ctx):
    ctx.params.setdefault("min_gap_pct", 0.3)     # minimum gap-down size (% of prev close) to consider
    ctx.params.setdefault("confirm_candles", 1)   # how many candles from the open to look for a reversal
    ctx.state["current_date"] = None
    ctx.state["prev_close"] = None
    ctx.state["gap_qualified"] = False
    ctx.state["candle_count"] = 0
    ctx.state["day_low"] = None
    ctx.state["traded_today"] = False


def on_candle(ctx, candle, history):
    today = candle["datetime"].date()
    if ctx.state["current_date"] != today:
        ctx.state["current_date"] = today
        ctx.state["candle_count"] = 0
        ctx.state["traded_today"] = False
        ctx.state["day_low"] = candle["low"]

        day_dates = history["datetime"].dt.date
        prev_days = history[day_dates < today]
        prev_close = prev_days["close"].iloc[-1] if not prev_days.empty else None
        ctx.state["prev_close"] = prev_close

        if prev_close:
            gap_pct = (candle["open"] - prev_close) / prev_close * 100
            ctx.state["gap_qualified"] = gap_pct < -ctx.params["min_gap_pct"]
        else:
            ctx.state["gap_qualified"] = False

    ctx.state["candle_count"] += 1
    ctx.state["day_low"] = min(ctx.state["day_low"], candle["low"])

    if (
        ctx.position == 0
        and not ctx.state["traded_today"]
        and ctx.state["gap_qualified"]
        and ctx.state["candle_count"] <= ctx.params["confirm_candles"]
        and candle["close"] > candle["open"]  # bullish reversal candle
    ):
        ctx.buy(lots=1, sl=ctx.state["day_low"], target=ctx.state["prev_close"])
        ctx.state["traded_today"] = True
        ctx.log(f"BUY gap-fade: gap-down day, reversal candle, targeting gap-fill at {ctx.state['prev_close']:.2f}")
'''

EXAMPLES = {
    "SMA crossover": SMA_CROSSOVER,
    "RSI mean-reversion": RSI_MEAN_REVERSION,
    "200SMA regime + RSI(4)": SMA200_RSI_REGIME,
    "Opening range breakout": ORB_BREAKOUT,
    "VWAP bounce": VWAP_BOUNCE,
    "Supertrend + EMA(5/20)": SUPERTREND_EMA,
    "CPR breakout": CPR_BREAKOUT,
    "Gap-down fade": GAP_FADE,
}
