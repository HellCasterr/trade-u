from __future__ import annotations

import math
from dataclasses import asdict
from datetime import datetime, time, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from tradeu.models import (
    AnalogueStats,
    AnalysisConfig,
    InstrumentSpec,
    MarketQuote,
    PriceZone,
    TradeAnalysis,
)

REQUIRED_COLUMNS = {"timestamp", "open", "high", "low", "close"}
IST = ZoneInfo("Asia/Kolkata")
NSE_OPEN = time(9, 15)
NSE_CLOSE = time(15, 30)


def market_session_status(now: datetime | None = None) -> tuple[bool, datetime]:
    """Return whether the regular NSE cash/F&O session is open.

    This intentionally knows weekdays and session hours, not the exchange holiday
    calendar. A broker market-status endpoint should remain the final authority.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local = current.astimezone(IST)
    is_open = local.weekday() < 5 and NSE_OPEN <= local.time().replace(tzinfo=None) <= NSE_CLOSE
    return is_open, local


def candle_continuity(data: pd.DataFrame) -> dict[str, float | bool | None]:
    """Estimate missing intraday bars without treating the overnight gap as missing."""
    local = data["timestamp"].dt.tz_convert(IST)
    diffs = local.diff().dt.total_seconds() / 60
    same_day = local.dt.date.eq(local.shift(1).dt.date)
    intraday_diffs = diffs[same_day & diffs.gt(0) & diffs.le(180)]
    if intraday_diffs.empty:
        return {"is_intraday": False, "bar_minutes": None, "missing_ratio": 0.0}
    bar_minutes = float(intraday_diffs.median())
    if bar_minutes <= 0:
        return {"is_intraday": True, "bar_minutes": None, "missing_ratio": 1.0}
    expected = np.maximum(np.rint(intraday_diffs.to_numpy() / bar_minutes).astype(int), 1)
    missing = int(np.maximum(expected - 1, 0).sum())
    observed = int(len(intraday_diffs))
    return {
        "is_intraday": True,
        "bar_minutes": bar_minutes,
        "missing_ratio": missing / max(observed + missing, 1),
    }


def derivative_oi_context(data: pd.DataFrame, instrument: InstrumentSpec) -> dict[str, float | str | bool | None]:
    if instrument.kind == "CASH":
        return {"available": False, "classification": "Not applicable"}
    if "oi" not in data or data["oi"].notna().sum() < 21:
        return {"available": False, "classification": "OI unavailable"}
    sample = data.dropna(subset=["oi", "close"]).tail(21)
    if len(sample) < 21 or float(sample["oi"].iloc[0]) <= 0 or float(sample["close"].iloc[0]) <= 0:
        return {"available": False, "classification": "OI unavailable"}
    oi_change = (float(sample["oi"].iloc[-1]) / float(sample["oi"].iloc[0]) - 1) * 100
    price_change = (float(sample["close"].iloc[-1]) / float(sample["close"].iloc[0]) - 1) * 100
    if price_change >= 0 and oi_change >= 0:
        classification = "Long buildup"
    elif price_change < 0 and oi_change >= 0:
        classification = "Short buildup"
    elif price_change >= 0 and oi_change < 0:
        classification = "Short covering"
    else:
        classification = "Long unwinding"
    return {
        "available": True,
        "classification": classification,
        "oi_change_pct_20": oi_change,
        "price_change_pct_20": price_change,
    }


def prepare_candles(candles: pd.DataFrame) -> pd.DataFrame:
    """Validate and enrich an OHLCV frame without silently inventing prices."""
    missing = REQUIRED_COLUMNS.difference(candles.columns)
    if missing:
        raise ValueError(f"Missing candle columns: {', '.join(sorted(missing))}")
    frame = candles.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
    for column in ["open", "high", "low", "close", "volume", "oi"]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "open", "high", "low", "close"])
    frame = frame.drop_duplicates(subset="timestamp", keep="last").sort_values("timestamp")
    valid = (
        (frame[["open", "high", "low", "close"]] > 0).all(axis=1)
        & (frame["high"] >= frame[["open", "close", "low"]].max(axis=1))
        & (frame["low"] <= frame[["open", "close", "high"]].min(axis=1))
    )
    return frame.loc[valid].reset_index(drop=True)


def add_indicators(frame: pd.DataFrame, config: AnalysisConfig) -> pd.DataFrame:
    data = frame.copy()
    previous_close = data["close"].shift(1)
    true_range = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - previous_close).abs(),
            (data["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    data["atr"] = true_range.ewm(alpha=1 / config.atr_period, adjust=False).mean()
    data["ema_fast"] = data["close"].ewm(span=config.fast_ema, adjust=False).mean()
    data["ema_slow"] = data["close"].ewm(span=config.slow_ema, adjust=False).mean()
    return data


def _cluster_levels(levels: Iterable[float], width: float) -> list[tuple[float, int]]:
    clusters: list[list[float]] = []
    for value in sorted(float(level) for level in levels if np.isfinite(level)):
        if not clusters or abs(value - float(np.mean(clusters[-1]))) > width:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return [(float(np.mean(cluster)), len(cluster)) for cluster in clusters]


def find_zones(
    data: pd.DataFrame, config: AnalysisConfig, entry: float
) -> tuple[PriceZone | None, PriceZone | None, list[tuple[float, int]]]:
    window = config.pivot_window * 2 + 1
    pivot_high = data["high"].eq(data["high"].rolling(window, center=True).max())
    pivot_low = data["low"].eq(data["low"].rolling(window, center=True).min())
    lookback = min(len(data), 320)
    levels = pd.concat([data.loc[pivot_high, "high"], data.loc[pivot_low, "low"]]).tail(lookback)
    atr = float(data["atr"].iloc[-1])
    width = max(atr * config.zone_atr_width, entry * 0.001)
    clusters = _cluster_levels(levels, width)
    supports = [(level, strength) for level, strength in clusters if level < entry]
    resistances = [(level, strength) for level, strength in clusters if level > entry]
    support = None
    resistance = None
    if supports:
        level, strength = max(supports, key=lambda item: item[0])
        support = PriceZone(level, strength, "support", (entry - level) / atr)
    if resistances:
        level, strength = min(resistances, key=lambda item: item[0])
        resistance = PriceZone(level, strength, "resistance", (level - entry) / atr)
    return support, resistance, clusters


def _wilson(successes: int, total: int, z: float) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    p = successes / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return (centre - margin) / denominator, (centre + margin) / denominator


def historical_analogues(
    data: pd.DataFrame,
    direction: str,
    target_move: float,
    stop_move: float,
    config: AnalysisConfig,
) -> AnalogueStats:
    closes = data["close"].to_numpy(dtype=float)
    highs = data["high"].to_numpy(dtype=float)
    lows = data["low"].to_numpy(dtype=float)
    window = config.pattern_window
    horizon = config.forward_horizon
    if len(closes) < window * 2 + horizon + 5:
        return AnalogueStats()

    log_returns = np.diff(np.log(closes))
    current = log_returns[-window:]
    current = (current - current.mean()) / (current.std() + 1e-9)
    distances: list[tuple[float, int]] = []
    continuity = candle_continuity(data)
    timestamps = data["timestamp"].dt.tz_convert(IST)
    current_minute = int(timestamps.iloc[-1].hour * 60 + timestamps.iloc[-1].minute)
    bar_minutes = float(continuity.get("bar_minutes") or 0)
    latest_allowed = len(closes) - horizon - window - 2
    for end in range(window, latest_allowed + 1):
        if config.align_intraday_analogues and continuity["is_intraday"] and bar_minutes:
            candidate_minute = int(timestamps.iloc[end].hour * 60 + timestamps.iloc[end].minute)
            if abs(candidate_minute - current_minute) > bar_minutes:
                continue
        sample = log_returns[end - window : end]
        standardized = (sample - sample.mean()) / (sample.std() + 1e-9)
        distance = float(np.sqrt(np.mean((standardized - current) ** 2)))
        distances.append((distance, end))
    selected: list[tuple[float, int]] = []
    minimum_separation = max(window, horizon)
    for candidate in sorted(distances):
        _, end = candidate
        if all(abs(end - chosen_end) >= minimum_separation for _, chosen_end in selected):
            selected.append(candidate)
        if len(selected) >= config.analogue_count:
            break
    target_first = stop_first = timeout = 0
    returns: list[float] = []

    for _, end in selected:
        start = closes[end]
        if direction == "LONG":
            target_level = start * (1 + target_move)
            stop_level = start * (1 - stop_move)
        else:
            target_level = start * (1 - target_move)
            stop_level = start * (1 + stop_move)
        outcome = "timeout"
        for index in range(end + 1, min(len(closes), end + horizon + 1)):
            hit_target = highs[index] >= target_level if direction == "LONG" else lows[index] <= target_level
            hit_stop = lows[index] <= stop_level if direction == "LONG" else highs[index] >= stop_level
            if hit_stop:  # Conservative when both are touched inside one OHLC bar.
                outcome = "stop"
                break
            if hit_target:
                outcome = "target"
                break
        final = closes[min(len(closes) - 1, end + horizon)]
        signed_return = (final / start - 1) * (1 if direction == "LONG" else -1)
        returns.append(signed_return * 100)
        if outcome == "target":
            target_first += 1
        elif outcome == "stop":
            stop_first += 1
        else:
            timeout += 1

    total = len(selected)
    low, high = _wilson(target_first, total, config.confidence_z)
    return AnalogueStats(
        sample_size=total,
        target_first=target_first,
        stop_first=stop_first,
        timeout=timeout,
        win_probability=(target_first / total) if total else None,
        wilson_low=low,
        wilson_high=high,
        median_forward_return_pct=float(np.median(returns)) if returns else None,
    )


def _candle_labels(data: pd.DataFrame) -> list[str]:
    if len(data) < 2:
        return []
    previous = data.iloc[-2]
    current = data.iloc[-1]
    body = abs(current.close - current.open)
    candle_range = max(current.high - current.low, 1e-9)
    labels: list[str] = []
    if body / candle_range <= 0.1:
        labels.append("Doji / indecision")
    bullish_engulfing = (
        previous.close < previous.open
        and current.close > current.open
        and current.open <= previous.close
        and current.close >= previous.open
    )
    bearish_engulfing = (
        previous.close > previous.open
        and current.close < current.open
        and current.open >= previous.close
        and current.close <= previous.open
    )
    if bullish_engulfing:
        labels.append("Bullish engulfing")
    if bearish_engulfing:
        labels.append("Bearish engulfing")
    lower_wick = min(current.open, current.close) - current.low
    upper_wick = current.high - max(current.open, current.close)
    if lower_wick > body * 2 and upper_wick < body:
        labels.append("Hammer-like rejection")
    if upper_wick > body * 2 and lower_wick < body:
        labels.append("Shooting-star-like rejection")
    return labels


def analyze_market(
    candles: pd.DataFrame,
    instrument: InstrumentSpec,
    quote: MarketQuote | None = None,
    config: AnalysisConfig | None = None,
) -> tuple[TradeAnalysis, pd.DataFrame]:
    """Build an explainable setup. It never places or routes an order."""
    config = config or AnalysisConfig()
    raw_rows = len(candles)
    clean = prepare_candles(candles)
    if len(clean) < max(config.slow_ema + 10, 70):
        raise ValueError("At least 70 valid candles are required for analysis.")
    data = add_indicators(clean, config)
    entry = float(quote.ltp if quote and quote.ltp > 0 else data["close"].iloc[-1])
    atr = float(data["atr"].iloc[-1])
    support, resistance, clusters = find_zones(data, config, entry)
    fast = float(data["ema_fast"].iloc[-1])
    slow = float(data["ema_slow"].iloc[-1])
    trend = "bullish" if fast > slow else "bearish" if fast < slow else "flat"

    candidate = "NO TRADE"
    location_ok = False
    if trend == "bullish" and support:
        location_ok = support.distance_atr <= config.entry_zone_atr
        if location_ok:
            candidate = "LONG"
    elif trend == "bearish" and resistance:
        location_ok = resistance.distance_atr <= config.entry_zone_atr
        if location_ok:
            candidate = "SHORT"

    stop = target = None
    if candidate == "LONG" and support:
        stop = support.level - config.stop_atr_buffer * atr
        target = resistance.level if resistance else entry + 2 * (entry - stop)
    elif candidate == "SHORT" and resistance:
        stop = resistance.level + config.stop_atr_buffer * atr
        target = support.level if support else entry - 2 * (stop - entry)
    stop = _round_tick(stop, instrument.tick_size) if stop is not None else None
    target = _round_tick(target, instrument.tick_size) if target is not None else None

    valid_geometry = bool(
        candidate == "LONG"
        and stop is not None
        and target is not None
        and stop < entry < target
        or candidate == "SHORT"
        and stop is not None
        and target is not None
        and target < entry < stop
    )
    risk = abs(entry - stop) if valid_geometry and stop is not None else None
    reward = abs(target - entry) if valid_geometry and target is not None else None
    reward_risk = reward / risk if risk and reward else None
    target_pct = (reward / entry * 100) if reward else None
    stop_pct = (risk / entry * 100) if risk else None
    net_target = max(0.0, target_pct - config.estimated_cost_pct) if target_pct is not None else None
    net_stop = stop_pct + config.estimated_cost_pct if stop_pct is not None else None

    analogues = AnalogueStats()
    if candidate != "NO TRADE" and reward and risk:
        analogues = historical_analogues(
            data,
            candidate,
            reward / entry,
            risk / entry,
            config,
        )

    session_open, session_time = market_session_status()
    live_quote_pass = quote is not None or not config.require_live_quote
    spread_pass = (
        not config.require_live_quote
        if quote is None or quote.spread_pct is None
        else quote.spread_pct <= config.maximum_spread_pct
    )
    quote_age_seconds = None
    if quote is not None and quote.timestamp is not None:
        stamp = quote.timestamp
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        quote_age_seconds = max(0.0, (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds())
    quote_fresh_pass = (
        not session_open
        or not config.require_live_quote
        or (quote_age_seconds is not None and quote_age_seconds <= config.maximum_quote_age_seconds)
    )
    rr_pass = reward_risk is not None and reward_risk >= config.minimum_reward_risk
    sample_pass = analogues.sample_size >= config.minimum_analogues
    break_even = (1 / (1 + reward_risk)) if reward_risk else 1.0
    probability_pass = bool(
        analogues.wilson_low is not None and analogues.wilson_low > break_even + config.probability_edge_pct / 100
    )
    risk_per_lot = risk * max(instrument.lot_size, 1) if risk else None
    risk_pass = risk_per_lot is None or risk_per_lot <= config.risk_budget_inr
    position_lots = int(config.risk_budget_inr // risk_per_lot) if risk_per_lot and risk_per_lot > 0 else 0
    volume_pass = True
    if "volume" in data and data["volume"].notna().sum() >= 20:
        median_volume = float(data["volume"].tail(20).median())
        latest_volume = float(data["volume"].iloc[-1])
        volume_pass = median_volume <= 0 or latest_volume >= median_volume * 0.15
    expiry_pass = instrument.expiry is None or (instrument.expiry - pd.Timestamp.now().date()).days > 1
    data_pass = len(clean) >= 150 and len(clean) / max(raw_rows, 1) >= 0.9
    levels_pass = support is not None and resistance is not None
    level_strength_pass = bool(
        support is not None
        and resistance is not None
        and support.strength >= config.minimum_zone_strength
        and resistance.strength >= config.minimum_zone_strength
    )
    continuity = candle_continuity(data)
    continuity_pass = float(continuity["missing_ratio"] or 0.0) <= config.maximum_missing_bar_ratio
    candle_age_minutes = max(
        0.0,
        (datetime.now(timezone.utc) - data["timestamp"].iloc[-1].to_pydatetime()).total_seconds() / 60,
    )
    candle_fresh_pass = True
    if session_open and continuity["is_intraday"] and continuity["bar_minutes"]:
        candle_fresh_pass = candle_age_minutes <= float(continuity["bar_minutes"]) * config.maximum_candle_delay_bars

    oi_context = derivative_oi_context(data, instrument)
    supportive_oi = True
    if instrument.kind != "CASH":
        if not oi_context.get("available"):
            supportive_oi = not config.require_derivative_oi
        elif candidate == "LONG":
            supportive_oi = oi_context.get("classification") in {"Long buildup", "Short covering"}
        elif candidate == "SHORT":
            supportive_oi = oi_context.get("classification") in {"Short buildup", "Long unwinding"}

    gates = {
        "Enough clean history": data_pass,
        "Candle sequence is continuous": continuity_pass,
        "Latest candle is timely": candle_fresh_pass,
        "Required live quote available": live_quote_pass,
        "Live quote is fresh": quote_fresh_pass,
        "Support and resistance found": levels_pass,
        "Support/resistance have repeated touches": level_strength_pass,
        "Trend and location align": location_ok,
        "Stop/target geometry valid": valid_geometry,
        "Reward:risk threshold": rr_pass,
        "Spread acceptable": spread_pass,
        "Volume acceptable": volume_pass,
        "Not at expiry cliff": expiry_pass,
        "One-lot risk within budget": risk_pass,
        "Enough historical analogues": sample_pass,
        "Confidence clears break-even": probability_pass,
    }
    if instrument.kind != "CASH":
        gates["Derivative OI supports direction"] = supportive_oi
    verdict = candidate if candidate != "NO TRADE" and all(gates.values()) else "NO TRADE"
    reasons: list[str] = []
    warnings: list[str] = []
    if candidate == "NO TRADE":
        reasons.append("Price is not close enough to a trend-aligned support/resistance entry zone.")
    else:
        reasons.append(
            f"{trend.title()} EMA structure with price near a relevant level suggests a {candidate.lower()} candidate."
        )
    for name, passed in gates.items():
        (reasons if passed else warnings).append(("Pass: " if passed else "Block: ") + name)
    if quote is None:
        warnings.append("No live quote was supplied; spread and live-price freshness could not be verified.")
    if instrument.kind != "CASH" and not oi_context.get("available"):
        warnings.append("Derivative OI history is unavailable; OI confirmation could not be verified.")
    if analogues.sample_size:
        reasons.append(
            f"Historical analogue result: {analogues.target_first}/{analogues.sample_size} reached target before stop."
        )

    return (
        TradeAnalysis(
            verdict=verdict,
            entry=_round_tick(entry, instrument.tick_size),
            stop=stop,
            target=target,
            target_pct=target_pct,
            stop_loss_pct=stop_pct,
            net_target_pct=net_target,
            net_stop_loss_pct=net_stop,
            reward_risk=reward_risk,
            atr=atr,
            trend=trend,
            support=support,
            resistance=resistance,
            reasons=reasons,
            warnings=warnings,
            gates=gates,
            analogues=analogues,
            risk_per_lot_inr=risk_per_lot,
            reward_per_lot_inr=(reward * max(instrument.lot_size, 1)) if reward else None,
            position_lots=max(position_lots, 0),
            metadata={
                "candidate_direction": candidate,
                "break_even_probability": break_even,
                "candle_patterns": _candle_labels(data),
                "zones": clusters,
                "session_open": session_open,
                "session_time_ist": session_time.isoformat(),
                "quote_age_seconds": quote_age_seconds,
                "candle_age_minutes": candle_age_minutes,
                "continuity": continuity,
                "oi_context": oi_context,
                "config": asdict(config),
            },
        ),
        data,
    )


def _round_tick(value: float | None, tick: float) -> float | None:
    if value is None:
        return None
    if tick <= 0:
        return round(value, 2)
    return round(round(value / tick) * tick, 4)
