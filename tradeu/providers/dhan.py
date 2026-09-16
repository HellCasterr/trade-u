from __future__ import annotations

import time
from collections import deque
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from tradeu.live import payload_to_quote
from tradeu.models import MarketQuote
from tradeu.providers.base import DataProvider, ProviderError


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class DhanLiveStream:
    """Threaded wrapper around DhanHQ-py's official MarketFeed V2."""

    EXCHANGE_CODES = {
        "IDX_I": 0,
        "NSE_EQ": 1,
        "NSE_FNO": 2,
        "NSE_CURRENCY": 3,
        "BSE_EQ": 4,
        "MCX_COMM": 5,
        "BSE_CURRENCY": 7,
        "BSE_FNO": 8,
    }

    def __init__(self, client_id: str, access_token: str, security_id: str, exchange_segment: str, mode: str = "quote"):
        self.client_id = client_id
        self.access_token = access_token
        self.security_id = str(security_id)
        self.exchange_segment = exchange_segment
        self.mode = mode
        self._messages: deque[dict] = deque(maxlen=25)
        self._status = "stopped"
        self._feed = None
        self._thread = None

    @property
    def status(self) -> str:
        return self._status

    def _on_message(self, _feed, message: dict) -> None:
        self._messages.append(message)

    def _on_error(self, _feed, error: Any) -> None:
        self._status = f"error: {error}"

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        try:
            from dhanhq import DhanContext, MarketFeed
        except ImportError as exc:
            raise ProviderError("Install dhanhq to use Dhan live streaming.") from exc
        exchange = self.EXCHANGE_CODES.get(self.exchange_segment)
        if exchange is None:
            raise ProviderError(f"Unsupported Dhan stream segment: {self.exchange_segment}")
        modes = {"ticker": MarketFeed.Ticker, "quote": MarketFeed.Quote, "full": MarketFeed.Full}
        mode = modes.get(self.mode, MarketFeed.Quote)
        context = DhanContext(self.client_id, self.access_token)
        self._feed = MarketFeed(
            context,
            [(exchange, self.security_id, mode)],
            version="v2",
            on_connect=lambda *_: setattr(self, "_status", "connected"),
            on_message=self._on_message,
            on_error=self._on_error,
        )
        self._status = "connecting"
        self._thread = self._feed.start()

    def stop(self) -> None:
        if self._feed is not None:
            try:
                self._feed.close_connection()
            except Exception:
                pass
        self._status = "stopped"

    def latest(self) -> dict | None:
        return self._messages[-1] if self._messages else None

    def latest_quote(self) -> MarketQuote | None:
        payload = self.latest()
        return payload_to_quote(payload) if payload else None


