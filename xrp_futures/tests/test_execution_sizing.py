from dataclasses import replace
from decimal import Decimal

import pytest

from xrp_futures.config.live import LiveConfig
from xrp_futures.execution.sizing import EntryPlan, Skip, plan_entry
from xrp_futures.execution.symbol_filters import SymbolFilters

CONFIG = replace(LiveConfig(), risk_pct=0.003)   # fixed 0.3% so the hand-checked numbers don't follow the live default
XRP = SymbolFilters("XRPUSDT", Decimal("0.0001"), Decimal("0.1"), Decimal("0.1"), Decimal("5"))
BTC = SymbolFilters("BTCUSDT", Decimal("0.1"), Decimal("0.001"), Decimal("0.001"), Decimal("50"))


def test_long_entry_matches_backtest_risk_math():
    # $2,000 equity, price 1.50, ATR 0.06 -> stop 2*0.06 = 0.12 below (8%) -> notional 2000*0.003/0.08 = 75
    plan = plan_entry("XRPUSDT", 1, 2_000.0, 1.50, 0.06, XRP, open_notional=0.0, config=CONFIG)
    assert isinstance(plan, EntryPlan)
    assert plan.qty == Decimal("50.0")            # 75 / 1.50, already on the 0.1 step
    assert plan.notional == pytest.approx(75.0)
    assert plan.stop_price == Decimal("1.3800")
    # Loss if stopped = 0.3% of equity
    assert float(plan.qty) * (1.50 - float(plan.stop_price)) == pytest.approx(2_000 * 0.003)


def test_short_stop_is_above_price():
    plan = plan_entry("XRPUSDT", -1, 2_000.0, 1.50, 0.06, XRP, open_notional=0.0, config=CONFIG)
    assert plan.stop_price == Decimal("1.6200")


def test_skips_below_exchange_minimum_instead_of_upsizing():
    # $250 on BTC: target ~ 250*0.003/0.044 = $17 << 0.001 BTC (~$86)
    plan = plan_entry("BTCUSDT", 1, 250.0, 86_000.0, 1_900.0, BTC, open_notional=0.0, config=CONFIG)
    assert isinstance(plan, Skip) and plan.reason == "below_minimum"


def test_exposure_cap():
    config = replace(CONFIG, max_total_notional_pct=0.10)   # cap = $200 on $2,000
    plan = plan_entry("XRPUSDT", 1, 2_000.0, 1.50, 0.06, XRP, open_notional=150.0, config=config)
    assert isinstance(plan, Skip) and plan.reason == "exposure_cap"


def test_kill_switch_blocks_entries():
    plan = plan_entry("XRPUSDT", 1, 2_000.0, 1.50, 0.06, XRP, 0.0, CONFIG, kill_switch_tripped=True)
    assert isinstance(plan, Skip) and plan.reason == "kill_switch"


@pytest.mark.parametrize("atr", [0.0, float("nan"), -1.0])
def test_invalid_atr_is_skipped(atr):
    plan = plan_entry("XRPUSDT", 1, 2_000.0, 1.50, atr, XRP, 0.0, CONFIG)
    assert isinstance(plan, Skip) and plan.reason == "invalid_atr"
