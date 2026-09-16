from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

import numpy as np
import pandas as pd

from tradeu.models import MarketQuote
from tradeu.providers.base import DataProvider


@dataclass
class _DemoStream:
    _running: bool = False
    _tick: int = 0
    _latest: dict | None = None

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    @property
    def status(self) -> str:
        return "connected (simulated)" if self._running else "stopped"

    def latest(self) -> dict | None:
        if not self._running:
            return None
        self._tick += 1
        self._latest = {
            "type": "demo_tick",
            "ltp": round(22450 + np.sin(self._tick / 3) * 8, 2),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return self._latest

    def latest_quote(self) -> MarketQuote | None:
        payload = self._latest or self.latest()
        if payload is None:
            return None
        return MarketQuote(
            ltp=float(payload["ltp"]),
            bid=float(payload["ltp"]) - 0.5,
            ask=float(payload["ltp"]) + 0.5,
            timestamp=datetime.now(timezone.utc),
            raw=payload,
        )


class DemoProvider(DataProvider):
    """Deterministic data source for evaluation without broker credentials."""

    def historical(
        self,
        instrument_id: str,
        from_date: date,
        to_date: date,
        interval: str,
        **kwargs,
    ) -> pd.DataFrame:
        seed = sum(ord(char) for char in instrument_id.upper()) + int(from_date.strftime("%j"))
        rng = np.random.default_rng(seed)
        intraday = interval.lower() not in {"day", "1day", "daily", "d"}
        rows = 900 if intraday else max(260, min(700, (to_date - from_date).days))
        if intraday:
            digits = "".join(character for character in interval if character.isdigit())
            minutes = max(1, int(digits or "15"))
            bars_per_day = max(1, int(375 / minutes))
            days = int(np.ceil(rows / bars_per_day)) + 2
            sessions = []
            for session_date in pd.bdate_range(end=to_date, periods=days):
                start = pd.Timestamp.combine(session_date.date(), time(9, 15)).tz_localize("Asia/Kolkata")
                finish = pd.Timestamp.combine(session_date.date(), time(15, 30)).tz_localize("Asia/Kolkata")
                sessions.extend(
                    pd.date_range(start=start, end=finish - timedelta(minutes=minutes), freq=f"{minutes}min")
                )
            timestamps = pd.DatetimeIndex(sessions[-rows:])
        else:
            timestamps = pd.date_range(end=pd.Timestamp(to_date), periods=rows, freq="B", tz="Asia/Kolkata")

        drift = 0.00022
        noise = rng.normal(drift, 0.0048 if intraday else 0.012, rows)
        cycle = np.sin(np.linspace(0, 11 * np.pi, rows)) * 0.0017
        close = 22000 * np.exp(np.cumsum(noise + cycle))
        open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.0015, rows))
        width = np.abs(rng.normal(0.004, 0.0015, rows))
        high = np.maximum(open_, close) * (1 + width)
        low = np.minimum(open_, close) * (1 - width)
        volume = rng.lognormal(mean=12.4, sigma=0.55, size=rows).astype(int)
        oi = np.maximum(0, np.cumsum(rng.integers(-500, 700, rows)) + 800_000)
        return pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "oi": oi,
            }
        )

    def quote(self, instrument_id: str, **kwargs) -> MarketQuote:
        ltp = float(kwargs.get("last_price", 22450.0))
        return MarketQuote(
            ltp=ltp,
            bid=round(ltp - 0.5, 2),
            ask=round(ltp + 0.5, 2),
            volume=1_250_000,
            open_interest=8_500_000,
            timestamp=datetime.now(timezone.utc),
        )

    def stream(self, instrument_id: str, **kwargs) -> _DemoStream:
        return _DemoStream()
