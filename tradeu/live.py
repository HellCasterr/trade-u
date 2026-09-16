from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from tradeu.models import MarketQuote


def _walk(value: Any) -> Iterable[dict]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _first_number(nodes: list[dict], names: tuple[str, ...]) -> float | None:
    for node in nodes:
        for name in names:
            value = _number(node.get(name))
            if value is not None:
                return value
    return None


def _timestamp(value: Any) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    numeric = _number(value)
    if numeric is not None:
        if numeric > 10_000_000_000:
            numeric /= 1000
        try:
            return datetime.fromtimestamp(numeric, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            pass
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def payload_to_quote(payload: dict) -> MarketQuote | None:
    """Normalize common Upstox/Dhan stream payloads without losing the raw packet."""
    if not isinstance(payload, dict):
        return None
    nodes = list(_walk(payload))
    ltp = _first_number(nodes, ("ltp", "LTP", "last_price", "lastPrice"))
    if ltp is None or ltp <= 0:
        return None
    bid = _first_number(nodes, ("bid", "bid_price", "bidPrice", "bidP"))
    ask = _first_number(nodes, ("ask", "ask_price", "askPrice", "askP"))
    volume = _first_number(nodes, ("volume", "vol", "vtt"))
    oi = _first_number(nodes, ("oi", "OI", "open_interest", "openInterest"))
    timestamp_value = None
    for node in nodes:
        for name in ("ltt", "LTT", "last_trade_time", "lastTradeTime", "timestamp"):
            if node.get(name) is not None:
                timestamp_value = node[name]
                break
        if timestamp_value is not None:
            break
    return MarketQuote(
        ltp=ltp,
        bid=bid,
        ask=ask,
        volume=volume,
        open_interest=oi,
        timestamp=_timestamp(timestamp_value),
        raw=payload,
    )
