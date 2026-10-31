"""Horizon Agent — AI Agent Trading prototype for HashKey Chain Horizon Hackathon (India).

Track: AI Agent Trading (5,000 USDT pool).
Design: an autonomous trading agent with hard risk guardrails that produces
*trade intents*, signs them locally (HMAC stand-in for a wallet signature in
this offline prototype), and records an auditable settlement log modelled on
HashKey Chain (EVM, chain id 177 testnet) transaction receipts.

Offline by construction: no network, no real keys, no real funds. Market data
is a deterministic synthetic feed so the demo and tests are reproducible.
Swap `SyntheticFeed` for a live HashKey Chain price oracle / DEX feed and
`LocalSigner` for a real wallet to go on-chain.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
from dataclasses import dataclass, field, asdict

HASHKEY_TESTNET_CHAIN_ID = 177  # HashKey Chain testnet (EVM)


# ---------------------------------------------------------------- market data
@dataclass
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class SyntheticFeed:
    """Deterministic pseudo-market: trend + cycle + noise from a seed."""

    def __init__(self, seed: int = 42, start_price: float = 100.0):
        self.seed = seed
        self.start_price = start_price

    def candles(self, n: int, interval_s: int = 60) -> list[Candle]:
        out: list[Candle] = []
        price = self.start_price
        t0 = 1_730_000_000
        for i in range(n):
            # deterministic noise in [-1, 1)
            x = math.sin(self.seed * 12.9898 + i * 78.233) * 43758.5453
            noise = (x - math.floor(x)) * 2 - 1
            trend = 0.02 * math.sin(i / 18.0)          # slow regime cycle
            drift = trend + 0.35 * noise               # per-candle % move
            o = price
            c = max(0.01, price * (1 + drift / 100.0))
            h = max(o, c) * (1 + abs(noise) * 0.001)
            l = min(o, c) * (1 - abs(noise) * 0.001)
            out.append(Candle(t0 + i * interval_s, o, h, l, c, 1000 + 500 * abs(noise)))
            price = c
        return out


# ------------------------------------------------------------------- strategy
def ema(values: list[float], period: int) -> float:
    if not values:
        raise ValueError("ema needs values")
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    gains, losses = 0.0, 0.0
    for a, b in zip(closes[-period - 1:-1], closes[-period:]):
        d = b - a
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100 - 100 / (1 + rs)


@dataclass
class Signal:
    action: str            # BUY | SELL | HOLD
    strength: float        # 0..1
    reason: str


def generate_signal(closes: list[float]) -> Signal:
    """EMA-crossover + RSI filter. Explainable on purpose: every signal
    carries the human-readable reason a judge (or user) can audit."""
    if len(closes) < 30:
        return Signal("HOLD", 0.0, "warming up: need >=30 candles")
    fast, slow = ema(closes[-30:], 9), ema(closes[-30:], 21)
    r = rsi(closes)
    spread = (fast - slow) / slow * 100
    if spread > 0.05 and r < 70:
        return Signal("BUY", min(1.0, abs(spread)), f"EMA9>EMA21 by {spread:.2f}%, RSI {r:.0f} not overbought")
    if spread < -0.05 and r > 30:
        return Signal("SELL", min(1.0, abs(spread)), f"EMA9<EMA21 by {spread:.2f}%, RSI {r:.0f} not oversold")
    return Signal("HOLD", 0.0, f"no edge: EMA spread {spread:.2f}%, RSI {r:.0f}")


# ---------------------------------------------------------------- risk guard
@dataclass
class RiskLimits:
    max_position_pct: float = 20.0     # % of equity in one position
    max_daily_loss_pct: float = 3.0    # kill-switch threshold
    min_strength: float = 0.05         # ignore weak signals


@dataclass
class Portfolio:
    cash: float = 10_000.0             # quote units (simulated USDT)
    position: float = 0.0              # base units held
    day_start_equity: float = 10_000.0
    halted: bool = False

    def equity(self, price: float) -> float:
        return self.cash + self.position * price


class RiskGuard:
    def __init__(self, limits: RiskLimits):
        self.limits = limits

    def check(self, pf: Portfolio, sig: Signal, price: float) -> tuple[bool, str, float]:
        """Return (allowed, note, quote_size). The guard can veto the brain —
        that separation is the core safety claim of the agent."""
        if pf.halted:
            return False, "HALTED: daily-loss kill switch is on", 0.0
        equity = pf.equity(price)
        day_pnl_pct = (equity - pf.day_start_equity) / pf.day_start_equity * 100
        if day_pnl_pct <= -self.limits.max_daily_loss_pct:
            pf.halted = True
            return False, f"KILL SWITCH: day P&L {day_pnl_pct:.2f}% breached -{self.limits.max_daily_loss_pct}%", 0.0
        if sig.action == "HOLD" or sig.strength < self.limits.min_strength:
            return False, "signal below min strength / HOLD", 0.0
        cap = equity * self.limits.max_position_pct / 100.0
        if sig.action == "BUY":
            room = max(0.0, cap - pf.position * price)
            size = min(room, cap * sig.strength, pf.cash)
            return (size > 0, "BUY sized by strength within position cap", size)
        # SELL: never sell more than held
        size = min(pf.position * price, cap * sig.strength) if pf.position > 0 else 0.0
        return (size > 0, "SELL sized from held position", size)


# ------------------------------------------------------- signing + settlement
class LocalSigner:
    """Offline stand-in for a wallet: HMAC over the intent. On-chain version
    replaces this with an ECDSA signature from the agent's wallet; the intent
    format stays identical."""

    def __init__(self, secret: str = "demo-only-not-a-real-key"):
        self.secret = secret.encode()

    def sign(self, intent: dict) -> str:
        blob = json.dumps(intent, sort_keys=True).encode()
        return hmac.new(self.secret, blob, hashlib.sha256).hexdigest()


@dataclass
class TradeIntent:
    nonce: int
    chain_id: int
    pair: str
    side: str
    quote_size: float
    ref_price: float
    reason: str
    ts: int


@dataclass
class Receipt:
    intent: dict
    signature: str
    tx_hash: str
    status: str = "SETTLED_SIMULATED"


class SettlementLog:
    """Append-only audit log; each receipt chains to the previous hash so
    tampering with history is detectable (the on-chain version anchors this
    hash on HashKey Chain)."""

    def __init__(self, signer: LocalSigner):
        self.signer = signer
        self.receipts: list[Receipt] = []
        self.prev_hash = "GENESIS"

    def settle(self, intent: TradeIntent) -> Receipt:
        d = asdict(intent)
        sig = self.signer.sign(d)
        tx = hashlib.sha256((self.prev_hash + sig).encode()).hexdigest()
        r = Receipt(d, sig, "0x" + tx)
        self.receipts.append(r)
        self.prev_hash = tx
        return r

    def verify_chain(self) -> bool:
        prev = "GENESIS"
        for r in self.receipts:
            if self.signer.sign(r.intent) != r.signature:
                return False
            tx = hashlib.sha256((prev + r.signature).encode()).hexdigest()
            if r.tx_hash != "0x" + tx:
                return False
            prev = tx
        return True


# ---------------------------------------------------------------------- agent
@dataclass
class Agent:
    pair: str = "HSK/USDT"
    feed: SyntheticFeed = field(default_factory=SyntheticFeed)
    risk: RiskGuard = field(default_factory=lambda: RiskGuard(RiskLimits()))
    portfolio: Portfolio = field(default_factory=Portfolio)
    log: SettlementLog = field(default_factory=lambda: SettlementLog(LocalSigner()))
    nonce: int = 0

    def run(self, candles_n: int = 120) -> dict:
        candles = self.feed.candles(candles_n)
        closes: list[float] = []
        trades = 0
        for c in candles:
            closes.append(c.close)
            sig = generate_signal(closes)
            ok, _note, size = self.risk.check(self.portfolio, sig, c.close)
            if not ok or size <= 0:
                continue
            self.nonce += 1
            intent = TradeIntent(self.nonce, HASHKEY_TESTNET_CHAIN_ID, self.pair,
                                 sig.action, round(size, 2), round(c.close, 4), sig.reason, c.ts)
            qty = size / c.close
            fee = size * 0.001  # 0.1% fee model
            if sig.action == "BUY":
                self.portfolio.position += qty
                self.portfolio.cash -= size + fee
            else:
                self.portfolio.position -= qty
                self.portfolio.cash += size - fee
            self.log.settle(intent)
            trades += 1
        final_price = closes[-1]
        return {
            "pair": self.pair,
            "candles": candles_n,
            "trades_executed": trades,
            "final_position_units": round(self.portfolio.position, 6),
            "final_price": round(final_price, 4),
            "position_value": round(self.portfolio.position * final_price, 2),
            "cash": round(self.portfolio.cash, 2),
            "equity_mark_to_market": round(self.portfolio.equity(final_price), 2),
            "halted": self.portfolio.halted,
            "audit_chain_valid": self.log.verify_chain(),
            "receipts": [asdict(r) for r in self.log.receipts],
        }


if __name__ == "__main__":
    t0 = time.time()
    report = Agent().run()
    print(json.dumps({k: v for k, v in report.items() if k != "receipts"}, indent=2))
    print(f"receipts: {len(report['receipts'])} (first tx {report['receipts'][0]['tx_hash'][:18]}...)" if report["receipts"] else "receipts: 0")
    print(f"ran in {time.time()-t0:.2f}s, offline, $0")
