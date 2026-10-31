# Horizon Agent — AI Agent Trading on HashKey Chain

**HashKey Chain Horizon Hackathon · India — Track 1: AI Agent Trading**
Builder: Swarnnim Agnihotri (solo) · Status: working offline prototype, 7/7 tests passing

## What it is
An autonomous trading agent whose *brain* and *risk guard* are separate systems:
the strategy proposes, the guard disposes. Every executed trade becomes a
signed trade intent recorded in a hash-chained, tamper-evident settlement log
modelled on HashKey Chain (EVM, testnet chain id 177) receipts.

- **Strategy (explainable):** EMA 9/21 crossover + RSI filter. Every signal
  carries a human-readable reason string — no black-box orders.
- **Risk guard (can veto the brain):** 20% single-position cap, minimum
  signal-strength floor, and a −3% daily-loss **kill switch** that halts the
  agent. Sells can never exceed holdings; buys can never exceed cash.
- **Settlement log:** each intent is signed (HMAC stand-in for a wallet
  signature in this offline build) and hash-chained to the previous receipt,
  so any tampering with trade history is detectable. On-chain, the chain head
  hash is the value you'd anchor in a HashKey Chain transaction.

## Run it
```bash
python3 agent/engine.py        # demo run: 120 candles, prints report + receipts count
python3 -m pytest tests/ -q    # 7 tests
```
Demo result (deterministic seed): 11 trades, equity 10,000 → 10,057.04
simulated USDT mark-to-market, audit chain valid.

## Going on-chain (next step)
1. Replace `SyntheticFeed` with a HashKey Chain price feed / DEX quotes.
2. Replace `LocalSigner` (HMAC demo) with the agent wallet's ECDSA signature —
   the `TradeIntent` format does not change.
3. Submit intents to a HashKey Chain testnet DEX/settlement contract and
   anchor the audit-log head hash per batch.

## Honesty notes
Simulation only: synthetic data, simulated settlement, no real funds, no real
keys. This is a hackathon prototype, not financial advice and not a claim of
live trading performance.
