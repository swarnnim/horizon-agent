import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agent.engine import (Agent, Portfolio, RiskGuard, RiskLimits, Signal,
                          SettlementLog, LocalSigner, TradeIntent, generate_signal)

def test_feed_deterministic():
    a, b = Agent(), Agent()
    assert [c.close for c in a.feed.candles(50)] == [c.close for c in b.feed.candles(50)]

def test_signal_warmup_holds():
    assert generate_signal([100.0] * 10).action == "HOLD"

def test_kill_switch_halts():
    pf = Portfolio(cash=9_600.0, day_start_equity=10_000.0)  # -4% day
    ok, note, size = RiskGuard(RiskLimits()).check(pf, Signal("BUY", 0.9, "x"), 100.0)
    assert not ok and pf.halted and "KILL SWITCH" in note and size == 0.0

def test_position_cap_respected():
    pf = Portfolio()
    ok, _n, size = RiskGuard(RiskLimits(max_position_pct=20.0)).check(
        pf, Signal("BUY", 1.0, "x"), 100.0)
    assert ok and size <= 2_000.0 + 1e-9

def test_sell_never_exceeds_holdings():
    pf = Portfolio(position=0.0)
    ok, _n, size = RiskGuard(RiskLimits()).check(pf, Signal("SELL", 1.0, "x"), 100.0)
    assert not ok and size == 0.0

def test_audit_chain_detects_tamper():
    log = SettlementLog(LocalSigner())
    log.settle(TradeIntent(1, 177, "HSK/USDT", "BUY", 100.0, 100.0, "r", 1))
    assert log.verify_chain()
    log.receipts[0].intent["quote_size"] = 999.0
    assert not log.verify_chain()

def test_full_run_reproducible_and_audited():
    r1, r2 = Agent().run(120), Agent().run(120)
    assert r1["trades_executed"] == r2["trades_executed"]
    assert r1["audit_chain_valid"] is True
    assert r1["equity_mark_to_market"] > 0
