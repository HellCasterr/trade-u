# Architecture

## Scope

Trade-U is a single-user, local Windows application. It has no order router, database, cloud service, or background credential store. Streamlit owns the UI session; broker tokens remain in process memory and optional local `.env` storage.

## Modules

| Module | Responsibility |
|---|---|
| `app.py` | Streamlit UI, source selection, session state, charts and result explanation |
| `tradeu/models.py` | Typed instrument, quote, configuration and result contracts |
| `tradeu/providers/base.py` | Broker-neutral history/quote/stream interfaces |
| `tradeu/providers/upstox.py` | Upstox V3 history, V2 quote and official V3 WebSocket streamer |
| `tradeu/providers/dhan.py` | DhanHQ history/quote/option-chain methods and MarketFeed V2 wrapper |
| `tradeu/providers/demo.py` | Deterministic offline candles, quote and simulated tick |
| `tradeu/analysis.py` | Cleaning, indicators, zones, risk geometry, analogues and gates |
| `tradeu/live.py` | Broker-payload normalization into a shared live quote contract |
| `tradeu/option_pricing.py` | Transparent Black–Scholes scenario calculations |
| `tradeu/charts.py` | Plotly candlesticks, EMAs, levels, stop and target |

## Normalized data contract

All providers return a pandas frame with ascending timestamps and these names:

| Field | Type | Requirement |
|---|---|---|
| `timestamp` | timezone-aware datetime | required |
| `open`, `high`, `low`, `close` | positive float | required |
| `volume` | number | recommended |
| `oi` | number | optional; useful for derivatives |

Quotes normalize LTP, best bid/ask, volume, OI and timestamp. The original broker payload is retained in `MarketQuote.raw` for debugging.

## Analysis sequence

1. Parse timestamps/numbers, deduplicate timestamps, and reject impossible OHLC rows.
2. Compute Wilder-style exponentially smoothed ATR plus 20/50 EMA.
3. Detect centered swing highs/lows and cluster levels within an ATR-scaled distance.
4. Select a long candidate only for bullish EMA structure near support; select a short candidate only for bearish structure near resistance.
5. Put the stop outside that level by an ATR buffer. Use the nearest opposing zone as target.
6. Convert geometry to gross/net percentages, R multiple, rupees per lot, and a risk-budget lot cap.
7. Match normalized return shapes against older, time-aligned, spaced windows. Measure target-first, stop-first, and timeout outcomes. Count same-bar ambiguity as stop-first.
8. Compute a 95% Wilson interval and compare its lower bound with the setup's break-even probability.
9. Interpret 20-bar price/OI changes for futures and options as long buildup, short buildup, short covering, or long unwinding.
10. Evaluate freshness, candle continuity, level strength, liquidity, expiry, OI, risk and statistical gates. A single block produces `NO TRADE`, while preserving the candidate and explanation for inspection.

## Security boundaries

- `.env` and Streamlit secrets are gitignored.
- Password widgets mask tokens.
- No broker order API is imported or called.
- API errors are shortened and shown as actionable UI messages.
- WebSocket handles are local daemon threads, can be explicitly stopped, and expose normalized ticks that can be applied to a fresh analysis snapshot.

For a production deployment, move secrets into the Windows Credential Manager, add structured logging with redaction, isolate broker processes, persist immutable input/output audit records, and add a market-session clock plus stale-tick watchdog.

## Extension points

- Add another broker by implementing `DataProvider`.
- Add a market-regime classifier before candidate selection.
- Replace Euclidean analogue distance with dynamic time warping, but validate it against a strict out-of-sample protocol.
- Add walk-forward backtests and purged/embargoed cross-validation before using any learned model.
- Add current NSE contract-master synchronization and corporate-action adjustment.
