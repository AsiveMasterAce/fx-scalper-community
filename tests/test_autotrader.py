"""Automated trading loop: direction, daily limits, kill switch, paper + broker adapter."""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from app import autotrader, server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPLAY = os.path.join(ROOT, "backend", "data", "replay_sample.json")
NOW = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


def test_limits():
    auto = {"realized_pnl": 10.0, "pending_live": []}
    assert "profit target" in autotrader.limit_breached(auto, {"daily_profit_target": 10})
    assert autotrader.limit_breached(auto, {"daily_profit_target": 0, "daily_loss_limit": 0}) == ""
    losing = {"realized_pnl": -3.0, "pending_live": [{"stake": 2.0}]}
    assert "loss limit" in autotrader.limit_breached(losing, {"daily_loss_limit": 5})
    assert autotrader.limit_breached(losing, {"daily_loss_limit": 6}) == ""


def test_pick_entries_respects_direction_held_and_room():
    market = [
        {"symbol": "A", "decision": "BUY"},
        {"symbol": "B", "decision": "SELL"},
        {"symbol": "C", "decision": "BUY"},
        {"symbol": "D", "decision": "HOLD"},
    ]
    held = [{"symbol": "A"}]
    picks = autotrader.pick_entries(market, held, {"trade_direction": "buy", "max_open_positions": 6}, 1)
    assert [row["symbol"] for row in picks] == ["C"]
    picks = autotrader.pick_entries(market, [], {"trade_direction": "both", "max_open_positions": 2}, 0)
    assert [row["symbol"] for row in picks] == ["A", "B"]


def test_roll_day_resets_counters():
    auto = {"day": "2026-01-04", "realized_pnl": -9.0, "trades_today": 4, "halted_reason": "x"}
    autotrader.roll_day(auto, NOW)
    assert auto["day"] == "2026-01-05" and auto["realized_pnl"] == 0.0
    assert auto["trades_today"] == 0 and auto["halted_reason"] == "" and auto["loss_streak"] == 0


def test_paper_pnl_sign():
    position = {"entry_price": 1.0, "exposure": 100.0, "side": "SELL"}
    assert autotrader.paper_pnl(position, 0.99) == 1.0


@pytest.fixture
def client():
    server.app.config.update(TESTING=True)
    with server.app.test_client() as test_client:
        test_client.post("/api/reset")
        test_client.post(
            "/api/config",
            json={
                "symbols": [{"symbol": "frxEURUSD", "asset_class": "FOREX"}],
                "market_source": "replay",
                "market_replay_file": REPLAY,
                "auto_hold_minutes": 10,
            },
        )
        yield test_client
        test_client.post("/api/auto/stop")


def replay_decision():
    state = server.load_state()
    server.run_market_scan(state)
    return server.market_view(state["settings"])[0]["decision"]


def test_paper_loop_opens_then_closes_after_hold(client):
    decision = replay_decision()
    assert decision in {"BUY", "SELL"}, "replay fixture should produce a signal"
    assert client.post("/api/auto/start", json={"mode": "paper"}).status_code == 200
    auto = server.auto_trade_tick(now=NOW)
    state = server.load_state()
    assert auto["trades_today"] == 1
    assert [p["symbol"] for p in state["portfolio"] if p["auto"]] == ["frxEURUSD"]
    server.auto_trade_tick(now=datetime.now(timezone.utc) + timedelta(minutes=11))
    types = [row["type"] for row in server.load_state()["trade_history"]]
    assert "AUTO_CLOSE" in types and "AUTO_OPEN" in types and "AUTO_START" in types


def test_direction_filter_blocks_signal(client):
    decision = replay_decision()
    client.post("/api/config", json={"trade_direction": "sell" if decision == "BUY" else "buy"})
    client.post("/api/auto/start", json={"mode": "paper"})
    assert server.auto_trade_tick(now=NOW)["trades_today"] == 0


def test_stop_is_a_kill_switch(client):
    client.post("/api/auto/start", json={"mode": "paper"})
    client.post("/api/auto/stop")
    auto = server.auto_trade_tick(now=NOW)
    assert auto["running"] is False and auto["trades_today"] == 0
    assert server.load_state()["portfolio"] == []


def test_live_requires_opt_in(client):
    response = client.post("/api/auto/start", json={"mode": "live"})
    assert response.status_code == 400 and "live trading" in response.get_json()["error"].lower()


