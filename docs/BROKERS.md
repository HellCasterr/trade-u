# Broker setup and data contracts

## Credential policy

Create `.env` from `.env.example`. This file is ignored by git.

```dotenv
UPSTOX_ACCESS_TOKEN=...
DHAN_CLIENT_ID=...
DHAN_ACCESS_TOKEN=...
```

Tokens are broker-issued and may expire. Trade-U does not automate interactive login or token renewal because those flows depend on the user's registered app and redirect URI.

## Upstox adapter

`UpstoxProvider` uses:

- `GET /v3/historical-candle/{instrument_key}/{unit}/{interval}/{to_date}/{from_date}`;
- `GET /v2/market-quote/quotes?instrument_key=...`;
- `upstox_client.MarketDataStreamerV3` for `full`/`ltpc`/greek-capable WebSocket modes.

The UI expects the Upstox `instrument_key`, not only a trading symbol. Download/search the current broker instrument master because derivative keys expire and change.

Common failures:

| Symptom | Likely cause |
|---|---|
| HTTP 401/403 | Token expired, wrong app, or missing market-data entitlement |
| No candles | Invalid key, holiday, unsupported interval, or excessive date range |
| Quote works but stream fails | WebSocket entitlement, SDK/network issue, or broker connection limit |
| No bid/ask | Index quote or illiquid instrument does not expose depth |

The stream wrapper enables the SDK's 5-second auto-reconnect with up to 10 retries. REST quote/history still works independently if streaming is unavailable.

## Dhan adapter

`DhanProvider` uses official SDK methods:

- `historical_daily_data` → `/charts/historical`;
- `intraday_minute_data` → `/charts/intraday`;
- `quote_data` → `/marketfeed/quote`;
- `option_chain` and `expiry_list`;
- `MarketFeed` V2 for WebSocket ticks.

Dhan inputs are a numeric `securityId`, `exchangeSegment`, and `instrument` code. These three values must describe the same contract.

Typical combinations:

| Asset | Segment | Instrument code |
|---|---|---|
| NSE equity | `NSE_EQ` | `EQUITY` |
| NSE index | `IDX_I` | `INDEX` |
| NSE index future | `NSE_FNO` | `FUTIDX` |
| NSE stock future | `NSE_FNO` | `FUTSTK` |
| NSE index option | `NSE_FNO` | `OPTIDX` |
| NSE stock option | `NSE_FNO` | `OPTSTK` |

Dhan's documented live-feed limits include multiple connection and per-connection instrument caps, with subscription requests batched to 100 instruments. Trade-U subscribes to one selected instrument. The option-chain endpoint is rate-limited; avoid repeated rapid refreshes.

## Live stream lifecycle

The UI's **Start live stream** button constructs a broker SDK stream and starts its background thread. The latest packet is normalized into LTP, bid, ask, volume, OI, and timestamp where those fields are present. **Apply latest tick and re-analyze** sends that normalized quote through the complete safety/decision pipeline. **Fetch REST quote** is a fallback when WebSocket streaming is unavailable. **Stop live stream** closes the socket.

Trade-U deliberately requires an explicit apply/refresh action instead of recomputing on every tick. This avoids an uncontrolled UI rerun loop and preserves a comprehensible decision snapshot.

REST calls use bounded retry/backoff for throttling and temporary server errors. Authentication and malformed-instrument failures remain visible instead of retrying forever.

## Option data

For Upstox, the option lens can request the underlying put/call chain plus the selected contract's live IV, delta, gamma, theta, and vega. For Dhan, it can request the underlying option chain; Dhan's documented one-unique-request-per-three-seconds limit still applies.
