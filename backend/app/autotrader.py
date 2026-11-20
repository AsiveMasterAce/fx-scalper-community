"""Pure decision logic for the automated trading loop (no I/O, easy to test).

app.server owns the background thread, state persistence and broker calls; it
asks this module what to close, whether a daily limit is hit, and what to open.
"""

from datetime import datetime, timedelta, timezone
import re
from typing import List, Optional, Tuple

DIRECTION_SIDES = {"both": {"BUY", "SELL"}, "buy": {"BUY"}, "sell": {"SELL"}}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def default_auto_state() -> dict:
    return {
        "running": False,
        "mode": "paper",
        "day": "",
        "realized_pnl": 0.0,
        "trades_today": 0,
        "halted_reason": "",
        "started_at": None,
        "last_tick": None,
        "last_error": "",
        "pending_live": [],
        "paused_reason": "",
        "wins_today": 0,
        "losses_today": 0,
        "loss_streak": 0,
        "cooldown_until": None,
        "closed_trades": [],
        "flips_today": 0,
        "symbol_paused_until": {},
    }


def parse_iso(value: object) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def roll_day(auto: dict, now: datetime) -> dict:
    """Reset the daily counters at UTC midnight."""
    today = now.astimezone(timezone.utc).date().isoformat()
    if auto.get("day") != today:
        auto.update(
            {
                "day": today,
                "realized_pnl": 0.0,
                "trades_today": 0,
                "halted_reason": "",
                "wins_today": 0,
                "losses_today": 0,
                "loss_streak": 0,
                "flips_today": 0,
            }
        )
    return auto


def pending_stake(auto: dict) -> float:
    return sum(float(item.get("stake") or 0.0) for item in auto.get("pending_live", []))


def limit_breached(auto: dict, settings: dict) -> str:
    """Daily profit target / loss limit. Unsettled live stakes count as at risk."""
    realized = float(auto.get("realized_pnl") or 0.0)
    target = float(settings.get("daily_profit_target") or 0.0)
    limit = float(settings.get("daily_loss_limit") or 0.0)
    if target > 0 and realized >= target:
        return f"Daily profit target reached ({realized:.2f} >= {target:.2f})."
    if limit > 0 and pending_stake(auto) - realized >= limit:
        return f"Daily loss limit reached ({realized:.2f} realized, {pending_stake(auto):.2f} at risk)."
    return ""


def paper_pnl(position: dict, price: float) -> float:
    entry = float(position["entry_price"])
    move = (float(price) - entry) / entry * float(position["exposure"])
    return round(move if position["side"] == "BUY" else -move, 2)


def due_positions(portfolio: List[dict], now: datetime, hold_minutes: float) -> List[dict]:
    """Auto-opened paper positions whose holding time is up."""
    cutoff = now - timedelta(minutes=float(hold_minutes))
    due = []
    for position in portfolio:
        opened = parse_iso(position.get("opened_at"))
        if position.get("auto") and opened is not None and opened <= cutoff:
            due.append(position)
    return due


def _safe_float(value: object) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def update_peak_profit(item: dict, unrealized_profit: Optional[float]) -> Optional[float]:
    """Track the highest unrealized profit seen so far on an open trade."""
    if unrealized_profit is None:
        return _safe_float(item.get("peak_profit"))
    current = round(float(unrealized_profit), 4)
    item["unrealized_profit"] = current
    prev_peak = _safe_float(item.get("peak_profit"))
    peak = current if prev_peak is None else max(prev_peak, current)
    item["peak_profit"] = round(peak, 4)
    return item["peak_profit"]


