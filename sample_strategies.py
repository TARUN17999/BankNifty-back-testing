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

EXAMPLES = {
    "SMA crossover": SMA_CROSSOVER,
    "RSI mean-reversion": RSI_MEAN_REVERSION,
    "200SMA regime + RSI(4)": SMA200_RSI_REGIME,
}