class FakeBroker:
    name = "fake"
    profit = None

    def open_trade(self, *, symbol, side, amount, **options):
        return {"ref": "42", "price": amount, "contract_type": "CALL"}

    def trade_status(self, ref):
        return {"settled": FakeBroker.profit is not None, "profit": FakeBroker.profit}

    def close(self):
        pass


def test_live_loop_uses_broker_adapter_and_halts_on_loss(client, monkeypatch):
    monkeypatch.setattr(server, "live_broker_from_settings", lambda settings: FakeBroker())
    client.post("/api/config", json={"live_trading_enabled": True, "live_trade_stake": 2, "daily_loss_limit": 2})
    assert client.post("/api/auto/start", json={"mode": "live"}).status_code == 200
    FakeBroker.profit = None
    auto = server.auto_trade_tick(now=NOW)
    assert [item["ref"] for item in auto["pending_live"]] == ["42"]
    assert "loss limit" in server.auto_trade_tick(now=NOW)["halted_reason"]
    FakeBroker.profit = -2.0
    auto = server.auto_trade_tick(now=NOW)
    assert auto["realized_pnl"] == -2.0 and auto["pending_live"] == []
    assert auto["trades_today"] == 1
    types = [row["type"] for row in server.load_state()["trade_history"]]
    assert "LIVE_SETTLED" in types and "AUTO_HALT" in types


def test_guards_halt_or_pause():
    auto = autotrader.default_auto_state()
    assert autotrader.blocking_reason(auto, {}, NOW) == ("", False)
    auto["trades_today"] = 3
    assert autotrader.blocking_reason(auto, {"max_trades_per_day": 3}, NOW)[1] is True
    auto = autotrader.default_auto_state()
    for pnl in (-1, -1):
        autotrader.record_close(
            auto, {"loss_cooldown_minutes": 15}, symbol="A", side="BUY", pnl=pnl, now=NOW, mode="paper"
        )
    reason, halt = autotrader.blocking_reason(auto, {"max_loss_streak": 2}, NOW)
    assert "2 losses in a row" in reason and halt
    reason, halt = autotrader.blocking_reason(auto, {}, NOW + timedelta(minutes=5))
    assert "Cooling down" in reason and not halt
    assert autotrader.blocking_reason(auto, {}, NOW + timedelta(minutes=16)) == ("", False)


def test_trading_hours_window():
    assert autotrader.in_trading_hours(NOW, 8, 17)
    assert not autotrader.in_trading_hours(NOW, 13, 17)
    assert autotrader.in_trading_hours(NOW, 22, 13)
    assert not autotrader.in_trading_hours(NOW, 22, 6)
    assert autotrader.in_trading_hours(NOW, 0, 0)
    hours = {"trading_hours_start": 13, "trading_hours_end": 17}
    reason, halt = autotrader.blocking_reason(autotrader.default_auto_state(), hours, NOW)
    assert "Outside trading hours (13:00-17:00 UTC)" in reason and not halt


def test_record_close_and_performance():
    auto = autotrader.default_auto_state()
    for symbol, pnl in (("A", 2.0), ("A", -1.0), ("B", 0.5)):
        autotrader.record_close(auto, {}, symbol=symbol, side="BUY", pnl=pnl, now=NOW, mode="paper")
    assert auto["realized_pnl"] == 1.5 and auto["wins_today"] == 2 and auto["losses_today"] == 1
    assert auto["loss_streak"] == 0
    stats = autotrader.performance(auto["closed_trades"])
    assert stats["trades"] == 3 and stats["equity_curve"] == [2.0, 1.0, 1.5]
    assert stats["by_symbol"][0] == {"symbol": "A", "trades": 2, "wins": 1, "pnl": 1.0, "win_rate": 0.5}


def test_daily_summary_event_on_rollover(client):
    client.post("/api/auto/start", json={"mode": "paper"})
    state = server.load_state()
    state["auto_trader"].update({"day": "2026-01-04", "trades_today": 2, "wins_today": 1, "losses_today": 1,
                                 "realized_pnl": 0.4})
    server.save_state(state)
    auto = server.auto_trade_tick(now=NOW)
    assert auto["day"] == "2026-01-05"
    summary = [row for row in server.load_state()["trade_history"] if row["type"] == "DAILY_SUMMARY"]
    assert summary and summary[0]["note"].startswith("2026-01-04: 2 trades opened, 1 won / 1 lost")


