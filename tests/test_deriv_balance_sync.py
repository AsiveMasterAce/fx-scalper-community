"""Tests for Deriv account selection, real-time balance subscription, and post-trade sync."""

import json
import os
import queue
import time

import pytest
from websocket import WebSocketTimeoutException

from app import server
from app.brokers import DerivBroker
from app.deriv_service import DerivBalanceSyncService, DerivCommunityClient, DerivServiceError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPLAY = os.path.join(ROOT, "backend", "data", "replay_sample.json")


@pytest.fixture
def client():
    server.app.config.update(TESTING=True)
    server.BALANCE_SYNC.reset()
    with server.app.test_client() as test_client:
        test_client.post("/api/reset")
        yield test_client
        test_client.post("/api/auto/stop")
        server.BALANCE_SYNC.reset()
        test_client.post("/api/reset")


def test_select_options_account_ignores_stale_demo_id_in_real_mode(monkeypatch):
    client = DerivCommunityClient(
        app_id="34Wlf1aCGIjxxnkCqBz1K",
        token="pat_example_token",
        options_account_mode="real",
        options_account_id="DOT95143565",
    )
    monkeypatch.setattr(
        client,
        "_options_accounts",
        lambda: [
            {"account_id": "DOT95143565", "account_type": "demo", "balance": "10001.59", "currency": "USD"},
            {"account_id": "ROT92913033", "account_type": "real", "balance": "10.00", "currency": "USD"},
        ],
    )

    selected = client._select_options_account()
    assert selected["account_id"] == "ROT92913033"
    assert selected["account_type"] == "real"


def test_select_options_account_never_falls_back_from_real_to_demo(monkeypatch):
    client = DerivCommunityClient(
        app_id="34Wlf1aCGIjxxnkCqBz1K",
        token="pat_example_token",
        options_account_mode="real",
        options_account_id="DOT95143565",
    )
    monkeypatch.setattr(
        client,
        "_options_accounts",
        lambda: [
            {"account_id": "DOT95143565", "account_type": "demo", "balance": "10001.59", "currency": "USD"},
        ],
    )

    with pytest.raises(DerivServiceError, match="No Deriv 'real' options account"):
        client._select_options_account()


