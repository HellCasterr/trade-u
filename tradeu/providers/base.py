from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Protocol

import pandas as pd

from tradeu.models import MarketQuote


class ProviderError(RuntimeError):
    """A user-actionable data-provider failure."""


class LiveStream(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def latest(self) -> dict | None: ...
    @property
    def status(self) -> str: ...


class DataProvider(ABC):
    @abstractmethod
    def historical(
        self,
        instrument_id: str,
        from_date: date,
        to_date: date,
        interval: str,
        **kwargs,
    ) -> pd.DataFrame:
        """Return timestamp/open/high/low/close/volume and optional oi columns."""

    @abstractmethod
    def quote(self, instrument_id: str, **kwargs) -> MarketQuote:
        """Return a normalized live market snapshot."""

    @abstractmethod
    def stream(self, instrument_id: str, **kwargs) -> LiveStream:
        """Build (but do not automatically start) a live WebSocket stream."""
