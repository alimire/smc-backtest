# cTrader demo executor

Default mode is a **read-only dry run**. A separate gated smoke-order script can
place one tiny Fusion **demo** market order for connectivity testing.

## Fixed safety controls

- cTrader demo host only
- Fusion Markets demo login `10123191` only
- cTrader account ID `47820966` only
- EURUSD only
- A+ confirmation required
- Stop loss and take profit required and directionally valid
- Minimum 2.0 risk/reward
- Maximum risk of 0.25% per trade
- No new approval while any position is open
- No new approval while any pending order exists
- Linked account must be demo and Fusion Markets
- 1% daily realized-loss limit, calculated from the UK trading day
- Proposed risk cannot take the account beyond the daily loss limit
- Duplicate setup IDs are blocked
- Broker minimum and volume step are read from cTrader before sizing

## Run a dry run

Activate the project environment and provide a unique setup ID:

```bash
.venv/bin/python scripts/ctrader_demo_executor.py \
  --setup-id "2026-07-17-lokz-eurusd-long-001" \
  --side long \
  --entry 1.1600 \
  --sl 1.1590 \
  --tp 1.1620 \
  --risk 0.25 \
  --aplus
```

The command exits with status `0` when the proposal passes every control and
status `2` when it is blocked. Passing a dry run still never places an order.

Decisions are appended to `reports/demo_executor.jsonl`. Approved setup IDs are
stored in `.executor_state.json` to prevent accidental duplicate processing.

## One tiny demo smoke order

To place a real Fusion **demo** market order with SL/TP (minimum size):

```bash
.venv/bin/python scripts/ctrader_demo_smoke_order.py \
  --i-confirm-demo-order \
  --side buy \
  --sl-pips 15 \
  --tp-pips 30
```

This requires the confirmation flag, uses only account `10123191`, and refuses
live hosts. Current open smoke position can be closed manually in cTrader.

## Verify the safety policy

```bash
.venv/bin/python -m unittest discover -s tests -v
```

