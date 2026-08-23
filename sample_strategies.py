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

EXAMPLES = {
    "SMA crossover": SMA_CROSSOVER,
    "RSI mean-reversion": RSI_MEAN_REVERSION,
}