class DhanProvider(DataProvider):
    """Read-only adapter around the official DhanHQ Python SDK."""

    def __init__(self, client_id: str, access_token: str, retry_delays: tuple[float, ...] = (0.4, 1.0)):
        if not client_id.strip() or not access_token.strip():
            raise ProviderError("Dhan client ID and access token are required.")
        self.client_id = client_id.strip()
        self.access_token = access_token.strip()
        try:
            from dhanhq import DhanContext, dhanhq
        except ImportError as exc:
            raise ProviderError("Install dhanhq to use the Dhan provider.") from exc
        self._context = DhanContext(self.client_id, self.access_token)
        self._client = dhanhq(self._context)
        self.retry_delays = retry_delays

    @staticmethod
    def _validate(response: dict, operation: str) -> dict:
        if not isinstance(response, dict):
            raise ProviderError(f"Dhan {operation} returned an unexpected response.")
        if response.get("status") == "failure" or response.get("errorCode"):
            message = response.get("remarks") or response.get("errorMessage") or response
            raise ProviderError(f"Dhan {operation} failed: {message}")
        return response

    def _call(self, operation: str, function, *args, **kwargs) -> dict:
        last_error: Exception | None = None
        for attempt in range(len(self.retry_delays) + 1):
            try:
                response = function(*args, **kwargs)
                retryable = isinstance(response, dict) and str(response.get("errorCode", "")) in {"800", "805"}
                if not retryable or attempt == len(self.retry_delays):
                    return self._validate(response, operation)
            except ProviderError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt == len(self.retry_delays):
                    raise ProviderError(f"Dhan {operation} failed after retries: {exc}") from exc
            time.sleep(self.retry_delays[attempt])
        raise ProviderError(f"Dhan {operation} failed after retries: {last_error}")

    def historical(
        self,
        instrument_id: str,
        from_date: date,
        to_date: date,
        interval: str,
        **kwargs,
    ) -> pd.DataFrame:
        exchange_segment = kwargs.get("exchange_segment", "NSE_EQ")
        instrument_type = kwargs.get("instrument_type", "EQUITY")
        value = interval.strip().lower()
        if value in {"day", "1day", "daily", "d"}:
            response = self._call(
                "historical data",
                self._client.historical_daily_data,
                instrument_id,
                exchange_segment,
                instrument_type,
                from_date.isoformat(),
                to_date.isoformat(),
                expiry_code=int(kwargs.get("expiry_code", 0)),
                oi=bool(kwargs.get("oi", instrument_type not in {"EQUITY", "INDEX"})),
            )
        else:
            digits = "".join(char for char in value if char.isdigit())
            minutes = int(digits or "1")
            if minutes not in {1, 5, 15, 25, 60}:
                raise ProviderError("Dhan interval must be 1, 5, 15, 25, or 60 minutes.")
            response = self._call(
                "historical data",
                self._client.intraday_minute_data,
                instrument_id,
                exchange_segment,
                instrument_type,
                from_date.isoformat(),
                to_date.isoformat(),
                interval=minutes,
                oi=bool(kwargs.get("oi", instrument_type not in {"EQUITY", "INDEX"})),
            )
        payload = response
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        frame = pd.DataFrame(
            {
                "timestamp": data.get("timestamp", []),
                "open": data.get("open", []),
                "high": data.get("high", []),
                "low": data.get("low", []),
                "close": data.get("close", []),
                "volume": data.get("volume", []),
            }
        )
        if "open_interest" in data or "oi" in data:
            frame["oi"] = data.get("open_interest", data.get("oi"))
        if frame.empty:
            raise ProviderError("Dhan returned no candles for this instrument/date range.")
        frame["timestamp"] = _parse_dhan_timestamps(frame["timestamp"])
        for column in ["open", "high", "low", "close", "volume", "oi"]:
            if column in frame:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return (
            frame.dropna(subset=["timestamp", "open", "high", "low", "close"])
            .sort_values("timestamp")
            .reset_index(drop=True)
        )

    def quote(self, instrument_id: str, **kwargs) -> MarketQuote:
        exchange_segment = kwargs.get("exchange_segment", "NSE_EQ")
        payload = self._call("market quote", self._client.quote_data, {exchange_segment: [int(instrument_id)]})
        data = payload.get("data") or {}
        segment_data = data.get(exchange_segment, {})
        row = segment_data.get(str(instrument_id)) or segment_data.get(int(instrument_id)) or {}
        if not row:
            raise ProviderError("Dhan returned an empty quote. Check segment and security ID.")
        depth = row.get("depth") or {}
        buys = depth.get("buy") or []
        sells = depth.get("sell") or []
        bid = _number((buys[0] if buys else {}).get("price"))
        ask = _number((sells[0] if sells else {}).get("price"))
        ltp = _number(row.get("last_price") or row.get("ltp") or row.get("LTP"))
        if ltp is None:
            raise ProviderError("Dhan quote did not contain a last price.")
        return MarketQuote(
            ltp=ltp,
            bid=bid,
            ask=ask,
            volume=_number(row.get("volume")),
            open_interest=_number(row.get("oi") or row.get("open_interest")),
            timestamp=datetime.now(timezone.utc),
            raw=row,
        )

    def option_chain(self, underlying_security_id: int, segment: str, expiry: str) -> dict:
        return self._call("option chain", self._client.option_chain, underlying_security_id, segment, expiry)

    def expiry_list(self, underlying_security_id: int, segment: str) -> dict:
        return self._call("expiry list", self._client.expiry_list, underlying_security_id, segment)

    def stream(self, instrument_id: str, **kwargs) -> DhanLiveStream:
        return DhanLiveStream(
            self.client_id,
            self.access_token,
            instrument_id,
            kwargs.get("exchange_segment", "NSE_EQ"),
            kwargs.get("mode", "quote"),
        )


def _parse_dhan_timestamps(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().mean() > 0.8:
        return pd.to_datetime(numeric, unit="s", errors="coerce", utc=True)
    return pd.to_datetime(series, errors="coerce", utc=True)
