# Product roadmap

The current release is a read-only research MVP. The following features would most improve smoothness and decision quality without pretending to guarantee profitable trades.

## Implemented in the reliability upgrade

- Broker retries with bounded backoff for temporary failures and throttling.
- Fresh-quote, fresh-candle, continuity, repeated-level and optional OI confirmation gates.
- NSE regular-session awareness in Indian Standard Time.
- Normalized Upstox/Dhan WebSocket ticks that can re-run the complete decision engine.
- REST quote fallback when the stream is unavailable.
- Dhan option chain and Upstox option chain/Greek access from the option lens.
- Intraday-time-aligned, spaced historical analogues.
- Windows and Ubuntu CI across Python 3.11 and 3.12.

## Next: live usability

1. **Instrument master and search.** Synchronize Upstox/Dhan masters, resolve symbols to current contracts, auto-fill lot/tick size, and prevent expired-contract selection.
2. **Live candle builder.** Aggregate ticks into exchange-session OHLCV bars, close bars deterministically, persist them locally, and recompute only on bar close.
3. **Market calendar/status.** Use an official market-status source plus the NSE holiday calendar instead of relying only on weekday/session time.
4. **Notifications.** Notify once per newly qualified setup, with deduplication, cool-down, acknowledgement, and invalidation messages.
5. **Replay and audit log.** Store every input snapshot, gate result and model version so a recommendation can be reproduced.

## Next: analysis quality

1. **Multi-timeframe confirmation.** Daily/60-minute regime with 5/15-minute entry structure.
2. **Walk-forward evaluation.** Purged/embargoed splits, per-symbol/timeframe calibration, realistic fees/slippage, and untouched holdout periods.
3. **Option-chain features.** PCR, OI walls, change in OI, IV rank/skew/smile, liquidity by strike, max-pain as context, and underlying/contract synchronization.
4. **Futures features.** Spot-future basis, roll/expiry continuity, OI/volume regime, and front/next-month comparison.
5. **Event risk.** Earnings, corporate actions, index rebalances and major scheduled macro events as no-trade windows.
6. **Transaction-cost engine.** Brokerage, STT, exchange charges, GST, stamp duty, impact/slippage and option margin rather than one flat percentage.

## Before unattended use

- Package a signed Windows installer and store tokens in Windows Credential Manager.
- Add a persistent service separate from the Streamlit UI, heartbeats, watchdogs and automatic recovery.
- Add broker contract tests against sandbox/live read-only accounts.
- Add portfolio-level exposure, correlation and daily-loss limits.
- Complete paper-trading observation over multiple market regimes before considering any execution integration.

Order placement is intentionally outside the current scope.