def evaluate_early_exit(
    item: dict,
    status: dict,
    signal: Optional[dict],
    settings: dict,
) -> Optional[str]:
    """Decide whether an open trade should exit early ("Human Eye" Trade Guardian).

    Supports:
    - Hard Early Take-Profit (locks in target % of stake immediately)
    - Spike Grab (exits on a stretched/overextended impulse candle while green, before retest)
    - Trailing Profit Lock (lets big runners ride, exits on first pullback from peak_profit)
    - Reversal Exit (exits early if the chart signal flips to the opposite direction)
    - Hard Early Stop-Loss (cuts fast adverse moves before full -100% expiry loss)
    """
    if not settings.get("active_exit_enabled", False):
        return None
    if not status.get("is_valid_to_sell", False):
        return None
    unrealized = _safe_float(status.get("unrealized_profit"))
    if unrealized is None:
        return None
    stake = _safe_float(item.get("stake") if item.get("stake") is not None else item.get("exposure"))
    if stake is None or stake <= 0:
        return None

    profit_pct = (unrealized / stake) * 100.0
    peak = update_peak_profit(item, unrealized)
    peak_pct = ((peak / stake) * 100.0) if peak is not None else profit_pct
    role = str(item.get("role") or "").lower()

    # 1. Hard Early Take-Profit (scalps use their quicker target when configured)
    if role == "scalp" and float(settings.get("scalp_take_profit_pct") or 0.0) > 0:
        tp_pct = float(settings.get("scalp_take_profit_pct") or 30.0)
    else:
        tp_pct = float(settings.get("early_take_profit_pct") or 0.0)
    if tp_pct > 0 and profit_pct >= tp_pct:
        label = "Scalp take-profit" if role == "scalp" else "Early take-profit"
        return f"{label} hit ({profit_pct:+.1f}% >= +{tp_pct:.1f}%)"

    # 2. Spike Grab: bank profit on a long stretched / overextended candle before it retests
    # Anchor runners skip small spike grabs so they can ride the bigger trend via trailing lock.
    if role != "anchor" and settings.get("spike_grab_enabled", True) and signal:
        min_spike_pct = float(settings.get("spike_grab_min_profit_pct") or 25.0)
        stretched = str(signal.get("candle_quality") or "").lower() == "stretched"
        band_dist = _safe_float(signal.get("distance_to_band"))
        overextended = band_dist is not None and band_dist >= 0.85
        if profit_pct >= min_spike_pct and (stretched or overextended):
            tag = "stretched candle" if stretched else "band overextension"
            return f"Spike grab before retest ({profit_pct:+.1f}% on {tag})"

    # 3. Trailing Profit Lock: let big moves run, close on pullback from peak
    if settings.get("trailing_profit_enabled", True):
        arm_pct = float(settings.get("trailing_arm_pct") or 30.0)
        pullback_pct = float(settings.get("trailing_pullback_pct") or 12.0)
        if arm_pct > 0 and pullback_pct > 0 and peak_pct >= arm_pct and profit_pct > 0:
            drawdown_from_peak = peak_pct - profit_pct
            if drawdown_from_peak >= pullback_pct:
                return (
                    f"Trailing profit lock (peaked {peak_pct:+.1f}%, pulled back "
                    f"{drawdown_from_peak:.1f}% to {profit_pct:+.1f}%)"
                )

    # 4. Reversal Exit: chart flipped to the opposite side
    if settings.get("reversal_exit_enabled", True) and signal:
        side = str(item.get("side") or "").upper()
        decision = str(signal.get("decision") or "").upper()
        opposite = (side == "BUY" and decision == "SELL") or (side == "SELL" and decision == "BUY")
        if opposite:
            return f"Chart reversed to {decision} while {side} ({profit_pct:+.1f}%)"

    # 5. Hard Early Stop-Loss
    sl_pct = float(settings.get("early_stop_loss_pct") or 0.0)
    if sl_pct > 0 and profit_pct <= -sl_pct:
        return f"Early stop-loss hit ({profit_pct:+.1f}% <= -{sl_pct:.1f}%)"

    return None


