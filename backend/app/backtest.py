"""Walk-forward backtest over real candles, using the same rule engine as live scans.

At every bar the engine sees only the trailing `window` candles. A BUY/SELL
signal enters at the next bar's open (no look-ahead) and exits at the close
`horizon` bars later, like a fixed-duration contract. Positions never overlap.
Returns are raw price moves in percent; broker payouts and spreads are not
modelled, so treat the win rate as "direction was right", not as profit.
"""

from typing import Callable, Dict, List, Mapping, Optional, Sequence

from app.market_data import Candle, MarketDataError
from app.snapshot import (
    IndicatorSettings,
    build_snapshot,
    higher_timeframe_trend,
    minimum_candles,
    resample_candles,
)

MAX_TRADES_RETURNED = 50
DIRECTIONS = {"both": {"BUY", "SELL"}, "buy": {"BUY"}, "sell": {"SELL"}}


def clamp_int(value: object, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def contiguous_tail(candles: Sequence[Candle]) -> List[Candle]:
    """Longest run of strictly increasing epochs ending at the last candle.

    Replay fixtures are cycled when shorter than the requested count; the
    seam would otherwise read as a price gap.
    """
    if not candles:
        return []
    start = len(candles) - 1
    while start > 0 and candles[start - 1].epoch < candles[start].epoch:
        start -= 1
    return list(candles[start:])


def _max_drawdown(returns: Sequence[float]) -> float:
    peak = equity = drawdown = 0.0
    for value in returns:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _simulate_guardian_exit(
    *,
    symbol: str,
    asset_class: str,
    decision: str,
    entry_index: int,
    horizon: int,
    window: int,
    candles: Sequence[Candle],
    config: IndicatorSettings,
    settings: Mapping,
    evaluate: Callable[[dict, Mapping], dict],
    payout: Optional[float],
) -> tuple:
    """Walk bar-by-bar inside [entry_index .. entry_index + horizon - 1] simulating Active Trade Guardian."""
    entry_bar = candles[entry_index]
    entry_price = float(entry_bar.open)
    tp_pct = float(settings.get("early_take_profit_pct") or 50.0)
    sl_pct = float(settings.get("early_stop_loss_pct") or 50.0)
    spike_on = bool(settings.get("spike_grab_enabled", True))
    spike_min = float(settings.get("spike_grab_min_profit_pct") or 25.0)
    trail_on = bool(settings.get("trailing_profit_enabled", True))
    trail_arm = float(settings.get("trailing_arm_pct") or 30.0)
    trail_pb = float(settings.get("trailing_pullback_pct") or 12.0)
    rev_on = bool(settings.get("reversal_exit_enabled", True))

    # Scale raw % move into option-style % of stake using recent ATR (1.5x ATR ≈ full payout)
    try:
        entry_snap = build_snapshot(symbol, asset_class, candles[entry_index - window:entry_index], config)
        atr_pct = (float(entry_snap.get("atr") or 0.0) / entry_price) * 100.0
    except Exception:
        atr_pct = 0.0
    full_scale = max(atr_pct * 1.5, 0.15)
    max_payout_pct = (float(payout) * 100.0) if payout else 75.0

    peak_option_pct = 0.0
    for step in range(horizon):
        bar_idx = entry_index + step
        bar = candles[bar_idx]
        raw_move = (bar.close - entry_price) / entry_price * 100.0
        signed_move = raw_move if decision == "BUY" else -raw_move
        # Approximate live option P&L % of stake bounded in [-95%, +max_payout_pct]
        option_pct = max(-95.0, min(max_payout_pct, (signed_move / full_scale) * max_payout_pct))
        peak_option_pct = max(peak_option_pct, option_pct)

        if step == horizon - 1:
            return bar, signed_move, "expiry", (float(payout) if signed_move > 0 else -1.0) if payout else None

        if tp_pct > 0 and option_pct >= tp_pct:
            return bar, signed_move, "early_tp", round(option_pct / 100.0, 4) if payout else None

        bar_snap = None
        if (spike_on or rev_on) and bar_idx >= window - 1:
            try:
                bar_snap = build_snapshot(symbol, asset_class, candles[bar_idx - window + 1:bar_idx + 1], config)
            except MarketDataError:
                bar_snap = None

        if spike_on and bar_snap is not None:
            stretched = str(bar_snap.get("candle_quality") or "").lower() == "stretched"
            band_dist = float(bar_snap.get("distance_to_band") or 0.0)
            if option_pct >= spike_min and (stretched or band_dist >= 0.85):
                return bar, signed_move, "spike_grab", round(option_pct / 100.0, 4) if payout else None

        if trail_on and trail_arm > 0 and trail_pb > 0 and peak_option_pct >= trail_arm and option_pct > 0:
            if (peak_option_pct - option_pct) >= trail_pb:
                return bar, signed_move, "trailing_lock", round(option_pct / 100.0, 4) if payout else None

        if rev_on and bar_snap is not None:
            bar_dec = evaluate(bar_snap, settings).get("decision")
            if (decision == "BUY" and bar_dec == "SELL") or (decision == "SELL" and bar_dec == "BUY"):
                return bar, signed_move, "reversal", round(option_pct / 100.0, 4) if payout else None

        if sl_pct > 0 and option_pct <= -sl_pct:
            return bar, signed_move, "early_sl", round(option_pct / 100.0, 4) if payout else None

    exit_bar = candles[entry_index + horizon - 1]
    raw_move = (exit_bar.close - entry_price) / entry_price * 100.0
    signed_move = raw_move if decision == "BUY" else -raw_move
    return exit_bar, signed_move, "expiry", (float(payout) if signed_move > 0 else -1.0) if payout else None


def run_backtest(
    symbol: str,
    asset_class: str,
    candles: Sequence[Candle],
    settings: Optional[Mapping],
    evaluate: Callable[[dict, Mapping], dict],
    horizon: int = 5,
    window: int = 100,
    direction: str = "both",
    payout: Optional[float] = None,
    fee_pct: float = 0.0,
    use_guardian: bool = False,
) -> Dict[str, object]:
    """Walk-forward test. With `payout` (e.g. 0.85) each trade is also scored as a fixed-stake
    contract: +payout per  on a win, -1 on a loss. `fee_pct` (per side, e.g. 0.1 for Binance spot)
    adds net-of-fees returns for exchange-style trading. With `use_guardian=True` simulates
    Active Trade Guardian early exits (TP, spike grab, trailing lock, reversal, SL)."""
    settings = settings or {}
    config = IndicatorSettings.from_settings(settings)
    candles = contiguous_tail(candles)
    window = max(int(window), minimum_candles(config))
    horizon = max(1, int(horizon))
    allowed = DIRECTIONS.get(str(direction or "both").lower(), DIRECTIONS["both"])
    if len(candles) < window + horizon + 1:
        raise MarketDataError(
            "{} needs at least {} contiguous candles for a {}-bar window and {}-bar exit, got {}".format(
                symbol, window + horizon + 1, window, horizon, len(candles)
            )
        )

    mtf_enabled = bool(settings.get("mtf_confirm_enabled", False))
    base_gran = int(settings.get("market_granularity") or 300)
    htf_gran = int(settings.get("mtf_granularity") or 900)
    mtf_factor = max(2, int(round(htf_gran / base_gran))) if htf_gran > base_gran else 3

    trades: List[dict] = []
    guardian_stake_results: List[float] = []
    exit_reason_counts: Dict[str, int] = {}
    evaluated = signals = skipped = 0
    index = window - 1
    while index + horizon < len(candles):
        try:
            trail = candles[index - window + 1:index + 1]
            snapshot = build_snapshot(symbol, asset_class, trail, config)
            if mtf_enabled:
                htf_bars = resample_candles(candles[:index + 1], mtf_factor)
                snapshot["htf_trend"] = higher_timeframe_trend(htf_bars, config)
        except MarketDataError:
            skipped += 1
            index += 1
            continue
        evaluated += 1
        decision = evaluate(snapshot, settings).get("decision")
        if decision not in allowed:
            index += 1
            continue
        signals += 1
        entry_bar = candles[index + 1]
        if use_guardian:
            exit_bar, result, exit_reason, stake_pnl = _simulate_guardian_exit(
                symbol=symbol,
                asset_class=asset_class,
                decision=decision,
                entry_index=index + 1,
                horizon=horizon,
                window=window,
                candles=candles,
                config=config,
                settings=settings,
                evaluate=evaluate,
                payout=payout,
            )
            exit_reason_counts[exit_reason] = exit_reason_counts.get(exit_reason, 0) + 1
            if stake_pnl is not None:
                guardian_stake_results.append(stake_pnl)
            exit_bar_idx = next(
                (i for i in range(index + 1, index + horizon + 1) if candles[i].epoch == exit_bar.epoch),
                index + horizon,
            )
            step_advance = max(1, exit_bar_idx - index)
        else:
            exit_bar = candles[index + horizon]
            move = (exit_bar.close - entry_bar.open) / entry_bar.open * 100.0
            result = move if decision == "BUY" else -move
            exit_reason = "expiry"
            step_advance = horizon
        trade_row = {
            "side": decision,
            "entry_epoch": entry_bar.epoch,
            "exit_epoch": exit_bar.epoch,
            "entry": round(entry_bar.open, 5),
            "exit": round(exit_bar.close, 5),
            "return_pct": round(result, 4),
        }
        if use_guardian:
            trade_row["exit_reason"] = exit_reason
        trades.append(trade_row)
        index += step_advance

    returns = [trade["return_pct"] for trade in trades]
    wins = sum(1 for value in returns if value > 0)
    report = {
        "symbol": symbol,
        "asset_class": asset_class,
        "candles": len(candles),
        "period_start": candles[0].epoch,
        "period_end": candles[-1].epoch,
        "window": window,
        "horizon_bars": horizon,
        "bars_evaluated": evaluated,
        "bars_skipped": skipped,
        "signals": signals,
        "trades": len(trades),
        "wins": wins,
        "losses": len(trades) - wins,
        "win_rate": round(wins / len(trades), 4) if trades else None,
        "avg_return_pct": round(sum(returns) / len(returns), 4) if returns else None,
        "total_return_pct": round(sum(returns), 4),
        "max_drawdown_pct": round(_max_drawdown(returns), 4),
        "best_pct": max(returns) if returns else None,
        "worst_pct": min(returns) if returns else None,
        "recent_trades": trades[-MAX_TRADES_RETURNED:],
        "equity_curve": equity_curve(returns),
    }
    if use_guardian:
        early_exits = sum(count for reason, count in exit_reason_counts.items() if reason != "expiry")
        report["guardian_summary"] = {
            "enabled": True,
            "early_exits": early_exits,
            "exit_reasons": exit_reason_counts,
        }
    if fee_pct and fee_pct > 0:
        net = [value - 2.0 * fee_pct for value in returns]
        net_wins = sum(1 for value in net if value > 0)
        report.update(
            {
                "fee_pct": round(fee_pct, 4),
                "net_wins": net_wins,
                "net_win_rate": round(net_wins / len(net), 4) if net else None,
                "total_net_return_pct": round(sum(net), 4),
                "avg_net_return_pct": round(sum(net) / len(net), 4) if net else None,
                "max_net_drawdown_pct": round(_max_drawdown(net), 4),
                "net_equity_curve": equity_curve(net),
            }
        )
    if payout:
        stake = guardian_stake_results if (use_guardian and guardian_stake_results) else [
            payout if value > 0 else -1.0 for value in returns
        ]
        report.update(
            {
                "payout": round(payout, 4),
                "break_even_win_rate": round(1.0 / (1.0 + payout), 4),
                "net_per_stake": round(sum(stake), 4),
                "expectancy_per_stake": round(sum(stake) / len(stake), 4) if stake else None,
                "max_drawdown_stake": round(_max_drawdown(stake), 4),
            }
        )
    return report


def equity_curve(returns: Sequence[float], points: int = 200) -> List[float]:
    """Cumulative return after each trade, thinned to at most ``points`` values for charts."""
    curve, total = [], 0.0
    for value in returns:
        total += value
        curve.append(round(total, 4))
    step = -(-len(curve) // points) if curve else 1
    if step <= 1:
        return curve
    thinned = curve[step - 1::step]
    if thinned[-1] != curve[-1]:
        thinned.append(curve[-1])
    return thinned
