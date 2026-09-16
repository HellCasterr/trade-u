from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

Direction = Literal["LONG", "SHORT", "NO TRADE"]
InstrumentKind = Literal["CASH", "FUTURE", "OPTION"]


@dataclass(slots=True)
class InstrumentSpec:
    symbol: str
    kind: InstrumentKind = "CASH"
    lot_size: int = 1
    tick_size: float = 0.05
    expiry: date | None = None
    strike: float | None = None
    option_type: Literal["CE", "PE"] | None = None
    underlying_price: float | None = None


@dataclass(slots=True)
class MarketQuote:
    ltp: float
    bid: float | None = None
    ask: float | None = None
    volume: float | None = None
    open_interest: float | None = None
    timestamp: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def spread_pct(self) -> float | None:
        if self.bid is None or self.ask is None or self.bid <= 0 or self.ask < self.bid:
            return None
        mid = (self.bid + self.ask) / 2
        return ((self.ask - self.bid) / mid) * 100 if mid else None


@dataclass(slots=True)
class AnalysisConfig:
    atr_period: int = 14
    fast_ema: int = 20
    slow_ema: int = 50
    pivot_window: int = 5
    zone_atr_width: float = 0.60
    entry_zone_atr: float = 1.20
    stop_atr_buffer: float = 0.35
    minimum_reward_risk: float = 1.30
    pattern_window: int = 20
    forward_horizon: int = 15
    analogue_count: int = 30
    minimum_analogues: int = 20
    maximum_spread_pct: float = 0.50
    risk_budget_inr: float = 5_000.0
    estimated_cost_pct: float = 0.08
    confidence_z: float = 1.96
    probability_edge_pct: float = 2.0
    require_live_quote: bool = False
    maximum_quote_age_seconds: int = 120
    maximum_missing_bar_ratio: float = 0.05
    maximum_candle_delay_bars: float = 3.0
    minimum_zone_strength: int = 2
    require_derivative_oi: bool = False
    align_intraday_analogues: bool = True


@dataclass(slots=True)
class PriceZone:
    level: float
    strength: int
    kind: Literal["support", "resistance"]
    distance_atr: float


@dataclass(slots=True)
class AnalogueStats:
    sample_size: int = 0
    target_first: int = 0
    stop_first: int = 0
    timeout: int = 0
    win_probability: float | None = None
    wilson_low: float | None = None
    wilson_high: float | None = None
    median_forward_return_pct: float | None = None


@dataclass(slots=True)
class TradeAnalysis:
    verdict: Direction
    entry: float
    stop: float | None
    target: float | None
    target_pct: float | None
    stop_loss_pct: float | None
    net_target_pct: float | None
    net_stop_loss_pct: float | None
    reward_risk: float | None
    atr: float
    trend: str
    support: PriceZone | None
    resistance: PriceZone | None
    reasons: list[str]
    warnings: list[str]
    gates: dict[str, bool]
    analogues: AnalogueStats
    risk_per_lot_inr: float | None = None
    reward_per_lot_inr: float | None = None
    position_lots: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
