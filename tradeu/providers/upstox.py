from __future__ import annotations

import threading
from collections import deque
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import quote as url_quote

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from tradeu.live import payload_to_quote
from tradeu.models import MarketQuote
from tradeu.providers.base import DataProvider, ProviderError

UPSTOX_API = "https://api.upstox.com"


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class UpstoxLiveStream:
    """Threaded wrapper around the official Upstox MarketDataStreamerV3."""

    def __init__(self, access_token: str, instrument_key: str, mode: str = "full"):
        self.access_token = access_token
        self.instrument_key = instrument_key
        self.mode = mode
        self._messages: deque[dict] = deque(maxlen=25)
        self._status = "stopped"
        self._streamer = None
        self._thread: threading.Thread | None = None

    @property
    def status(self) -> str:
        return self._status

    def _on_message(self, message: dict) -> None:
        self._messages.append(message)

    def _on_error(self, error: Any) -> None:
        self._status = f"error: {error}"

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        try:
            import upstox_client
        except ImportError as exc:
            raise ProviderError("Install upstox-python-sdk to use live streaming.") from exc

        configuration = upstox_client.Configuration()
        configuration.access_token = self.access_token
        api_client = upstox_client.ApiClient(configuration)
        self._streamer = upstox_client.MarketDataStreamerV3(api_client, [self.instrument_key], self.mode)
        self._streamer.on("open", lambda: setattr(self, "_status", "connected"))
        self._streamer.on("message", self._on_message)
        self._streamer.on("error", self._on_error)
        self._streamer.on("reconnecting", lambda *_: setattr(self, "_status", "reconnecting"))
        self._streamer.auto_reconnect(True, 5, 10)
        self._status = "connecting"
        self._thread = threading.Thread(target=self._streamer.connect, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._streamer is not None:
            try:
                self._streamer.disconnect()
            except Exception:
                pass
        self._status = "stopped"

    def latest(self) -> dict | None:
        return self._messages[-1] if self._messages else None

    def latest_quote(self) -> MarketQuote | None:
        payload = self.latest()
        return payload_to_quote(payload) if payload else None


class UpstoxProvider(DataProvider):
    """Read-only Upstox V2/V3 market-data adapter."""

    def __init__(self, access_token: str, timeout: int = 20):
        if not access_token.strip():
            raise ProviderError("An Upstox access token is required.")
        self.access_token = access_token.strip()
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Authorization": f"Bearer {self.access_token}",
            }
        )
        retry = Retry(
            total=3,
            connect=3,
            read=2,
            backoff_factor=0.45,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def _get(self, url: str, **kwargs) -> dict:
        try:
            response = self.session.get(url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            detail = getattr(exc.response, "text", "")[:400]
            raise ProviderError(f"Upstox request failed: {exc}. {detail}") from exc
        if payload.get("status") == "error":
            raise ProviderError(f"Upstox error: {payload.get('errors') or payload}")
        return payload

    def historical(
        self,
        instrument_id: str,
        from_date: date,
        to_date: date,
        interval: str,
        **kwargs,
    ) -> pd.DataFrame:
        unit, step = _upstox_interval(interval)
        key = url_quote(instrument_id, safe="")
        url = f"{UPSTOX_API}/v3/historical-candle/{key}/{unit}/{step}/{to_date.isoformat()}/{from_date.isoformat()}"
        payload = self._get(url)
        candles = payload.get("data", {}).get("candles", [])
        if not candles:
            raise ProviderError("Upstox returned no candles for this instrument/date range.")
        frame = pd.DataFrame(
            candles,
            columns=["timestamp", "open", "high", "low", "close", "volume", "oi"][: len(candles[0])],
        )
        return _normalize_frame(frame)

    def quote(self, instrument_id: str, **kwargs) -> MarketQuote:
        payload = self._get(
            f"{UPSTOX_API}/v2/market-quote/quotes",
            params={"instrument_key": instrument_id},
        )
        data = payload.get("data") or {}
        row = next(iter(data.values()), {})
        if not row:
            raise ProviderError("Upstox returned an empty quote.")
        depth = row.get("depth") or {}
        buys = depth.get("buy") or []
        sells = depth.get("sell") or []
        bid = _number((buys[0] if buys else {}).get("price"))
        ask = _number((sells[0] if sells else {}).get("price"))
        ltp = _number(row.get("last_price") or row.get("ltp"))
        if ltp is None:
            raise ProviderError("Upstox quote did not contain a last price.")
        timestamp = row.get("last_trade_time")
        parsed_time = None
        if timestamp:
            try:
                parsed_time = datetime.fromtimestamp(float(timestamp) / 1000, tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                pass
        return MarketQuote(
            ltp=ltp,
            bid=bid,
            ask=ask,
            volume=_number(row.get("volume")),
            open_interest=_number(row.get("oi")),
            timestamp=parsed_time or datetime.now(timezone.utc),
            raw=row,
        )

    def stream(self, instrument_id: str, **kwargs) -> UpstoxLiveStream:
        return UpstoxLiveStream(self.access_token, instrument_id, kwargs.get("mode", "full"))

    def option_chain(self, underlying_instrument_key: str, expiry: date) -> dict:
        return self._get(
            f"{UPSTOX_API}/v2/option/chain",
            params={"instrument_key": underlying_instrument_key, "expiry_date": expiry.isoformat()},
        )

    def option_greeks(self, instrument_keys: list[str]) -> dict:
        if not instrument_keys or len(instrument_keys) > 50:
            raise ProviderError("Upstox option Greeks accepts 1 to 50 instrument keys.")
        return self._get(
            f"{UPSTOX_API}/v3/market-quote/option-greek",
            params={"instrument_key": ",".join(instrument_keys)},
        )


def _upstox_interval(interval: str) -> tuple[str, int]:
    value = interval.strip().lower()
    if value in {"day", "1day", "daily", "d"}:
        return "days", 1
    if value in {"week", "1week", "weekly", "w"}:
        return "weeks", 1
    digits = "".join(char for char in value if char.isdigit())
    minutes = int(digits or "1")
    allowed = {1, 2, 3, 5, 10, 15, 30}
    if minutes not in allowed:
        raise ProviderError(f"Upstox minute interval must be one of {sorted(allowed)}.")
    return "minutes", minutes


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
    for column in ["open", "high", "low", "close", "volume", "oi"]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return (
        frame.dropna(subset=["timestamp", "open", "high", "low", "close"])
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