def _can_scale_in(
    symbol_positions: List[dict],
    side: str,
    settings: dict,
    now: Optional[datetime],
) -> bool:
    """Allow adding a stacked position on a symbol only when existing trades are winning in the same direction."""
    max_per_symbol = max(1, int(settings.get("max_positions_per_symbol") or 1))
    if len(symbol_positions) >= max_per_symbol:
        return False
    if not symbol_positions:
        return True
    # Never hedge or average into a different direction
    if any(str(pos.get("side") or "").upper() != side for pos in symbol_positions):
        return False
    # Respect spacing between stacked entries
    cooldown_min = float(settings.get("scale_in_cooldown_minutes") or 0.0)
    if cooldown_min > 0 and now is not None:
        cutoff = now - timedelta(minutes=cooldown_min)
        for pos in symbol_positions:
            opened = parse_iso(pos.get("opened_at"))
            if opened is not None and opened > cutoff:
                return False
    # Require the MOST RECENT existing trade on this symbol to be clearly in profit
    # (prevents stacking into a pullback just because an older anchor trade is still green)
    min_profit_pct = float(settings.get("scale_in_min_profit_pct") or 20.0)
    latest = max(
        symbol_positions,
        key=lambda pos: parse_iso(pos.get("opened_at")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    stake = _safe_float(latest.get("stake") if latest.get("stake") is not None else latest.get("exposure"))
    unrealized = _safe_float(latest.get("unrealized_profit"))
    if stake and stake > 0 and unrealized is not None:
        return (unrealized / stake) * 100.0 >= min_profit_pct
    return False


_CLOSED_WINDOW_RE = re.compile(
    r"not available from\s+(\d{2}):(\d{2}):(\d{2})\s+to\s+(\d{2}):(\d{2}):(\d{2})",
    re.IGNORECASE,
)


def parse_market_closed_until(error_message: str, now: datetime) -> Optional[str]:
    """Parse Deriv 'Trading is not available from HH:MM:SS to HH:MM:SS' into a UTC reopen ISO timestamp."""
    match = _CLOSED_WINDOW_RE.search(str(error_message or ""))
    if not match:
        return None
    end_h, end_m, end_s = int(match.group(4)), int(match.group(5)), int(match.group(6))
    now_utc = now.astimezone(timezone.utc)
    reopen = now_utc.replace(hour=end_h, minute=end_m, second=end_s, microsecond=0) + timedelta(minutes=1)
    if reopen <= now_utc:
        reopen += timedelta(days=1)
    return iso(reopen)


def resolve_live_stake(settings: dict, balance: Optional[float]) -> float:
    """Resolve live stake: fixed live_trade_stake, or % of live balance clamped to [min, max]."""
    base = float(settings.get("live_trade_stake", 1.0))
    if not settings.get("dynamic_stake_enabled", False):
        return round(max(0.35, base), 2)
    bal = _safe_float(balance)
    if bal is None or bal <= 0:
        return round(max(0.35, base), 2)
    pct = float(settings.get("dynamic_stake_pct") or 2.0)
    low = max(0.35, float(settings.get("dynamic_stake_min") or 0.50))
    high = max(low, float(settings.get("dynamic_stake_max") or 5.0))
    raw = bal * (pct / 100.0)
    return round(max(low, min(high, raw)), 2)


def clamp_duration_before_close(
    duration: int,
    unit: str,
    now: datetime,
    settings: dict,
) -> Tuple[int, str, bool]:
    """Trim hour/minute durations so a contract expires before the daily market close.

    Returns (resolved_duration, resolved_unit, was_clamped).
    """
    unit_norm = str(unit or "m").strip().lower()
    if not settings.get("auto_clamp_duration_enabled", False):
        return int(duration), unit_norm, False
    if unit_norm not in {"m", "h"}:
        return int(duration), unit_norm, False
    total_minutes = int(duration) * (60 if unit_norm == "h" else 1)
    close_hour = int(settings.get("market_close_hour_utc", 21)) % 24
    buffer_minutes = 10
    now_utc = now.astimezone(timezone.utc)
    close_dt = now_utc.replace(hour=close_hour, minute=0, second=0, microsecond=0)
    if close_dt <= now_utc:
        close_dt += timedelta(days=1)
    available = int((close_dt - now_utc).total_seconds() // 60) - buffer_minutes
    if available < 15 or total_minutes <= available:
        return int(duration), unit_norm, False
    return available, "m", True


def pick_entries(
    market: List[dict],
    portfolio: List[dict],
    settings: dict,
    open_count: int,
    *,
    now: Optional[datetime] = None,
    symbol_paused_until: Optional[dict] = None,
) -> List[dict]:
    """Signals to act on: allowed direction, per-symbol cap (with winning scale-in), under max_open_positions."""
    sides = DIRECTION_SIDES.get(str(settings.get("trade_direction") or "both"), DIRECTION_SIDES["both"])
    paused = symbol_paused_until or {}
    by_symbol: dict = {}
    for position in portfolio:
        by_symbol.setdefault(position["symbol"], []).append(position)
    room = max(0, int(settings.get("max_open_positions", 6)) - open_count)
    picks = []
    for row in market:
        if len(picks) >= room:
            break
        decision = row.get("decision")
        symbol = row["symbol"]
        if decision not in sides:
            continue
        if now is not None and symbol in paused:
            until = parse_iso(paused.get(symbol))
            if until is not None and now < until:
                continue
        existing = by_symbol.get(symbol, [])
        if not existing:
            picks.append(row)
            by_symbol[symbol] = [{"symbol": symbol, "side": decision}]
        elif _can_scale_in(existing, decision, settings, now):
            picks.append(row)
            # Only add at most one stacked trade per symbol per tick
            by_symbol[symbol] = existing + [{"symbol": symbol, "side": decision, "opened_at": iso(now) if now else None}]
    return picks


MAX_CLOSED_TRADES = 200


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def day_summary(auto: dict, now: datetime) -> str:
    """One-line recap of the previous UTC day, or "" if the day hasn't changed or nothing happened."""
    day = auto.get("day")
    if not day or day == now.astimezone(timezone.utc).date().isoformat():
        return ""
    trades, wins, losses = (int(auto.get(key) or 0) for key in ("trades_today", "wins_today", "losses_today"))
    if not (trades or wins or losses):
        return ""
    return "{}: {} trades opened, {} won / {} lost, realized P&L {:+.2f}.".format(
        day, trades, wins, losses, float(auto.get("realized_pnl") or 0.0)
    )


def record_close(auto: dict, settings: dict, *, symbol: str, side: str, pnl: object, now: datetime, mode: str) -> None:
    """Book a closed/settled trade: daily P&L, win/loss counters, loss streak, cooldown, history."""
    pnl = round(float(pnl), 2)
    auto["realized_pnl"] = round(float(auto.get("realized_pnl") or 0.0) + pnl, 2)
    if pnl > 0:
        auto["wins_today"] = int(auto.get("wins_today") or 0) + 1
        auto["loss_streak"] = 0
    elif pnl < 0:
        auto["losses_today"] = int(auto.get("losses_today") or 0) + 1
        auto["loss_streak"] = int(auto.get("loss_streak") or 0) + 1
        cooldown = float(settings.get("loss_cooldown_minutes") or 0)
        if cooldown > 0:
            auto["cooldown_until"] = iso(now + timedelta(minutes=cooldown))
    closed = list(auto.get("closed_trades") or [])
    closed.append({"closed_at": iso(now), "symbol": symbol, "side": side, "pnl": pnl, "mode": mode})
    auto["closed_trades"] = closed[-MAX_CLOSED_TRADES:]


def in_trading_hours(now: datetime, start: object, end: object) -> bool:
    """UTC hour window [start, end). start == end means always; start > end wraps past midnight."""
    start, end = int(start or 0), int(end or 0)
    if start == end or (start == 0 and end == 24):
        return True
    hour = now.astimezone(timezone.utc).hour
    return start <= hour < end if start < end else (hour >= start or hour < end)


def blocking_reason(auto: dict, settings: dict, now: datetime) -> Tuple[str, bool]:
    """Why no new trade may open right now, and whether that halts trading for the rest of the day."""
    reason = limit_breached(auto, settings)
    if reason:
        return reason, True
    max_trades = int(settings.get("max_trades_per_day") or 0)
    if max_trades and int(auto.get("trades_today") or 0) >= max_trades:
        return f"Max trades per day reached ({max_trades}).", True
    max_streak = int(settings.get("max_loss_streak") or 0)
    streak = int(auto.get("loss_streak") or 0)
    if max_streak and streak >= max_streak:
        return f"{streak} losses in a row (limit {max_streak}).", True
    until = parse_iso(auto.get("cooldown_until"))
    if until is not None and now < until:
        return f"Cooling down after a loss until {auto['cooldown_until']}.", False
    start, end = int(settings.get("trading_hours_start") or 0), int(settings.get("trading_hours_end") or 0)
    if not in_trading_hours(now, start, end):
        return f"Outside trading hours ({start:02d}:00-{end:02d}:00 UTC).", False
    return "", False


def performance(closed_trades: List[dict]) -> dict:
    """Win rate and P&L per symbol plus a cumulative equity curve from closed auto trades."""
    by_symbol: dict = {}
    equity, curve, wins = 0.0, [], 0
    for trade in closed_trades:
        pnl = float(trade.get("pnl") or 0.0)
        row = by_symbol.setdefault(trade["symbol"], {"symbol": trade["symbol"], "trades": 0, "wins": 0, "pnl": 0.0})
        row["trades"] += 1
        row["wins"] += 1 if pnl > 0 else 0
        row["pnl"] = round(row["pnl"] + pnl, 2)
        wins += 1 if pnl > 0 else 0
        equity = round(equity + pnl, 2)
        curve.append(equity)
    for row in by_symbol.values():
        row["win_rate"] = round(row["wins"] / row["trades"], 4)
    total = len(closed_trades)
    return {
        "trades": total,
        "wins": wins,
        "win_rate": round(wins / total, 4) if total else None,
        "pnl": equity,
        "by_symbol": sorted(by_symbol.values(), key=lambda row: row["pnl"], reverse=True),
        "equity_curve": curve,
    }
