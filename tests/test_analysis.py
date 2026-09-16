from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from tradeu.analysis import analyze_market, candle_continuity, derivative_oi_context, prepare_candles
from tradeu.live import payload_to_quote
from tradeu.models import AnalysisConfig, InstrumentSpec, MarketQuote
from tradeu.option_pricing import black_scholes, option_scenarios
from tradeu.providers.demo import DemoProvider
from tradeu.providers.dhan import DhanProvider
from tradeu.providers.upstox import UpstoxProvider, _upstox_interval


def demo_candles():
    return DemoProvider().historical("NIFTY", date.today() - timedelta(days=30), date.today(), "15minute")


def test_prepare_candles_drops_impossible_rows_and_duplicates():
    candles = demo_candles().head(20)
    duplicate = candles.iloc[[0]].copy()
    invalid = candles.iloc[[1]].copy()
    invalid["timestamp"] = invalid["timestamp"] + timedelta(seconds=1)
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


def test_live_quote_can_be_required_as_a_hard_safety_gate():
    result, _ = analyze_market(
        demo_candles(),
        InstrumentSpec("NIFTY"),
        quote=None,
        config=AnalysisConfig(require_live_quote=True),
    )
    assert result.gates["Required live quote available"] is False
    assert result.gates["Spread acceptable"] is False
    assert result.verdict == "NO TRADE"


def test_stale_quote_is_blocked_during_market_session(monkeypatch):
    monkeypatch.setattr(
        "tradeu.analysis.market_session_status",
        lambda: (True, datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)),
    )
    candles = demo_candles()
    ltp = float(candles.close.iloc[-1])
    stale = MarketQuote(
        ltp=ltp,
        bid=ltp - 1,
        ask=ltp + 1,
        timestamp=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    result, _ = analyze_market(
        candles,
        InstrumentSpec("NIFTY"),
        stale,
        AnalysisConfig(require_live_quote=True, maximum_quote_age_seconds=60),
    )
    assert result.gates["Live quote is fresh"] is False
    assert result.verdict == "NO TRADE"


def test_intraday_continuity_detects_missing_bar_but_ignores_overnight():
    timestamps = pd.to_datetime(
        [
            "2026-09-14T09:15:00+05:30",
            "2026-09-14T09:20:00+05:30",
            "2026-09-14T09:30:00+05:30",
            "2026-09-15T09:15:00+05:30",
            "2026-09-15T09:20:00+05:30",
        ],
        utc=True,
    )
    metrics = candle_continuity(pd.DataFrame({"timestamp": timestamps}))
    assert metrics["is_intraday"] is True
    assert metrics["bar_minutes"] == 5
    assert 0 < metrics["missing_ratio"] < 0.5


def test_derivative_oi_context_classifies_long_buildup():
    data = pd.DataFrame({"close": range(100, 121), "oi": range(1000, 1210, 10)})
    context = derivative_oi_context(data, InstrumentSpec("NIFTY FUT", "FUTURE"))
    assert context["available"] is True
    assert context["classification"] == "Long buildup"


def test_upstox_and_dhan_stream_packets_normalize_to_quote():
    upstox_payload = {
        "feeds": {
            "NSE_FO|123": {
                "fullFeed": {
                    "marketFF": {
                        "ltpc": {"ltp": 201.5, "ltt": 1_789_520_400_000},
                        "marketLevel": {"bidAskQuote": [{"bidP": 201.4, "askP": 201.6}]},
                        "oi": 55000,
                    }
                }
            }
        }
    }
    dhan_payload = {"LTP": "202.5", "volume": 1000, "OI": 56000}
    upstox_quote = payload_to_quote(upstox_payload)
    dhan_quote = payload_to_quote(dhan_payload)
    assert upstox_quote and upstox_quote.ltp == 201.5 and upstox_quote.spread_pct is not None
    assert dhan_quote and dhan_quote.ltp == 202.5 and dhan_quote.open_interest == 56000


def test_dhan_retries_only_retryable_error_codes():
    provider = object.__new__(DhanProvider)
    provider.retry_delays = (0,)
    calls = iter([{"status": "failure", "errorCode": 805}, {"status": "success", "data": {"ok": True}}])
    result = provider._call("test", lambda: next(calls))
    assert result["data"]["ok"] is True