def test_max_trades_per_day_halts(client):
    client.post("/api/config", json={"max_trades_per_day": 1})
    client.post("/api/auto/start", json={"mode": "paper"})
    assert server.auto_trade_tick(now=NOW)["trades_today"] == 1
    assert "Max trades per day" in server.auto_trade_tick(now=NOW)["halted_reason"]
    assert server.bootstrap_payload(server.load_state())["auto_performance"]["trades"] == 0


def test_backtest_payout_scoring(client):
    client.post("/api/backtests/run", json={"payout_pct": 85, "candle_count": 1000})
    result = server.load_state()["last_backtest"]["candle_backtest"]["results"][0]
    assert result["trades"] > 0
    assert result["break_even_win_rate"] == round(1 / 1.85, 4)
    assert result["net_per_stake"] == round(result["wins"] * 0.85 - result["losses"], 4)
    assert len(result["equity_curve"]) == result["trades"]


def test_cli_backtest(capsys):
    from app import cli

    code = cli.main(["backtest", "--replay", REPLAY, "--symbol", "frxEURUSD", "--payout", "85", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0 and report["results"][0]["symbol"] == "frxEURUSD" and report["results"][0]["payout"] == 0.85
    assert cli.main(["backtest", "--replay", REPLAY, "--symbol", "frxEURUSD"]) == 0
    assert "net/$1" not in capsys.readouterr().out


def test_early_exit_spike_grab_trailing_and_reversal():
    base = {
        "active_exit_enabled": True,
        "early_take_profit_pct": 60.0,
        "early_stop_loss_pct": 50.0,
        "spike_grab_enabled": True,
        "spike_grab_min_profit_pct": 25.0,
        "trailing_profit_enabled": True,
        "trailing_arm_pct": 30.0,
        "trailing_pullback_pct": 12.0,
        "reversal_exit_enabled": True,
    }
    item = {"symbol": "frxXAUUSD", "side": "BUY", "stake": 1.0}

    # Disabled when active_exit_enabled is False or broker disallows early sell
    assert autotrader.evaluate_early_exit(item, {"is_valid_to_sell": True, "unrealized_profit": 0.8}, None, {}) is None
    assert autotrader.evaluate_early_exit(item, {"is_valid_to_sell": False, "unrealized_profit": 0.8}, None, base) is None

    # Spike grab on a long stretched candle before retest
    reason = autotrader.evaluate_early_exit(
        dict(item),
        {"is_valid_to_sell": True, "unrealized_profit": 0.30},
        {"decision": "BUY", "candle_quality": "stretched", "distance_to_band": 0.5},
        base,
    )
    assert reason and "Spike grab" in reason

    # Trailing profit lock: lets a clean runner climb, exits on pullback from peak
    runner = dict(item)
    clean_sig = {"decision": "BUY", "candle_quality": "clean", "distance_to_band": 0.4}
    assert autotrader.evaluate_early_exit(runner, {"is_valid_to_sell": True, "unrealized_profit": 0.35}, clean_sig, base) is None
    assert autotrader.evaluate_early_exit(runner, {"is_valid_to_sell": True, "unrealized_profit": 0.52}, clean_sig, base) is None
    assert runner["peak_profit"] == 0.52
    reason = autotrader.evaluate_early_exit(runner, {"is_valid_to_sell": True, "unrealized_profit": 0.39}, clean_sig, base)
    assert reason and "Trailing profit lock" in reason

    # Reversal exit when chart flips to opposite direction
    reason = autotrader.evaluate_early_exit(
        dict(item),
        {"is_valid_to_sell": True, "unrealized_profit": -0.15},
        {"decision": "SELL", "candle_quality": "clean", "distance_to_band": 0.3},
        base,
    )
    assert reason and "Chart reversed to SELL" in reason

    # Hard early stop-loss
    reason = autotrader.evaluate_early_exit(
        dict(item),
        {"is_valid_to_sell": True, "unrealized_profit": -0.55},
        {"decision": "HOLD", "candle_quality": "clean", "distance_to_band": 0.3},
        base,
    )
    assert reason and "Early stop-loss" in reason


def test_winning_scale_in_stacks_only_on_winners_with_spacing():
    market = [{"symbol": "frxXAUUSD", "decision": "BUY"}]
    settings = {
        "trade_direction": "both",
        "max_open_positions": 6,
        "max_positions_per_symbol": 2,
        "scale_in_min_profit_pct": 20.0,
        "scale_in_cooldown_minutes": 5,
    }
    # Losing trade -> never scale in
    losing = [{"symbol": "frxXAUUSD", "side": "BUY", "stake": 1.0, "unrealized_profit": -0.2,
               "opened_at": autotrader.iso(NOW - timedelta(minutes=10))}]
    assert autotrader.pick_entries(market, losing, settings, 1, now=NOW) == []

    # Winning trade but too recent -> wait for cooldown spacing
    too_soon = [{"symbol": "frxXAUUSD", "side": "BUY", "stake": 1.0, "unrealized_profit": 0.3,
                 "opened_at": autotrader.iso(NOW - timedelta(minutes=2))}]
    assert autotrader.pick_entries(market, too_soon, settings, 1, now=NOW) == []

    # Winning trade past cooldown -> scale in!
    winning = [{"symbol": "frxXAUUSD", "side": "BUY", "stake": 1.0, "unrealized_profit": 0.25,
                "opened_at": autotrader.iso(NOW - timedelta(minutes=6))}]
    picks = autotrader.pick_entries(market, winning, settings, 1, now=NOW)
    assert [row["symbol"] for row in picks] == ["frxXAUUSD"]

    # Already at max_positions_per_symbol -> stop stacking
    assert autotrader.pick_entries(market, winning + winning, settings, 2, now=NOW) == []


class GuardianFakeBroker:
    name = "fake"
    next_id = 100
    unrealized = None
    valid_to_sell = True
    sold_refs = []

    def open_trade(self, *, symbol, side, amount, **options):
        GuardianFakeBroker.next_id += 1
        return {"ref": str(GuardianFakeBroker.next_id), "price": amount, "contract_type": "CALL"}

    def trade_status(self, ref):
        return {
            "settled": False,
            "profit": None,
            "unrealized_profit": GuardianFakeBroker.unrealized,
            "is_valid_to_sell": GuardianFakeBroker.valid_to_sell,
        }

    def sell_early(self, ref, stake=0.0, price=0.0):
        GuardianFakeBroker.sold_refs.append(ref)
        return {"profit": float(GuardianFakeBroker.unrealized or 0.0), "sold_for": stake + float(GuardianFakeBroker.unrealized or 0.0)}

    def close(self):
        pass


def test_live_loop_early_exit_and_instant_reentry(client, monkeypatch):
    GuardianFakeBroker.next_id = 100
    GuardianFakeBroker.sold_refs = []
    GuardianFakeBroker.unrealized = 0.0
    GuardianFakeBroker.valid_to_sell = True
    monkeypatch.setattr(server, "live_broker_from_settings", lambda settings: GuardianFakeBroker())
    client.post(
        "/api/config",
        json={
            "live_trading_enabled": True,
            "live_trade_stake": 1.0,
            "active_exit_enabled": True,
            "early_take_profit_pct": 40.0,
        },
    )
    assert client.post("/api/auto/start", json={"mode": "live"}).status_code == 200
    auto = server.auto_trade_tick(now=NOW)
    assert [item["ref"] for item in auto["pending_live"]] == ["101"]

    # Price surges to +$0.45 (+45% >= +40% TP) -> sells 101 early AND immediately re-enters 102 on the same tick!
    GuardianFakeBroker.unrealized = 0.45
    auto = server.auto_trade_tick(now=NOW + timedelta(minutes=1))
    assert GuardianFakeBroker.sold_refs == ["101"]
    assert auto["realized_pnl"] == 0.45
    assert [item["ref"] for item in auto["pending_live"]] == ["102"]
    notes = [row["note"] for row in server.load_state()["trade_history"] if row["type"] == "LIVE_SETTLED"]
    assert any("closed early" in note and "Early take-profit" in note for note in notes)


def test_market_closed_auto_pause_and_dynamic_stake():
    # Parse Deriv market-closed window
    until = autotrader.parse_market_closed_until(
        "Trading is not available from 21:00:00 to 23:59:59.",
        datetime(2026, 11, 19, 21, 15, tzinfo=timezone.utc),
    )
    assert until == "2026-11-20T00:00:59Z"

    # pick_entries skips paused symbols until reopen time
    market = [{"symbol": "frxXAUUSD", "decision": "SELL"}]
    paused = {"frxXAUUSD": until}
    assert autotrader.pick_entries(
        market, [], {"trade_direction": "both", "max_open_positions": 6}, 0,
        now=datetime(2026, 11, 19, 22, 0, tzinfo=timezone.utc),
        symbol_paused_until=paused,
    ) == []
    assert len(autotrader.pick_entries(
        market, [], {"trade_direction": "both", "max_open_positions": 6}, 0,
        now=datetime(2026, 11, 20, 0, 5, tzinfo=timezone.utc),
        symbol_paused_until=paused,
    )) == 1

    # Auto-compounding dynamic stake sizing
    dyn = {
        "live_trade_stake": 1.0,
        "dynamic_stake_enabled": True,
        "dynamic_stake_pct": 2.0,
        "dynamic_stake_min": 0.50,
        "dynamic_stake_max": 5.0,
    }
    assert autotrader.resolve_live_stake(dyn, 100.0) == 2.0
    assert autotrader.resolve_live_stake(dyn, 10.0) == 0.50  # clamped to min
    assert autotrader.resolve_live_stake(dyn, 1000.0) == 5.0  # clamped to max
    assert autotrader.resolve_live_stake({**dyn, "dynamic_stake_enabled": False}, 100.0) == 1.0


def test_manual_early_close_and_guardian_fast_tick(client, monkeypatch):
    GuardianFakeBroker.next_id = 200
    GuardianFakeBroker.sold_refs = []
    GuardianFakeBroker.unrealized = 0.20
    GuardianFakeBroker.valid_to_sell = True
    monkeypatch.setattr(server, "live_broker_from_settings", lambda settings: GuardianFakeBroker())
    client.post(
        "/api/config",
        json={
            "live_trading_enabled": True,
            "live_trade_stake": 1.0,
            "active_exit_enabled": True,
            "early_take_profit_pct": 50.0,
        },
    )
    client.post("/api/auto/start", json={"mode": "live"})
    auto = server.auto_trade_tick(now=NOW)
    assert [item["ref"] for item in auto["pending_live"]] == ["201"]

    # Fast guardian tick updates unrealized_profit & peak_profit without full scan
    auto = server.guardian_tick(now=NOW + timedelta(seconds=10))
    assert auto["pending_live"][0]["unrealized_profit"] == 0.20
    assert auto["pending_live"][0]["peak_profit"] == 0.20

    # Manual 1-click early close via API works on Deriv-style contracts
    resp = client.post("/api/live-positions/201/close")
    assert resp.status_code == 200
    assert GuardianFakeBroker.sold_refs == ["201"]
    assert server.load_state()["auto_trader"]["pending_live"] == []


def test_stop_and_reverse_flips_on_reversal(client, monkeypatch):
    decision = replay_decision()
    opposite = "SELL" if decision == "BUY" else "BUY"
    GuardianFakeBroker.next_id = 300
    GuardianFakeBroker.sold_refs = []
    GuardianFakeBroker.unrealized = -0.10
    GuardianFakeBroker.valid_to_sell = True
    monkeypatch.setattr(server, "live_broker_from_settings", lambda settings: GuardianFakeBroker())
    client.post(
        "/api/config",
        json={
            "live_trading_enabled": True,
            "live_trade_stake": 1.0,
            "active_exit_enabled": True,
            "reversal_exit_enabled": True,
            "stop_and_reverse_enabled": True,
            "max_flips_per_day": 2,
        },
    )
    client.post("/api/auto/start", json={"mode": "live"})
    # Seed an open trade on the OPPOSITE side of the replay signal
    state = server.load_state()
    state["auto_trader"]["pending_live"] = [
        {"ref": "300", "symbol": "frxEURUSD", "side": opposite, "stake": 1.0, "opened_at": autotrader.iso(NOW)}
    ]
    server.save_state(state)

    auto = server.auto_trade_tick(now=NOW + timedelta(minutes=1))
    assert GuardianFakeBroker.sold_refs == ["300"]
    assert auto["flips_today"] == 1
    assert len(auto["pending_live"]) == 1 and auto["pending_live"][0]["side"] == decision
