from datetime import date, timedelta

import pandas as pd
import pytest

from tradeu.analysis import analyze_market, prepare_candles
from tradeu.models import AnalysisConfig, InstrumentSpec, MarketQuote
from tradeu.option_pricing import black_scholes, option_scenarios
from tradeu.providers.demo import DemoProvider
from tradeu.providers.upstox import UpstoxProvider, _upstox_interval


def demo_candles():
    return DemoProvider().historical("NIFTY", date.today() - timedelta(days=30), date.today(), "15minute")


def test_prepare_candles_drops_impossible_rows_and_duplicates():
    candles = demo_candles().head(20)
    duplicate = candles.iloc[[0]].copy()
    invalid = candles.iloc[[1]].copy()
    invalid["timestamp"] = invalid["timestamp"] + pd.Timedelta(seconds=1)
    invalid["high"] = invalid["low"] - 1
    dirty = pd.concat([candles, duplicate, invalid], ignore_index=True)
    clean = prepare_candles(dirty)
    assert len(clean) == 20
    assert clean["timestamp"].is_monotonic_increasing


def test_analysis_returns_auditable_percentages_and_gates():
    candles = demo_candles()
    instrument = InstrumentSpec("NIFTY", "FUTURE", lot_size=25, expiry=date.today() + timedelta(days=10))
    quote = MarketQuote(
        float(candles.close.iloc[-1]), bid=float(candles.close.iloc[-1]) - 1, ask=float(candles.close.iloc[-1]) + 1
    )
    result, enriched = analyze_market(candles, instrument, quote, AnalysisConfig(minimum_analogues=10))
    assert len(enriched) == len(candles)
    assert result.verdict in {"LONG", "SHORT", "NO TRADE"}
    assert set(result.gates).issuperset({"Spread acceptable", "Reward:risk threshold", "Confidence clears break-even"})
    if result.stop is not None:
        assert result.stop_loss_pct is not None and result.stop_loss_pct > 0
        assert result.net_stop_loss_pct >= result.stop_loss_pct
    if result.target is not None:
        assert result.target_pct is not None and result.target_pct > 0
        assert result.net_target_pct <= result.target_pct


def test_wide_spread_forces_no_trade_when_a_candidate_exists_or_is_irrelevant():
    candles = demo_candles()
    ltp = float(candles.close.iloc[-1])
    quote = MarketQuote(ltp, bid=ltp * 0.98, ask=ltp * 1.02)
    result, _ = analyze_market(candles, InstrumentSpec("NIFTY"), quote)
    assert result.gates["Spread acceptable"] is False
    assert result.verdict == "NO TRADE"


def test_black_scholes_and_option_scenarios_are_finite():
    call = black_scholes(22500, 22500, 7 / 365, 0.065, 0.18, "CE")
    put = black_scholes(22500, 22500, 7 / 365, 0.065, 0.18, "PE")
    assert call > 0 and put > 0
    table = option_scenarios(200, 22500, 22500, date.today() + timedelta(days=7), "CE", 22700, 22350, 18)
    assert len(table) == 3
    assert table.select_dtypes("number").notna().all().all()


@pytest.mark.parametrize(
    ("label", "expected"),
    [("day", ("days", 1)), ("15minute", ("minutes", 15)), ("5m", ("minutes", 5))],
)
def test_upstox_interval_mapping(label, expected):
    assert _upstox_interval(label) == expected


def test_upstox_adapter_normalizes_mock_payloads(monkeypatch):
    provider = UpstoxProvider("test-token")
    candles = [["2026-09-15T09:15:00+05:30", 100, 103, 99, 102, 1000, 50]]
    monkeypatch.setattr(provider, "_get", lambda *args, **kwargs: {"data": {"candles": candles}})
    frame = provider.historical("NSE_EQ|TEST", date(2026, 9, 1), date(2026, 9, 15), "day")
    assert list(frame.columns) == ["timestamp", "open", "high", "low", "close", "volume", "oi"]
    assert float(frame.close.iloc[0]) == 102
