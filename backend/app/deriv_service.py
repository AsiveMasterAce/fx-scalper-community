from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from websocket import WebSocketTimeoutException, create_connection


DEFAULT_REST_URL = "https://api.derivws.com"
DEFAULT_WS_URL = "wss://ws.derivws.com/websockets/v3"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_float(value: object) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class DerivServiceError(RuntimeError):
    pass


class DerivCommunityClient:
    def __init__(
        self,
        *,
        app_id: str,
        token: str,
        rest_url: str = DEFAULT_REST_URL,
        ws_url: str = DEFAULT_WS_URL,
        options_account_mode: str = "demo",
        options_account_id: str = "",
    ) -> None:
        self.app_id = self._normalize_app_id(app_id)
        self.token = self._normalize_token(token)
        self.rest_url = (str(rest_url or DEFAULT_REST_URL).strip() or DEFAULT_REST_URL).rstrip("/")
        self.ws_url = str(ws_url or DEFAULT_WS_URL).strip() or DEFAULT_WS_URL
        self.options_account_mode = str(options_account_mode or "demo").strip().lower() or "demo"
        self.options_account_id = str(options_account_id or "").strip()
        self.ws = None
        self._otp_auth = False
        self._selected_account: Optional[dict] = None
        self._req_id = 0
        self._last_balance_info: Optional[dict] = None

    @staticmethod
    def _normalize_app_id(value: object) -> str:
        raw = str(value or "").strip()
        # Current Deriv applications use opaque alphanumeric IDs. Older apps
        # commonly used numeric IDs, so preserve any non-empty value and only
        # fall back to the public legacy ID when no App ID was supplied.
        return raw or "1089"

    @staticmethod
    def _normalize_token(value: object) -> str:
        token = str(value or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        token = token.replace("\u200b", "").replace("\ufeff", "")
        token = "".join(token.split())
        return token

    @property
    def token_mode(self) -> str:
        return "pat" if self.token.startswith("pat_") else "legacy"

    def close(self) -> None:
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
        self.ws = None

    def _ensure_credentials(self) -> None:
        if not self.token:
            raise DerivServiceError("Missing Deriv API token.")
        if not self.app_id:
            raise DerivServiceError("Missing Deriv app ID.")

    def _rest_headers(self) -> dict:
        self._ensure_credentials()
        return {
            "Deriv-App-ID": self.app_id,
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

    def _rest_call(self, method: str, path: str, payload: Optional[dict] = None) -> dict:
        url = f"{self.rest_url.rstrip('/')}/{path.lstrip('/')}"
        headers = self._rest_headers()
        if payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = requests.request(method.upper(), url, headers=headers, json=payload, timeout=15)
        except Exception as exc:
            raise DerivServiceError(f"Deriv REST request failed: {exc}") from exc
        try:
            data = response.json() if response.content else {}
        except Exception:
            data = {}
        if response.status_code >= 400:
            message = None
            errors = data.get("errors") if isinstance(data, dict) else None
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                message = errors[0].get("message")
            raise DerivServiceError(message or f"Deriv REST error {response.status_code} for {path}")
        return data if isinstance(data, dict) else {}

    def _options_accounts(self) -> list[dict]:
        payload = self._rest_call("GET", "/trading/v1/options/accounts")
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            nested = data.get("accounts") or data.get("items")
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
            return [data]
        return []

    def _create_options_account(self, account_type: str) -> dict:
        payload = self._rest_call(
            "POST",
            "/trading/v1/options/accounts",
            {
                "currency": "USD",
                "group": "row",
                "account_type": account_type,
            },
        )
        data = payload.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        if isinstance(data, dict):
            return data
        raise DerivServiceError("Deriv did not return an options account after create request.")

    @staticmethod
    def _account_id(account: object) -> str:
        if not isinstance(account, dict):
            return ""
        for key in ("account_id", "accountId", "loginid", "id"):
            value = account.get(key)
            if value:
                return str(value).strip()
        return ""

    @staticmethod
    def infer_account_type_from_id(account_id: object) -> str:
        raw = str(account_id or "").strip().upper()
        if not raw:
            return ""
        if raw.startswith(("DOT", "VRTC", "VRW")):
            return "demo"
        if raw.startswith(("ROT", "CR", "MF", "MLT")):
            return "real"
        return ""

    @classmethod
    def _account_type(cls, account: object) -> str:
        if not isinstance(account, dict):
            return ""
        for key in ("account_type", "accountType", "type"):
            value = account.get(key)
            if value:
                normalized = str(value).strip().lower()
                if normalized in {"demo", "virtual"}:
                    return "demo"
                if normalized in {"real", "live"}:
                    return "real"
                return normalized
        if "is_virtual" in account and account.get("is_virtual") is not None:
            return "demo" if bool(account.get("is_virtual")) else "real"
        return cls.infer_account_type_from_id(cls._account_id(account))

    def _select_options_account(self) -> dict:
        accounts = self._options_accounts()
        if self.options_account_mode in {"demo", "real"}:
            matching_mode = [
                account for account in accounts if self._account_type(account) == self.options_account_mode
            ]
            if self.options_account_id:
                for account in matching_mode:
                    if self._account_id(account) == self.options_account_id:
                        return account
                # If the saved options_account_id belongs to a different account mode
                # (for example, a stale DOT demo ID after switching to real mode),
                # never silently connect to the wrong-mode account.
            if matching_mode:
                return matching_mode[0]
            if not accounts:
                return self._create_options_account(self.options_account_mode)
            available = ", ".join(
                f"{self._account_id(acc) or 'unknown'} ({self._account_type(acc) or 'unknown'})"
                for acc in accounts
            )
            raise DerivServiceError(
                f"No Deriv '{self.options_account_mode}' options account is available for this token "
                f"(available: {available})."
            )

        if self.options_account_id:
            for account in accounts:
                if self._account_id(account) == self.options_account_id:
                    return account
        if accounts:
            return accounts[0]
        raise DerivServiceError("No Deriv options account is available for this token.")

    def _otp_ws_url(self, account_id: str) -> str:
        payload = self._rest_call("POST", f"/trading/v1/options/accounts/{account_id}/otp")
        data = payload.get("data")
        ws_url = data.get("url") if isinstance(data, dict) else None
        if not ws_url:
            raise DerivServiceError("Deriv OTP response did not include a WebSocket URL.")
        return str(ws_url)

    def _ws_url_with_app_id(self) -> str:
        parsed = urlparse(self.ws_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["app_id"] = self.app_id
        return urlunparse(parsed._replace(query=urlencode(query)))

    def connect(self) -> dict:
        self.close()
        self._ensure_credentials()
        try:
            if self.token_mode == "pat":
                account = self._select_options_account()
                account_id = self._account_id(account)
                if not account_id:
                    raise DerivServiceError("Unable to resolve a Deriv options account ID.")
                ws_url = self._otp_ws_url(account_id)
                self.ws = create_connection(ws_url, timeout=15)
                self.ws.settimeout(15)
                self._otp_auth = True
                self._selected_account = account
            else:
                self.ws = create_connection(self._ws_url_with_app_id(), timeout=15)
                self.ws.settimeout(15)
                authorize = self.send({"authorize": self.token})
                if "error" in authorize:
                    raise DerivServiceError(authorize["error"].get("message", "Authorization failed."))
                self._selected_account = authorize.get("authorize") or {}
                self._otp_auth = False
            return self.account_snapshot(update_source="connect")
        except Exception as exc:
            self.close()
            if isinstance(exc, DerivServiceError):
                raise
            raise DerivServiceError(f"Deriv connection failed: {exc}") from exc

    def _next_req_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def send(
        self,
        payload: dict,
        *,
        on_event: Optional[Callable[[dict], None]] = None,
    ) -> dict:
        if self.ws is None:
            raise DerivServiceError("Deriv WebSocket is not connected.")
        wire_payload = dict(payload)
        req_id = wire_payload.get("req_id")
        if req_id is None:
            req_id = self._next_req_id()
            wire_payload["req_id"] = req_id
        try:
            self.ws.send(json.dumps(wire_payload))
            while True:
                raw = self.ws.recv()
                data = json.loads(raw) if raw else {}
                if not isinstance(data, dict):
                    return {}
                resp_req_id = data.get("req_id")
                if resp_req_id is not None and resp_req_id != req_id:
                    if data.get("msg_type") == "balance" and isinstance(data.get("balance"), dict):
                        self._last_balance_info = data["balance"]
                    if on_event is not None:
                        on_event(data)
                    continue
                break
        except Exception as exc:
            raise DerivServiceError(f"Deriv WebSocket request failed: {exc}") from exc
        if isinstance(data, dict) and "error" in data:
            raise DerivServiceError(data["error"].get("message", "Deriv returned an error."))
        return data if isinstance(data, dict) else {}

    def get_balance(self) -> dict:
        response = self.send({"balance": 1})
        balance = response.get("balance")
        if not isinstance(balance, dict):
            return {}
        self._last_balance_info = balance
        return balance

    def subscribe_balance(self) -> dict:
        response = self.send({"balance": 1, "subscribe": 1})
        balance = response.get("balance") if isinstance(response.get("balance"), dict) else {}
        subscription = response.get("subscription") if isinstance(response.get("subscription"), dict) else {}
        sub_id = str(subscription.get("id") or balance.get("id") or "").strip() or None
        if balance:
            self._last_balance_info = balance
        return {
            **balance,
            "subscription_id": sub_id,
        }

    def forget(self, subscription_id: str) -> dict:
        if not subscription_id or self.ws is None:
            return {}
        return self.send({"forget": str(subscription_id)})

    def get_statement(self, limit: int = 20) -> list[dict]:
        response = self.send({"statement": 1, "description": 1, "limit": int(limit)})
        statement = response.get("statement") or {}
        rows = statement.get("transactions")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def get_profit_table(self, limit: int = 20) -> list[dict]:
        response = self.send({"profit_table": 1, "description": 1, "limit": int(limit)})
        table = response.get("profit_table") or {}
        rows = table.get("transactions")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def get_active_symbols(self) -> list[dict]:
        # The current Deriv API removed the legacy product_type filter.
        return self.send({"active_symbols": "brief"}).get("active_symbols") or []

    def account_snapshot(
        self,
        *,
        balance_info: Optional[dict] = None,
        require_live_balance: bool = False,
        update_source: str = "refresh",
    ) -> dict:
        account = self._selected_account or {}
        live_balance_info: dict = {}
        balance_error = ""
        balance_synced = False
        if isinstance(balance_info, dict) and "balance" in balance_info:
            live_balance_info = balance_info
            balance_synced = _safe_float(live_balance_info.get("balance")) is not None
        else:
            try:
                live_balance_info = self.get_balance()
                balance_synced = _safe_float(live_balance_info.get("balance")) is not None
            except Exception as exc:
                balance_error = str(exc)
                if require_live_balance:
                    if isinstance(exc, DerivServiceError):
                        raise
                    raise DerivServiceError(f"Failed to retrieve Deriv balance: {exc}") from exc

        loginid = str(
            live_balance_info.get("loginid")
            or account.get("loginid")
            or self._account_id(account)
            or ""
        ).strip()
        options_account_id = self._account_id(account) or loginid
        account_type = (
            self._account_type(account)
            or self.infer_account_type_from_id(loginid)
            or ("options" if self._otp_auth else "legacy")
        )
        balance_value = _safe_float(live_balance_info.get("balance"))
        if balance_value is None and not balance_error:
            balance_value = _safe_float(account.get("balance"))
        currency = str(live_balance_info.get("currency") or account.get("currency") or "USD").strip().upper()
        configured_mode = self.options_account_mode if self.options_account_mode in {"demo", "real"} else ""
        mode_mismatch = bool(
            configured_mode and account_type in {"demo", "real"} and account_type != configured_mode
        )
        synced_at = _utc_now_iso() if balance_synced else None
        return {
            "token_mode": self.token_mode,
            "app_id": self.app_id,
            "account_id": loginid,
            "loginid": loginid,
            "balance": balance_value,
            "currency": currency,
            "account_type": account_type,
            "configured_mode": configured_mode or account_type,
            "account_mode_mismatch": mode_mismatch,
            "options_account_id": options_account_id,
            "connected": True,
            "balance_synced": balance_synced,
            "balance_synced_at": synced_at,
            "balance_error": balance_error,
            "update_source": update_source,
        }

    def refresh_account_snapshot(self, *, update_source: str = "refresh") -> dict:
        return self.account_snapshot(require_live_balance=True, update_source=update_source)

    def propose(
        self,
        *,
        symbol: str,
        contract_type: str,
        amount: float,
        duration: Optional[int] = None,
        duration_unit: Optional[str] = None,
        currency: str,
        basis: str = "stake",
        multiplier: Optional[int] = None,
    ) -> dict:
        ctype = str(contract_type).strip().upper()
        payload: dict = {
            "proposal": 1,
            "amount": float(amount),
            "basis": basis,
            "contract_type": ctype,
            "currency": str(currency).strip().upper(),
            "underlying_symbol": str(symbol).strip(),
        }
        if ctype in {"MULTUP", "MULTDOWN"}:
            payload["multiplier"] = int(multiplier or 100)
        else:
            payload["duration"] = int(duration or 5)
            payload["duration_unit"] = str(duration_unit or "t").strip().lower()
        try:
            return self.send(payload).get("proposal") or {}
        except DerivServiceError as exc:
            if "underlying_symbol" not in str(exc):
                raise
            fallback = dict(payload)
            fallback["symbol"] = fallback.pop("underlying_symbol")
            return self.send(fallback).get("proposal") or {}

    def buy(self, proposal_id: str, price: float) -> dict:
        return self.send({"buy": proposal_id, "price": float(price)}).get("buy") or {}

    def sell_contract(self, contract_id: int | str, price: float = 0.0) -> dict:
        return self.send({"sell": int(contract_id), "price": float(price)}).get("sell") or {}

    def get_open_contracts(self) -> list[dict]:
        portfolio = self.send({"portfolio": 1}).get("portfolio") or {}
        rows = portfolio.get("contracts")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _deriv_settings_signature(settings: dict) -> tuple:
    return (
        str(settings.get("broker_provider") or "deriv").strip().lower(),
        str(settings.get("deriv_app_id") or "").strip(),
        str(settings.get("deriv_token") or "").strip(),
        str(settings.get("deriv_api_url") or DEFAULT_REST_URL).strip(),
        str(settings.get("deriv_ws_url") or DEFAULT_WS_URL).strip(),
        str(settings.get("deriv_options_account_mode") or "demo").strip().lower() or "demo",
        str(settings.get("deriv_options_account_id") or "").strip(),
    )


class DerivBalanceSyncService:
    """Maintains a live Deriv WebSocket balance subscription and authoritative balance cache.

    Real account balances are exclusively sourced from Deriv API responses (balance
    subscriptions and explicit balance calls) and are never calculated from journal P&L.
    """

    def __init__(
        self,
        *,
        client_factory: Callable[..., DerivCommunityClient] = DerivCommunityClient,
        on_update: Optional[Callable[[dict], None]] = None,
        ping_interval: float = 25.0,
        recv_timeout: float = 5.0,
    ) -> None:
        self._client_factory = client_factory
        self._on_update = on_update
        self._ping_interval = float(ping_interval)
        self._recv_timeout = float(recv_timeout)
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._active_client: Optional[DerivCommunityClient] = None
        self._signature: Optional[tuple] = None
        self._settings: dict = {}
        self._state: dict = self._empty_state()

    @staticmethod
    def _empty_state(configured_mode: str = "demo", api_status: str = "unconfigured") -> dict:
        return {
            "broker": "deriv",
            "connected": False,
            "api_status": api_status,
            "subscription_active": False,
            "subscription_id": None,
            "sync_status": "unconfigured" if api_status == "unconfigured" else "syncing",
            "account_id": "",
            "loginid": "",
            "account_type": "",
            "configured_mode": configured_mode,
            "account_mode_mismatch": False,
            "balance": None,
            "currency": None,
            "token_mode": None,
            "last_synced_at": None,
            "last_update_source": None,
            "last_error": "",
        }

    def _build_client(self, settings: dict) -> DerivCommunityClient:
        return self._client_factory(
            app_id=str(settings.get("deriv_app_id") or "").strip(),
            token=str(settings.get("deriv_token") or "").strip(),
            rest_url=str(settings.get("deriv_api_url") or DEFAULT_REST_URL).strip() or DEFAULT_REST_URL,
            ws_url=str(settings.get("deriv_ws_url") or DEFAULT_WS_URL).strip() or DEFAULT_WS_URL,
            options_account_mode=str(settings.get("deriv_options_account_mode") or "demo").strip().lower() or "demo",
            options_account_id=str(settings.get("deriv_options_account_id") or "").strip(),
        )

    @staticmethod
    def is_configured(settings: dict) -> bool:
        return (
            str(settings.get("broker_provider") or "deriv").strip().lower() == "deriv"
            and bool(str(settings.get("deriv_app_id") or "").strip())
            and bool(str(settings.get("deriv_token") or "").strip())
        )

    def ensure_started(self, settings: dict) -> None:
        signature = _deriv_settings_signature(settings)
        configured = self.is_configured(settings)
        with self._lock:
            if not configured:
                self._stop_locked()
                self._signature = None
                self._settings = {}
                self._state = self._empty_state(
                    configured_mode=str(settings.get("deriv_options_account_mode") or "demo").strip().lower() or "demo",
                    api_status="unconfigured",
                )
                return
            if self._signature == signature and self._thread is not None and self._thread.is_alive():
                return
            self._stop_locked()
            self._signature = signature
            self._settings = dict(settings)
            configured_mode = str(settings.get("deriv_options_account_mode") or "demo").strip().lower() or "demo"
            token = str(settings.get("deriv_token") or "").strip()
            self._state = {
                **self._empty_state(configured_mode=configured_mode, api_status="connecting"),
                "token_mode": "pat" if token.startswith("pat_") else "legacy",
            }
            self._stop_event = threading.Event()
            stop_event = self._stop_event
            self._thread = threading.Thread(
                target=self._run_loop,
                args=(dict(settings), signature, stop_event),
                name="deriv-balance-sync",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        self._stop_event.set()
        client = self._active_client
        self._active_client = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def reset(self) -> None:
        with self._lock:
            self._stop_locked()
            self._signature = None
            self._settings = {}
            self._state = self._empty_state()

    def record_snapshot(self, snapshot: dict, *, source: str = "refresh") -> dict:
        """Record an authoritative Deriv API snapshot (e.g. after purchase, settlement, or test)."""
        if not isinstance(snapshot, dict):
            return self.get_status(self._settings)
        balance_value = _safe_float(snapshot.get("balance"))
        account_id = str(
            snapshot.get("account_id")
            or snapshot.get("loginid")
            or snapshot.get("options_account_id")
            or ""
        ).strip()
        account_type = str(snapshot.get("account_type") or "").strip().lower()
        if not account_type and account_id:
            account_type = DerivCommunityClient.infer_account_type_from_id(account_id)
        configured_mode = str(
            snapshot.get("configured_mode")
            or self._state.get("configured_mode")
            or account_type
            or "demo"
        ).strip().lower()
        mismatch = bool(
            configured_mode in {"demo", "real"}
            and account_type in {"demo", "real"}
            and account_type != configured_mode
        )
        synced_at = snapshot.get("balance_synced_at") or (_utc_now_iso() if balance_value is not None else None)
        with self._lock:
            self._state.update(
                {
                    "account_id": account_id or self._state.get("account_id", ""),
                    "loginid": account_id or self._state.get("loginid", ""),
                    "account_type": account_type or self._state.get("account_type", ""),
                    "configured_mode": configured_mode,
                    "account_mode_mismatch": mismatch,
                    "token_mode": snapshot.get("token_mode") or self._state.get("token_mode"),
                }
            )
            if balance_value is not None:
                self._state.update(
                    {
                        "balance": balance_value,
                        "currency": str(snapshot.get("currency") or self._state.get("currency") or "USD").strip().upper(),
                        "last_synced_at": synced_at,
                        "last_update_source": source,
                        "sync_status": "synced",
                        "last_error": "",
                    }
                )
            elif snapshot.get("balance_error"):
                self._state["last_error"] = str(snapshot["balance_error"])
                self._state["sync_status"] = "error"
            if snapshot.get("connected"):
                self._state["connected"] = True
                if self._state.get("api_status") in {"unconfigured", "disconnected", "error"}:
                    self._state["api_status"] = "connected"
            state_copy = dict(self._state)
        self._emit_update(state_copy)
        return state_copy

    def refresh_now(self, settings: dict, *, source: str = "refresh") -> dict:
        """Fetch the current live balance directly from Deriv API and update sync state."""
        if not self.is_configured(settings):
            raise DerivServiceError("Add a Deriv app ID and API token before refreshing the balance.")
        client = self._build_client(settings)
        try:
            snapshot = client.connect()
            if not snapshot.get("balance_synced"):
                snapshot = client.refresh_account_snapshot(update_source=source)
            else:
                snapshot["update_source"] = source
            self.record_snapshot(snapshot, source=source)
            return snapshot
        except DerivServiceError as exc:
            with self._lock:
                self._state.update(
                    {
                        "sync_status": "error",
                        "last_error": str(exc),
                    }
                )
            raise
        finally:
            client.close()

    def get_status(self, settings: Optional[dict] = None, fallback_broker_status: Optional[dict] = None) -> dict:
        settings = settings or {}
        configured_mode = str(settings.get("deriv_options_account_mode") or "demo").strip().lower() or "demo"
        configured = self.is_configured(settings)
        token = str(settings.get("deriv_token") or "").strip()
        token_mode = ("pat" if token.startswith("pat_") else "legacy") if token else None
        with self._lock:
            state = dict(self._state)

        if not configured:
            return {
                **self._empty_state(configured_mode=configured_mode, api_status="unconfigured"),
                "broker": str(settings.get("broker_provider") or "deriv").strip().lower(),
                "token_mode": token_mode,
            }

        # If the live service hasn't synced yet, we may display the last verified broker status
        # strictly as a stale/cached API reading (never fabricated from journal P&L), while
        # flagging any mismatch with the currently configured account mode.
        if not state.get("account_id") and isinstance(fallback_broker_status, dict):
            fb_account = str(
                fallback_broker_status.get("account_id")
                or fallback_broker_status.get("loginid")
                or fallback_broker_status.get("options_account_id")
                or ""
            ).strip()
            fb_type = str(fallback_broker_status.get("account_type") or "").strip().lower()
            if not fb_type and fb_account:
                fb_type = DerivCommunityClient.infer_account_type_from_id(fb_account)
            fb_balance = _safe_float(fallback_broker_status.get("balance"))
            if fb_account:
                state["account_id"] = fb_account
                state["loginid"] = fb_account
                state["account_type"] = fb_type
                state["currency"] = fallback_broker_status.get("currency") or state.get("currency")
                if fb_balance is not None and state.get("balance") is None:
                    state["balance"] = fb_balance
                    state["last_synced_at"] = (
                        fallback_broker_status.get("balance_synced_at")
                        or fallback_broker_status.get("checked_at")
                    )
                    state["last_update_source"] = fallback_broker_status.get("update_source") or "broker_test"
                    if state.get("sync_status") in {"unconfigured", "syncing"}:
                        state["sync_status"] = "stale"

        state["configured_mode"] = configured_mode
        state["token_mode"] = state.get("token_mode") or token_mode
        acct_type = str(state.get("account_type") or "").strip().lower()
        state["account_mode_mismatch"] = bool(
            configured_mode in {"demo", "real"}
            and acct_type in {"demo", "real"}
            and acct_type != configured_mode
        )
        return state

    def _emit_update(self, snapshot: dict) -> None:
        if self._on_update is not None:
            try:
                self._on_update(snapshot)
            except Exception:
                pass

    def _run_loop(self, settings: dict, signature: tuple, stop_event: threading.Event) -> None:
        backoff = 2.0
        while not stop_event.is_set():
            client = self._build_client(settings)
            subscription_id: Optional[str] = None
            try:
                with self._lock:
                    if self._signature != signature or stop_event.is_set():
                        return
                    self._active_client = client
                    self._state["api_status"] = "connecting" if not self._state.get("last_synced_at") else "reconnecting"

                snapshot = client.connect()
                if stop_event.is_set():
                    return
                sub_info = client.subscribe_balance()
                subscription_id = sub_info.get("subscription_id")
                balance_value = _safe_float(sub_info.get("balance"))
                if balance_value is None:
                    balance_value = _safe_float(snapshot.get("balance"))
                account_id = str(
                    sub_info.get("loginid")
                    or snapshot.get("account_id")
                    or snapshot.get("loginid")
                    or ""
                ).strip()
                account_type = str(
                    snapshot.get("account_type")
                    or DerivCommunityClient.infer_account_type_from_id(account_id)
                    or ""
                ).strip().lower()
                currency = str(sub_info.get("currency") or snapshot.get("currency") or "USD").strip().upper()
                configured_mode = str(settings.get("deriv_options_account_mode") or "demo").strip().lower() or "demo"

                with self._lock:
                    if self._signature != signature or stop_event.is_set():
                        return
                    self._state.update(
                        {
                            "connected": True,
                            "api_status": "connected",
                            "subscription_active": bool(subscription_id),
                            "subscription_id": subscription_id,
                            "sync_status": "synced" if balance_value is not None else "stale",
                            "account_id": account_id,
                            "loginid": account_id,
                            "account_type": account_type,
                            "configured_mode": configured_mode,
                            "account_mode_mismatch": bool(
                                configured_mode in {"demo", "real"}
                                and account_type in {"demo", "real"}
                                and account_type != configured_mode
                            ),
                            "balance": balance_value,
                            "currency": currency,
                            "token_mode": client.token_mode,
                            "last_synced_at": _utc_now_iso() if balance_value is not None else self._state.get("last_synced_at"),
                            "last_update_source": "subscription",
                            "last_error": "",
                        }
                    )
                    state_copy = dict(self._state)
                self._emit_update(state_copy)
                backoff = 2.0

                if client.ws is not None:
                    client.ws.settimeout(self._recv_timeout)
                last_ping = time.monotonic()

                while not stop_event.is_set():
                    if client.ws is None:
                        break
                    try:
                        raw = client.ws.recv()
                    except WebSocketTimeoutException:
                        now = time.monotonic()
                        if now - last_ping >= self._ping_interval:
                            client.ws.send(json.dumps({"ping": 1}))
                            last_ping = now
                        continue
                    if not raw:
                        break
                    try:
                        message = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(message, dict):
                        continue
                    if "error" in message:
                        raise DerivServiceError(message["error"].get("message", "Deriv balance stream error."))
                    if message.get("msg_type") == "balance" and isinstance(message.get("balance"), dict):
                        bal = message["balance"]
                        new_balance = _safe_float(bal.get("balance"))
                        new_currency = str(bal.get("currency") or currency or "USD").strip().upper()
                        new_loginid = str(bal.get("loginid") or account_id).strip()
                        with self._lock:
                            if self._signature != signature or stop_event.is_set():
                                return
                            if new_balance is not None:
                                self._state["balance"] = new_balance
                                self._state["currency"] = new_currency
                                self._state["last_synced_at"] = _utc_now_iso()
                                self._state["last_update_source"] = "subscription"
                                self._state["sync_status"] = "synced"
                            if new_loginid:
                                self._state["account_id"] = new_loginid
                                self._state["loginid"] = new_loginid
                            self._state["last_error"] = ""
                            state_copy = dict(self._state)
                        self._emit_update(state_copy)
            except Exception as exc:
                if stop_event.is_set():
                    return
                with self._lock:
                    if self._signature != signature:
                        return
                    had_balance = self._state.get("balance") is not None
                    self._state.update(
                        {
                            "connected": False,
                            "api_status": "reconnecting" if had_balance else "error",
                            "subscription_active": False,
                            "subscription_id": None,
                            "sync_status": "stale" if had_balance else "error",
                            "last_error": str(exc),
                        }
                    )
            finally:
                if subscription_id and client.ws is not None:
                    try:
                        client.ws.settimeout(2)
                        client.forget(subscription_id)
                    except Exception:
                        pass
                client.close()
                with self._lock:
                    if self._active_client is client:
                        self._active_client = None

            if stop_event.wait(backoff):
                return
            backoff = min(backoff * 2.0, 30.0)
