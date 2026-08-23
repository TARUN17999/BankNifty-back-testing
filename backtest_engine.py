"""Bar-by-bar backtest engine for Bank Nifty options-buying strategies.

Execution model — read this before trusting the numbers
---------------------------------------------------------
- Each row of the input data is one OHLCV candle of whatever instrument you
  loaded (an option's premium, a futures price, or the index itself).
- A stop-loss / target set on an open position is checked against the
  CURRENT candle's high/low BEFORE your `on_candle()` runs for that candle.
  The position must already be at least one candle old (we never check SL/
  target on the same candle it was opened, to avoid look-ahead).
- If both the stop-loss and the target are inside the same candle's
  high-low range, the stop-loss is assumed to trigger first. This is the
  conservative (worst-case) assumption — real fills could go either way.
- Any `ctx.buy()` / `ctx.sell()` / `ctx.exit_position()` call you make
  inside `on_candle()` fills at that same candle's CLOSE price. There is no
  look-ahead into future candles.
- If a position is still open on the last candle of your data, it is
  force-closed at the last close price with exit_reason="backtest_end".
- Optional end-of-day square-off (a checkbox in the app) force-closes any
  open position on the last candle of each calendar day — common for
  intraday options-buying strategies.
- Slippage (in premium points) and a flat brokerage-per-order are the only
  transaction-cost modelling. There is no bid/ask, liquidity, or impact-cost
  simulation, and option margin requirements are not modelled — this engine
  is built for BUYING strategies (you pay premium, you never owe more than
  you paid).

None of this guarantees your live results will match the backtest. Treat
this as a strategy-validation tool, not a promise of future performance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Trade:
    entry_time: pd.Timestamp
    entry_price: float
    side: str  # "long" or "short"
    quantity: int  # already multiplied by lot size
    lots: int
    sl: float | None = None
    target: float | None = None
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    pnl: float | None = None
    pnl_pct: float | None = None

    def to_dict(self) -> dict:
        return {
            "entry_time": self.entry_time,
            "side": self.side,
            "lots": self.lots,
            "quantity": self.quantity,
            "entry_price": round(self.entry_price, 2),
            "sl": round(self.sl, 2) if self.sl is not None else None,
            "target": round(self.target, 2) if self.target is not None else None,
            "exit_time": self.exit_time,
            "exit_price": round(self.exit_price, 2) if self.exit_price is not None else None,
            "exit_reason": self.exit_reason,
            "pnl": round(self.pnl, 2) if self.pnl is not None else None,
            "pnl_pct": round(self.pnl_pct, 2) if self.pnl_pct is not None else None,
        }


class StrategyError(Exception):
    """Raised when the pasted strategy code fails to load or run."""


class Context:
    """Passed into your strategy's `initialize(ctx)` and `on_candle(ctx, candle, history)`.

    Trading API
    -----------
    ctx.buy(lots=1, sl=None, target=None, sl_points=None, target_points=None)
        Open (or add to) a long position, sized in lots of `ctx.lot_size` units.
    ctx.sell(lots=1, sl=None, target=None, sl_points=None, target_points=None)
        Open (or add to) a short position. Only meaningful for option-selling /
        writing strategies — margin is NOT modelled, so treat P&L here as
        indicative only.
    ctx.exit_position(reason="signal")
        Close the entire open position at this candle's close.
    ctx.set_stop_loss(price=None, points=None)
        Update the stop-loss on the open position. `points` is measured from
        the entry price (in the position's favour being positive risk, i.e.
        always a positive number of points away from entry).
    ctx.set_target(price=None, points=None)
        Same as above, for the profit target.

    State & info
    -------------
    ctx.position      Signed open quantity (+units long, -units short, 0 flat).
    ctx.avg_price     Entry price of the current open position (0 if flat).
    ctx.cash          Cash balance after all fills so far.
    ctx.equity        Cash + mark-to-market value of the open position.
    ctx.params        dict of parameters you can read (set from the app UI).
    ctx.state         A plain dict that persists across candles — use it for
                       your own indicators/lookback state instead of globals.
    ctx.log(msg)      Add a line to the run log shown in the Results tab.
    """

    def __init__(self, capital: float, lot_size: int, brokerage_per_order: float,
                 slippage_points: float, params: dict | None = None):
        self.initial_capital = capital
        self.cash = capital
        self.lot_size = lot_size
        self.brokerage_per_order = brokerage_per_order
        self.slippage_points = slippage_points

        self.position = 0
        self.avg_price = 0.0
        self.sl: float | None = None
        self.target: float | None = None

        self.params: dict = params or {}
        self.state: dict = {}

        self.trades: list[Trade] = []
        self.logs: list[str] = []

        self._open_trade: Trade | None = None
        self._current_time = None
        self._current_price = None
        self._pending_exit_reason: str | None = None

    # -- strategy-facing API -------------------------------------------------
    def buy(self, lots: int = 1, sl: float | None = None, target: float | None = None,
            sl_points: float | None = None, target_points: float | None = None) -> None:
        self._enter("long", lots, sl, target, sl_points, target_points)

    def sell(self, lots: int = 1, sl: float | None = None, target: float | None = None,
             sl_points: float | None = None, target_points: float | None = None) -> None:
        self._enter("short", lots, sl, target, sl_points, target_points)

    def exit_position(self, reason: str = "signal") -> None:
        if self.position != 0:
            self._pending_exit_reason = reason

    def set_stop_loss(self, price: float | None = None, points: float | None = None) -> None:
        if price is not None:
            self.sl = price
        elif points is not None and self._open_trade is not None:
            sign = 1 if self._open_trade.side == "long" else -1
            self.sl = self._open_trade.entry_price - sign * abs(points)
        if self._open_trade is not None:
            self._open_trade.sl = self.sl

    def set_target(self, price: float | None = None, points: float | None = None) -> None:
        if price is not None:
            self.target = price
        elif points is not None and self._open_trade is not None:
            sign = 1 if self._open_trade.side == "long" else -1
            self.target = self._open_trade.entry_price + sign * abs(points)
        if self._open_trade is not None:
            self._open_trade.target = self.target

    def log(self, msg: object) -> None:
        self.logs.append(f"[{self._current_time}] {msg}")

    @property
    def equity(self) -> float:
        if self.position == 0 or self._current_price is None:
            return self.cash
        return self.cash + self.position * self._current_price

    # -- internal --------------------------------------------------------
    def _enter(self, side, lots, sl, target, sl_points, target_points):
        if lots <= 0:
            return
        if self.position != 0:
            # Already in a trade — ignore rather than silently pyramid, so
            # strategies that forget to check ctx.position don't blow up.
            return
        qty = int(lots) * self.lot_size
        fill_price = self._current_price + (
            self.slippage_points if side == "long" else -self.slippage_points
        )
        signed_qty = qty if side == "long" else -qty
        self._apply_fill(signed_qty, fill_price)
        self.avg_price = fill_price
        trade = Trade(
            entry_time=self._current_time,
            entry_price=fill_price,
            side=side,
            quantity=qty,
            lots=int(lots),
        )
        self._open_trade = trade
        self.trades.append(trade)
        if sl is not None or sl_points is not None:
            self.set_stop_loss(price=sl, points=sl_points)
        if target is not None or target_points is not None:
            self.set_target(price=target, points=target_points)

    def _apply_fill(self, signed_qty_change: int, price: float) -> None:
        self.cash -= signed_qty_change * price
        self.cash -= self.brokerage_per_order
        self.position += signed_qty_change

    def _close_open_position(self, price: float, reason: str) -> None:
        if self.position == 0 or self._open_trade is None:
            return
        exit_price = price - (
            self.slippage_points if self.position > 0 else -self.slippage_points
        )
        self._apply_fill(-self.position, exit_price)
        trade = self._open_trade
        trade.exit_time = self._current_time
        trade.exit_price = exit_price
        trade.exit_reason = reason
        side_sign = 1 if trade.side == "long" else -1
        trade.pnl = side_sign * (exit_price - trade.entry_price) * trade.quantity \
            - 2 * self.brokerage_per_order
        cost_basis = trade.entry_price * trade.quantity
        trade.pnl_pct = (trade.pnl / cost_basis * 100) if cost_basis else 0.0
        self.avg_price = 0.0
        self.sl = None
        self.target = None
        self._open_trade = None


@dataclass
class BacktestResult:
    equity_curve: pd.DataFrame  # columns: datetime, equity
    trades: pd.DataFrame
    metrics: dict
    logs: list[str] = field(default_factory=list)


def load_strategy(code: str) -> dict:
    """exec() the pasted strategy source and return its namespace.

    Runs with full Python privileges in your own local environment — same
    trust model as running any .py file you wrote yourself. Only paste code
    you wrote or trust.
    """
    namespace: dict = {"pd": pd, "np": np}
    try:
        compiled = compile(code, "<strategy>", "exec")
        exec(compiled, namespace)  # noqa: S102 - intentional, see docstring above.
    except Exception as exc:  # noqa: BLE001 - surface any error to the UI
        raise StrategyError(f"Strategy code failed to load: {exc}") from exc
    if "on_candle" not in namespace or not callable(namespace["on_candle"]):
        raise StrategyError("Strategy code must define a function `on_candle(ctx, candle, history)`.")
    return namespace


def run_backtest(
    df: pd.DataFrame,
    strategy_namespace: dict,
    capital: float,
    lot_size: int,
    brokerage_per_order: float = 20.0,
    slippage_points: float = 0.0,
    eod_squareoff: bool = True,
    params: dict | None = None,
) -> BacktestResult:
    """Run `strategy_namespace`'s on_candle()/initialize() over `df`.

    `df` must have columns: datetime, open, high, low, close, volume
    (see data_utils.normalize_ohlc for how uploaded CSVs are coerced into
    this shape).
    """
    if df.empty:
        raise StrategyError("No data to backtest — load some candles first.")

    ctx = Context(capital, lot_size, brokerage_per_order, slippage_points, params)

    initialize = strategy_namespace.get("initialize")
    if callable(initialize):
        try:
            initialize(ctx)
        except Exception as exc:  # noqa: BLE001
            raise StrategyError(f"Error in initialize(ctx): {exc}") from exc

    on_candle = strategy_namespace["on_candle"]

    equity_rows = []
    dates = df["datetime"].dt.date
    n = len(df)

    for i in range(n):
        row = df.iloc[i]
        ctx._current_time = row["datetime"]
        ctx._current_price = row["close"]

        # 1. Check SL/target on an already-open position (>= 1 candle old).
        if ctx.position != 0 and ctx._open_trade is not None and ctx._open_trade.entry_time != row["datetime"]:
            hit_sl = hit_target = False
            if ctx.position > 0:
                hit_sl = ctx.sl is not None and row["low"] <= ctx.sl
                hit_target = ctx.target is not None and row["high"] >= ctx.target
            else:
                hit_sl = ctx.sl is not None and row["high"] >= ctx.sl
                hit_target = ctx.target is not None and row["low"] <= ctx.target
            if hit_sl:
                ctx._close_open_position(ctx.sl, "stop_loss")
            elif hit_target:
                ctx._close_open_position(ctx.target, "target")

        # 2. Let the strategy react to this candle.
        try:
            history = df.iloc[: i + 1]
            on_candle(ctx, row, history)
        except Exception as exc:  # noqa: BLE001
            raise StrategyError(f"Error in on_candle() at {row['datetime']}: {exc}") from exc

        # 3. Apply any exit the strategy requested this candle.
        if ctx._pending_exit_reason is not None:
            ctx._close_open_position(row["close"], ctx._pending_exit_reason)
            ctx._pending_exit_reason = None

        # 4. Optional end-of-day square-off.
        is_last_of_day = (i == n - 1) or (dates.iloc[i] != dates.iloc[i + 1])
        if eod_squareoff and is_last_of_day and ctx.position != 0:
            ctx._close_open_position(row["close"], "eod_squareoff")

        equity_rows.append({"datetime": row["datetime"], "equity": ctx.equity})

    # Force-close anything still open at the very end of the data.
    if ctx.position != 0:
        last_row = df.iloc[-1]
        ctx._current_time = last_row["datetime"]
        ctx._close_open_position(last_row["close"], "backtest_end")
        equity_rows[-1]["equity"] = ctx.equity

    equity_curve = pd.DataFrame(equity_rows)
    trades_df = pd.DataFrame([t.to_dict() for t in ctx.trades])
    metrics = compute_metrics(equity_curve, trades_df, capital)

    return BacktestResult(equity_curve=equity_curve, trades=trades_df, metrics=metrics, logs=ctx.logs)


def compute_metrics(equity_curve: pd.DataFrame, trades: pd.DataFrame, initial_capital: float) -> dict:
    closed = trades[trades["exit_time"].notna()] if not trades.empty else trades
    total_trades = len(closed)

    if total_trades == 0 or equity_curve.empty:
        final_equity = equity_curve["equity"].iloc[-1] if not equity_curve.empty else initial_capital
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "net_pnl": final_equity - initial_capital,
            "net_pnl_pct": (final_equity - initial_capital) / initial_capital * 100 if initial_capital else 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "profit_factor": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "expectancy": 0.0,
            "max_drawdown": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe": 0.0,
            "max_consecutive_wins": 0,
            "max_consecutive_losses": 0,
            "final_equity": final_equity,
        }

    wins_mask = closed["pnl"] > 0
    losses_mask = closed["pnl"] < 0
    wins = int(wins_mask.sum())
    losses = int(losses_mask.sum())
    win_rate = wins / total_trades * 100 if total_trades else 0.0

    gross_profit = closed.loc[wins_mask, "pnl"].sum()
    gross_loss = closed.loc[losses_mask, "pnl"].sum()  # negative
    net_pnl = closed["pnl"].sum()
    profit_factor = (gross_profit / abs(gross_loss)) if gross_loss != 0 else float("inf") if gross_profit > 0 else 0.0

    avg_win = closed.loc[wins_mask, "pnl"].mean() if wins else 0.0
    avg_loss = closed.loc[losses_mask, "pnl"].mean() if losses else 0.0
    expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)

    eq = equity_curve["equity"]
    running_max = eq.cummax()
    drawdown = eq - running_max
    drawdown_pct = drawdown / running_max.replace(0, np.nan) * 100
    max_drawdown = drawdown.min()
    max_drawdown_pct = drawdown_pct.min()

    # Sharpe on daily-resampled equity returns (annualised, rf = 0).
    daily_eq = equity_curve.set_index("datetime")["equity"].resample("D").last().dropna()
    daily_returns = daily_eq.pct_change().dropna()
    if len(daily_returns) > 1 and daily_returns.std() != 0:
        sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252)
    else:
        sharpe = 0.0

    # Consecutive win/loss streaks.
    outcome = np.where(wins_mask, 1, np.where(losses_mask, -1, 0))
    max_w = max_l = cur_w = cur_l = 0
    for o in outcome:
        if o > 0:
            cur_w += 1
            cur_l = 0
        elif o < 0:
            cur_l += 1
            cur_w = 0
        else:
            cur_w = cur_l = 0
        max_w = max(max_w, cur_w)
        max_l = max(max_l, cur_l)

    final_equity = eq.iloc[-1]

    return {
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "net_pnl": net_pnl,
        "net_pnl_pct": net_pnl / initial_capital * 100 if initial_capital else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
        "max_drawdown": max_drawdown,
        "max_drawdown_pct": max_drawdown_pct,
        "sharpe": sharpe,
        "max_consecutive_wins": max_w,
        "max_consecutive_losses": max_l,
        "final_equity": final_equity,
    }