def test_config_mode_switch_clears_stale_account_id_and_cached_demo_status(client):
    client.post(
        "/api/config",
        json={
            "deriv_app_id": "34Wlf1aCGIjxxnkCqBz1K",
            "deriv_token": "pat_secret",
            "deriv_options_account_mode": "demo",
            "deriv_options_account_id": "DOT95143565",
        },
    )
    state = server.load_state()
    state["last_broker_status"] = {
        "ok": True,
        "connected": True,
        "loginid": "DOT95143565",
        "account_id": "DOT95143565",
        "account_type": "demo",
        "balance": 10000.0,
        "currency": "USD",
    }
    server.save_state(state)

    # Switching mode to real while form still submits the old DOT id clears it
    resp = client.post(
        "/api/config",
        json={
            "deriv_options_account_mode": "real",
            "deriv_options_account_id": "DOT95143565",
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["settings"]["deriv_options_account_mode"] == "real"
    assert body["settings"]["deriv_options_account_id"] == ""
    assert server.load_state()["last_broker_status"] is None
    assert body["broker_sync"]["balance"] is None


def test_realtime_balance_subscription_receives_updates():
    incoming: queue.Queue = queue.Queue()
    sent_payloads: list[dict] = []

    class FakeWS:
        def __init__(self):
            self.closed = False

        def settimeout(self, value):
            pass

        def send(self, raw):
            msg = json.loads(raw)
            sent_payloads.append(msg)
            req_id = msg.get("req_id")
            if msg.get("balance") == 1 and msg.get("subscribe") == 1:
                incoming.put(
                    json.dumps(
                        {
                            "req_id": req_id,
                            "msg_type": "balance",
                            "balance": {"balance": 10.0, "currency": "USD", "loginid": "ROT92913033"},
                            "subscription": {"id": "sub-real-1"},
                        }
                    )
                )
            elif msg.get("forget"):
                incoming.put(json.dumps({"req_id": req_id, "forget": 1, "msg_type": "forget"}))

        def recv(self):
            if self.closed:
                return ""
            try:
                return incoming.get(timeout=0.05)
            except queue.Empty:
                raise WebSocketTimeoutException("timeout")

        def close(self):
            self.closed = True

    class FakeClient(DerivCommunityClient):
        def connect(self):
            self.ws = FakeWS()
            self._otp_auth = True
            self._selected_account = {
                "account_id": "ROT92913033",
                "account_type": "real",
                "balance": "10.00",
                "currency": "USD",
            }
            return {
                "token_mode": "pat",
                "app_id": self.app_id,
                "account_id": "ROT92913033",
                "loginid": "ROT92913033",
                "balance": 10.0,
                "currency": "USD",
                "account_type": "real",
                "configured_mode": "real",
                "account_mode_mismatch": False,
                "options_account_id": "ROT92913033",
                "connected": True,
                "balance_synced": True,
            }

    service = DerivBalanceSyncService(client_factory=FakeClient, ping_interval=60.0, recv_timeout=0.05)
    settings = {
        "broker_provider": "deriv",
        "deriv_app_id": "34Wlf1aCGIjxxnkCqBz1K",
        "deriv_token": "pat_secret",
        "deriv_options_account_mode": "real",
    }
    try:
        service.ensure_started(settings)
        deadline = time.time() + 3.0
        while time.time() < deadline:
            status = service.get_status(settings)
            if status["subscription_active"] and status["balance"] == 10.0:
                break
            time.sleep(0.02)

        status = service.get_status(settings)
        assert status["connected"] is True
        assert status["subscription_active"] is True
        assert status["subscription_id"] == "sub-real-1"
        assert status["account_id"] == "ROT92913033"
        assert status["account_type"] == "real"
        assert status["balance"] == 10.0
        assert status["sync_status"] == "synced"

        # Push real-time post-settlement update from Deriv
        incoming.put(
            json.dumps(
                {
                    "msg_type": "balance",
                    "balance": {"balance": 11.14, "currency": "USD", "loginid": "ROT92913033"},
                }
            )
        )
        deadline = time.time() + 3.0
        while time.time() < deadline:
            status = service.get_status(settings)
            if status["balance"] == 11.14:
                break
            time.sleep(0.02)

        status = service.get_status(settings)
        assert status["balance"] == 11.14
        assert status["last_update_source"] == "subscription"
        assert status["account_mode_mismatch"] is False
    finally:
        service.stop()


def test_live_trade_open_refreshes_balance_after_purchase(client, monkeypatch):
    client.post(
        "/api/config",
        json={
            "live_trading_enabled": True,
            "deriv_app_id": "34Wlf1aCGIjxxnkCqBz1K",
            "deriv_token": "pat_secret",
            "deriv_options_account_mode": "real",
            "symbols": [{"symbol": "frxXAUUSD", "asset_class": "COMMODITIES"}],
        },
    )

    class FakeLiveClient:
        app_id = "34Wlf1aCGIjxxnkCqBz1K"
        token_mode = "pat"

        def __init__(self):
            self.bought = False

        def connect(self):
            return {
                "token_mode": "pat",
                "app_id": self.app_id,
                "account_id": "ROT92913033",
                "loginid": "ROT92913033",
                "options_account_id": "ROT92913033",
                "account_type": "real",
                "configured_mode": "real",
                "account_mode_mismatch": False,
                "balance": 10.0,
                "currency": "USD",
                "connected": True,
                "balance_synced": True,
            }

        def propose(self, **kwargs):
            return {"id": "prop-1", "ask_price": 1.5}

        def buy(self, proposal_id, price):
            self.bought = True
            return {"contract_id": 99001, "buy_price": 1.5, "balance_after": 8.5}

        def refresh_account_snapshot(self, *, update_source="refresh"):
            assert self.bought is True
            return {
                "token_mode": "pat",
                "app_id": self.app_id,
                "account_id": "ROT92913033",
                "loginid": "ROT92913033",
                "options_account_id": "ROT92913033",
                "account_type": "real",
                "configured_mode": "real",
                "account_mode_mismatch": False,
                "balance": 8.5,
                "currency": "USD",
                "connected": True,
                "balance_synced": True,
                "update_source": update_source,
            }

        def close(self):
            pass

    monkeypatch.setattr(server, "deriv_client_from_settings", lambda settings: FakeLiveClient())

    resp = client.post(
        "/api/live-trades/open",
        json={"symbol": "frxXAUUSD", "side": "BUY", "amount": 1.5, "duration": 5, "duration_unit": "m"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"]["balance"] == 8.5
    assert body["status"]["account_id"] == "ROT92913033"
    assert body["status"]["account_type"] == "real"
    assert body["status"]["update_source"] == "purchase"
    assert body["broker_sync"]["balance"] == 8.5
    assert body["broker_sync"]["last_update_source"] == "purchase"


def test_auto_trader_refreshes_balance_on_purchase_and_settlement_never_from_journal(client, monkeypatch):
    client.post(
        "/api/config",
        json={
            "live_trading_enabled": True,
            "deriv_app_id": "34Wlf1aCGIjxxnkCqBz1K",
            "deriv_token": "pat_secret",
            "deriv_options_account_mode": "real",
            "symbols": [{"symbol": "frxEURUSD", "asset_class": "FOREX"}],
            "market_source": "replay",
            "market_replay_file": REPLAY,
            "live_trade_stake": 1.5,
            "max_trades_per_day": 1,
        },
    )

    events = []

    class FakeAutoDerivBroker:
        name = "deriv"
        settles_itself = True
        sides = frozenset({"BUY", "SELL"})
        settled_profit = None

        def open_trade(self, *, symbol, side, amount, **options):
            events.append("open")
            return {
                "ref": "55501",
                "price": amount,
                "contract_type": "CALL",
                "account": {
                    "token_mode": "pat",
                    "account_id": "ROT92913033",
                    "loginid": "ROT92913033",
                    "options_account_id": "ROT92913033",
                    "account_type": "real",
                    "configured_mode": "real",
                    "account_mode_mismatch": False,
                    "balance": 8.5,
                    "currency": "USD",
                    "connected": True,
                    "balance_synced": True,
                    "update_source": "purchase",
                },
            }

        def trade_status(self, ref):
            return {"settled": FakeAutoDerivBroker.settled_profit is not None, "profit": FakeAutoDerivBroker.settled_profit}

        def refresh_balance(self, *, source="refresh"):
            events.append(f"refresh:{source}")
            return {
                "token_mode": "pat",
                "account_id": "ROT92913033",
                "loginid": "ROT92913033",
                "options_account_id": "ROT92913033",
                "account_type": "real",
                "configured_mode": "real",
                "account_mode_mismatch": False,
                "balance": 11.14,
                "currency": "USD",
                "connected": True,
                "balance_synced": True,
                "update_source": source,
            }

        def close(self):
            pass

    monkeypatch.setattr(server, "live_broker_from_settings", lambda settings: FakeAutoDerivBroker())
    assert client.post("/api/auto/start", json={"mode": "live"}).status_code == 200

    # Tick 1: opens live trade and refreshes post-purchase balance (8.50)
    FakeAutoDerivBroker.settled_profit = None
    server.auto_trade_tick()
    boot = client.get("/api/bootstrap").get_json()
    assert boot["broker_sync"]["account_id"] == "ROT92913033"
    assert boot["broker_sync"]["account_type"] == "real"
    assert boot["broker_sync"]["balance"] == 8.5
    assert boot["broker_sync"]["last_update_source"] == "purchase"

    # Tick 2: settles live trade (+1.14) and refreshes post-settlement balance from Deriv API (11.14, NOT 8.5 + 1.14!)
    FakeAutoDerivBroker.settled_profit = 1.14
    server.auto_trade_tick()
    boot = client.get("/api/bootstrap").get_json()
    assert "refresh:settlement" in events
    assert boot["broker_sync"]["balance"] == 11.14
    assert boot["broker_sync"]["last_update_source"] == "settlement"
    # Live P&L must not pollute simulated paper equity either
    assert boot["stats"]["paper_realized_pnl"] == 0.0
    assert boot["stats"]["paper_equity"] == 10000.0


def test_real_balance_is_never_fabricated_from_journal_pnl(client):
    client.post(
        "/api/config",
        json={
            "deriv_app_id": "34Wlf1aCGIjxxnkCqBz1K",
            "deriv_token": "pat_secret",
            "deriv_options_account_mode": "real",
        },
    )
    state = server.load_state()
    state["auto_trader"]["closed_trades"] = [
        {"closed_at": "2026-11-19T12:00:00Z", "symbol": "frxXAUUSD", "side": "PUT", "pnl": 1.14, "mode": "live"},
        {"closed_at": "2026-11-19T12:05:00Z", "symbol": "frxXAUUSD", "side": "PUT", "pnl": -1.50, "mode": "live"},
    ]
    state["last_broker_status"] = None
    server.save_state(state)

    boot = client.get("/api/bootstrap").get_json()
    assert boot["broker_sync"]["balance"] is None
    assert boot["stats"]["paper_realized_pnl"] == 0.0


def test_broker_balance_refresh_endpoint(client, monkeypatch):
    client.post(
        "/api/config",
        json={
            "deriv_app_id": "34Wlf1aCGIjxxnkCqBz1K",
            "deriv_token": "pat_secret",
            "deriv_options_account_mode": "real",
        },
    )
    monkeypatch.setattr(
        server.BALANCE_SYNC,
        "refresh_now",
        lambda settings, source="refresh": {
            "token_mode": "pat",
            "account_id": "ROT92913033",
            "loginid": "ROT92913033",
            "options_account_id": "ROT92913033",
            "account_type": "real",
            "configured_mode": "real",
            "account_mode_mismatch": False,
            "balance": 10.0,
            "currency": "USD",
            "connected": True,
            "balance_synced": True,
            "update_source": source,
        },
    )
    resp = client.post("/api/broker/balance/refresh")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["broker_sync"]["account_id"] == "ROT92913033"
    assert body["broker_sync"]["account_type"] == "real"
    assert body["broker_sync"]["balance"] == 10.0
    assert body["broker_sync"]["currency"] == "USD"


def test_deriv_broker_open_trade_refreshes_balance(monkeypatch):
    calls = []

    class FakeClient:
        app_id = "34Wlf1aCGIjxxnkCqBz1K"
        token_mode = "pat"

        def connect(self):
            return {"account_id": "ROT92913033", "loginid": "ROT92913033", "account_type": "real", "balance": 10.0}

        def propose(self, **kwargs):
            return {"id": "p-1", "ask_price": 1.5}

        def buy(self, proposal_id, price):
            calls.append("buy")
            return {"contract_id": 12345, "buy_price": 1.5, "balance_after": 8.5}

        def refresh_account_snapshot(self, *, update_source="refresh"):
            calls.append(f"refresh:{update_source}")
            return {
                "account_id": "ROT92913033",
                "loginid": "ROT92913033",
                "account_type": "real",
                "balance": 8.5,
                "currency": "USD",
                "balance_synced": True,
                "update_source": update_source,
            }

        def close(self):
            pass

    broker = DerivBroker({"deriv_app_id": "34Wlf1aCGIjxxnkCqBz1K", "deriv_token": "pat_secret", "deriv_options_account_mode": "real"})
    broker.client = FakeClient()
    result = broker.open_trade(symbol="frxXAUUSD", side="SELL", amount=1.5)
    assert calls == ["buy", "refresh:purchase"]
    assert result["account"]["balance"] == 8.5
    assert result["account"]["account_id"] == "ROT92913033"
