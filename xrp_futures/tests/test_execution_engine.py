"""
End-to-end engine cycles against an in-memory fake Binance (no network).
"""

from dataclasses import replace
from decimal import Decimal

import pandas as pd
import pytest

from xrp_futures.config.live import LiveConfig
from xrp_futures.execution import commands as cmdq
from xrp_futures.execution.engine import LiveEngine, last_closed_bar_open
from xrp_futures.execution.live_signal import LiveSignal
from xrp_futures.execution.order_manager import DryRunExchange, Fill, Snapshot
from xrp_futures.execution.reconcile import ExchangeOrder, ExchangePosition
from xrp_futures.execution.state import StateStore
from xrp_futures.execution.symbol_filters import SymbolFilters

SYM = "XRPUSDT"
CONFIG = replace(LiveConfig(), universe=(SYM,), equity_override_usdt=2_000.0, risk_pct=0.003)
FILTERS = {SYM: SymbolFilters(SYM, Decimal("0.0001"), Decimal("0.1"), Decimal("0.1"), Decimal("5"))}
T0 = pd.Timestamp("2026-09-24 12:05", tz="UTC")        # 5 min after the 12:00 close -> last closed = 00:00


class FakeExchange:
    def __init__(self, price=1.50):
        self.prices = {SYM: price}
        self.position = Decimal("0")             # signed
        self.entry_price = 0.0
        self.stops: dict[str, tuple[int, Decimal, Decimal]] = {}   # cid -> (position_side, qty, price)
        self.triggered: dict[str, Fill] = {}
        self.calls: list[str] = []
        self.next_id = 1
        self.fail_stop = False
        self.margin_problem = None           # e.g. "CROSSED 20x (expected ISOLATED 3x)"
        self.lose_response = None            # exception raised AFTER an entry executes (lost response)
        self.reject_entry = None             # exception raised INSTEAD of executing (definite rejection)
        self.orders: dict[str, Fill] = {}    # client_id -> fill, for entry_status lookups
        self.order_visible = True            # False: Binance doesn't show the order yet (-2013)

    def sync_clock(self):            # a read, not an action: counted apart from `calls`
        self.clock_syncs = getattr(self, "clock_syncs", 0) + 1
        return 0

    def ensure_margin(self, symbol, leverage):
        return self.margin_problem

    def entry_status(self, symbol, client_id):
        return self.orders.get(client_id) if self.order_visible else None

    def snapshot(self):
        positions = {}
        if self.position != 0:
            upnl = float(self.position) * (self.prices[SYM] - self.entry_price)
            positions[SYM] = ExchangePosition(SYM, self.position, self.entry_price, upnl)
        stops = [ExchangeOrder(SYM, cid, "STOP_MARKET", float(p)) for cid, (_, _, p) in self.stops.items()]
        return Snapshot(positions, stops, wallet_equity=5_000.0)

    def mark_price(self, symbol):
        return self.prices[symbol]

    def _fill(self, qty, realized=0.0):
        self.next_id += 1
        price = self.prices[SYM]
        return Fill(self.next_id, price, qty, commission=float(qty) * price * 0.0005, realized_pnl=realized,
                    time_ms=int(T0.value // 10**6))

    def market_order(self, symbol, side, qty, reduce_only, client_id=None):
        if not reduce_only and self.reject_entry:
            raise self.reject_entry
        self.calls.append(f"market {side} {qty} reduce={reduce_only}")
        realized = 0.0
        if reduce_only:
            realized = float(self.position) * (self.prices[SYM] - self.entry_price)
            self.position = Decimal("0")
        else:
            self.position, self.entry_price = side * qty, self.prices[SYM]
        fill = self._fill(qty, realized)
        if client_id:
            self.orders[client_id] = fill
        if not reduce_only and self.lose_response:
            raise self.lose_response
        return fill

    def place_stop(self, symbol, position_side, qty, stop_price):
        if self.fail_stop:
            raise RuntimeError("rejected")
        cid = f"xfb_s_{SYM}_{self.next_id}"
        self.next_id += 1
        self.stops[cid] = (position_side, qty, stop_price)
        self.calls.append(f"stop {stop_price}")
        return cid

    def cancel_stop(self, symbol, cid):
        self.calls.append(f"cancel {cid}")
        self.stops.pop(cid, None)

    def trigger_stop(self, price):
        """Price hits the stop while the bot is not running."""
        (cid, (side, qty, _)), = self.stops.items()
        self.prices[SYM] = price
        realized = float(self.position) * (price - self.entry_price)
        self.position = Decimal("0")
        del self.stops[cid]
        self.triggered[cid] = self._fill(qty, realized)

    def stop_fill(self, symbol, cid):
        return self.triggered.get(cid)

    def funding_since(self, symbol, since_ms):
        return -0.01


def _signal(side, bar_open, atr=0.06, ch_long=1.30, ch_short=1.70):
    return LiveSignal(SYM, bar_open, close=1.5, signal=side, atr=atr, chandelier_long=ch_long, chandelier_short=ch_short)


def _engine(tmp_path, ex, sig_holder, dry_run=False, config=CONFIG):
    return LiveEngine(config, ex, lambda s: sig_holder["sig"], FILTERS, StateStore("testnet", root=tmp_path),
                      dry_run=dry_run)


def test_last_closed_bar_open():
    assert last_closed_bar_open(T0) == pd.Timestamp("2026-09-24 00:00", tz="UTC")
    assert last_closed_bar_open(pd.Timestamp("2026-09-24 11:59", tz="UTC")) == pd.Timestamp("2026-09-23 12:00", tz="UTC")


def test_entry_places_stop_and_persists(tmp_path):
    ex, sig = FakeExchange(), {"sig": _signal(1, last_closed_bar_open(T0))}
    state = _engine(tmp_path, ex, sig).cycle(T0)
    pos = state.positions[SYM]
    # 2000 * 0.3% / (2*0.06/1.5 = 8%) = 75 USDT -> 50 XRP; stop 1.50 - 0.12
    assert pos.qty == "50.0" and pos.stop_price == "1.3800"
    assert ex.position == Decimal("50.0") and len(ex.stops) == 1
    reloaded = StateStore("testnet", root=tmp_path).load(CONFIG)
    assert reloaded.positions[SYM].stop_client_order_id in ex.stops
    assert reloaded.last_processed_bar == last_closed_bar_open(T0).isoformat()


def test_same_candle_is_not_processed_twice(tmp_path):
    ex, sig = FakeExchange(), {"sig": _signal(1, last_closed_bar_open(T0))}
    eng = _engine(tmp_path, ex, sig)
    eng.cycle(T0)
    n_calls = len(ex.calls)
    eng.cycle(T0 + pd.Timedelta(minutes=5))
    assert len(ex.calls) == n_calls


def test_stale_candle_is_retried_not_marked(tmp_path):
    ex = FakeExchange()
    sig = {"sig": _signal(1, last_closed_bar_open(T0) - pd.Timedelta("12h"))}   # Binance not updated yet
    state = _engine(tmp_path, ex, sig).cycle(T0)
    assert state.positions == {} and state.last_processed_bar is None


def test_trailing_places_new_stop_before_cancelling_old(tmp_path):
    ex, sig = FakeExchange(), {"sig": _signal(1, last_closed_bar_open(T0))}
    eng = _engine(tmp_path, ex, sig)
    eng.cycle(T0)
    old = next(iter(ex.stops))
    ex.prices[SYM] = 1.60
    t1 = T0 + pd.Timedelta("12h")
    sig["sig"] = _signal(1, last_closed_bar_open(t1), ch_long=1.45)
    state = eng.cycle(t1)
    assert state.positions[SYM].stop_price == "1.4500"
    assert list(ex.stops) == [state.positions[SYM].stop_client_order_id]
    assert ex.calls.index("stop 1.4500") < ex.calls.index(f"cancel {old}")


def test_trailing_never_loosens(tmp_path):
    ex, sig = FakeExchange(), {"sig": _signal(1, last_closed_bar_open(T0))}
    eng = _engine(tmp_path, ex, sig)
    eng.cycle(T0)
    t1 = T0 + pd.Timedelta("12h")
    sig["sig"] = _signal(1, last_closed_bar_open(t1), ch_long=1.20)    # lower than 1.38
    assert eng.cycle(t1).positions[SYM].stop_price == "1.3800"


def test_signal_flip_closes_then_reverses(tmp_path):
    ex, sig = FakeExchange(), {"sig": _signal(1, last_closed_bar_open(T0))}
    eng = _engine(tmp_path, ex, sig)
    eng.cycle(T0)
    ex.prices[SYM] = 1.56
    t1 = T0 + pd.Timedelta("12h")
    sig["sig"] = _signal(-1, last_closed_bar_open(t1))
    state = eng.cycle(t1)
    assert state.positions[SYM].side == -1 and ex.position < 0
    trade = eng.store.read_jsonl(eng.store.trades_path)[0]
    assert trade["reason"] == "signal" and trade["side"] == "LONG"
    assert trade["net_pnl"] == pytest.approx(50 * 0.06 - trade["commission"] - 0.01)
    assert state.realized_pnl == pytest.approx(trade["net_pnl"])


def test_stop_filled_while_away_is_recorded_and_reentry_follows(tmp_path):
    ex, sig = FakeExchange(), {"sig": _signal(1, last_closed_bar_open(T0))}
    eng = _engine(tmp_path, ex, sig)
    eng.cycle(T0)
    ex.trigger_stop(1.38)
    state = eng.cycle(T0 + pd.Timedelta(minutes=5))      # same candle: only reconcile runs
    assert SYM not in state.positions
    assert eng.store.read_jsonl(eng.store.trades_path)[0]["reason"] == "stop"
    assert state.realized_pnl < 0
    t1 = T0 + pd.Timedelta("12h")
    sig["sig"] = _signal(1, last_closed_bar_open(t1))    # signal still LONG -> re-enter, like the backtest
    assert SYM in eng.cycle(t1).positions


def test_failed_stop_closes_position_and_pauses_symbol(tmp_path):
    ex, sig = FakeExchange(), {"sig": _signal(1, last_closed_bar_open(T0))}
    ex.fail_stop = True
    state = _engine(tmp_path, ex, sig).cycle(T0)
    assert ex.position == 0 and SYM not in state.positions and SYM in state.paused


def test_dry_run_sends_nothing_and_saves_nothing(tmp_path):
    ex, sig = FakeExchange(), {"sig": _signal(1, last_closed_bar_open(T0))}
    dry = DryRunExchange(ex)
    _engine(tmp_path, dry, sig, dry_run=True).cycle(T0)
    assert ex.calls == [] and ex.position == 0
    assert any("STOP_MARKET" in w for w in dry.writes)
    assert not (tmp_path / "testnet" / "state.json").exists()


def test_kill_switch_blocks_entries(tmp_path):
    ex, sig = FakeExchange(), {"sig": _signal(1, last_closed_bar_open(T0))}
    store = StateStore("testnet", root=tmp_path)
    state = store.load(CONFIG)
    state.kill_switch.update(2_000)
    state.kill_switch.update(1_300)
    store.save(state)
    assert _engine(tmp_path, ex, sig).cycle(T0).positions == {}


def test_commands_pause_close_and_reset(tmp_path):
    ex, sig = FakeExchange(), {"sig": _signal(1, last_closed_bar_open(T0))}
    eng = _engine(tmp_path, ex, sig)
    eng.cycle(T0)
    q = cmdq.CommandQueue(eng.store.dir)
    q.submit(cmdq.PAUSE_BOT)
    q.submit(cmdq.CLOSE_POSITION, SYM)
    state = eng.cycle(T0 + pd.Timedelta(minutes=5))
    assert state.bot_paused and SYM not in state.positions and ex.position == 0 and ex.stops == {}
    assert [r["ok"] for r in q.history()] == [True, True]
    # Paused bot: a new candle with a LONG signal does not re-enter.
    t1 = T0 + pd.Timedelta("12h")
    sig["sig"] = _signal(1, last_closed_bar_open(t1))
    assert eng.cycle(t1).positions == {}
    q.submit(cmdq.RESUME_BOT)
    q.submit(cmdq.RESET_KILL_SWITCH)
    state = eng.cycle(t1 + pd.Timedelta(minutes=5))
    assert not state.bot_paused and not state.kill_switch.tripped


def test_unknown_position_pauses_symbol_and_is_left_alone(tmp_path):
    ex, sig = FakeExchange(), {"sig": _signal(1, last_closed_bar_open(T0))}
    ex.position, ex.entry_price = Decimal("-10"), 1.5      # e.g. a manual trade by the user
    state = _engine(tmp_path, ex, sig).cycle(T0)
    assert SYM in state.paused and ex.position == Decimal("-10") and ex.calls == []


def test_engine_emits_activity_events(tmp_path):
    from xrp_futures.execution import events as ev
    ex, sig = FakeExchange(), {"sig": _signal(1, last_closed_bar_open(T0))}
    eng = _engine(tmp_path, ex, sig)
    eng.cycle(T0)                                              # entry
    ex.prices[SYM] = 1.60
    t1 = T0 + pd.Timedelta("12h")
    sig["sig"] = _signal(1, last_closed_bar_open(t1), ch_long=1.45)
    eng.cycle(t1)                                              # trail
    t2 = t1 + pd.Timedelta("12h")
    sig["sig"] = _signal(0, last_closed_bar_open(t2))
    eng.cycle(t2)                                              # exit (signal flat)
    actions = [e for e in eng.events.read() if e["type"] != ev.STATUS]   # periodic summaries tested separately
    kinds = [e["type"] for e in actions]
    assert kinds == [ev.POSITION_OPENED, ev.CANDLE_PROCESSED, ev.STOP_MOVED, ev.CANDLE_PROCESSED,
                     ev.POSITION_CLOSED, ev.CANDLE_PROCESSED]
    opened, _, moved, _, closed, last = actions
    assert opened["risk_usdt"] == pytest.approx(2_000 * 0.003, rel=1e-6)
    assert moved["old_stop"] == 1.38 and moved["new_stop"] == 1.45
    assert moved["locked_pnl"] == pytest.approx(50 * (1.45 - 1.50))
    assert closed["reason"] == "signal" and closed["net_pnl"] > 0
    assert last["actions"] == {"opened": 0, "closed": 1, "stops_moved": 0, "skipped": 0}


def test_skipped_entry_event_once_per_candle_and_dry_run_records_nothing(tmp_path):
    from xrp_futures.execution import events as ev
    config = replace(CONFIG, equity_override_usdt=10.0)        # too small for the 5 USDT minimum
    ex, sig = FakeExchange(), {"sig": _signal(1, last_closed_bar_open(T0))}
    eng = _engine(tmp_path, ex, sig, config=config)
    eng.cycle(T0)
    eng.cycle(T0 + pd.Timedelta(minutes=5))                    # same candle: no repeat
    skipped = [e for e in eng.events.read() if e["type"] == ev.ENTRY_SKIPPED]
    assert len(skipped) == 1 and skipped[0]["reason"] == "below_minimum"
    dry = _engine(tmp_path / "dry", FakeExchange(), sig, dry_run=True)
    dry.cycle(T0)
    assert dry.events.read() == []


def test_equity_log_records_live_price_and_pnl_per_position(tmp_path):
    ex, sig = FakeExchange(), {"sig": _signal(1, last_closed_bar_open(T0))}
    eng = _engine(tmp_path, ex, sig)
    eng.cycle(T0)
    ex.prices[SYM] = 1.56                                        # +0.06 on 50 XRP
    eng.cycle(T0 + pd.Timedelta(minutes=5))
    last = eng.store.read_jsonl(eng.store.equity_path)[-1]
    assert last["positions"][SYM]["price"] == pytest.approx(1.56)
    assert last["positions"][SYM]["unrealized"] == pytest.approx(50 * 0.06)


def test_status_summary_every_30_minutes(tmp_path):
    from xrp_futures.execution import events as ev
    ex, sig = FakeExchange(), {"sig": _signal(1, last_closed_bar_open(T0))}
    eng = _engine(tmp_path, ex, sig)
    for minutes in range(0, 65, 5):                              # 13 cycles, 5 min apart
        eng.cycle(T0 + pd.Timedelta(minutes=minutes))
    statuses = [e for e in eng.events.read() if e["type"] == ev.STATUS]
    assert len(statuses) == 3                                    # at 0, 30 and 60 min
    assert statuses[0]["market_up"] == 1 and statuses[0]["market_total"] == 1
    assert statuses[0]["open_positions"] == 1


# ---- fatal-error review (2026-10-11) -------------------------------------------------
def test_lost_entry_response_is_adopted_with_its_stop(tmp_path):
    """Timeout after the order executed: the position must get its stop, not be 'unknown'."""
    ex, sig = FakeExchange(), {"sig": _signal(1, last_closed_bar_open(T0))}
    ex.lose_response = TimeoutError("read timed out")
    state = _engine(tmp_path, ex, sig).cycle(T0)
    assert ex.position == Decimal("50.0")
    assert SYM in state.positions and SYM not in state.paused and not state.pending_entries
    assert list(ex.stops) == [state.positions[SYM].stop_client_order_id]


def test_entry_invisible_at_first_is_adopted_next_cycle(tmp_path):
    ex, sig = FakeExchange(), {"sig": _signal(1, last_closed_bar_open(T0))}
    ex.lose_response, ex.order_visible = TimeoutError("read timed out"), False
    eng = _engine(tmp_path, ex, sig)
    state = eng.cycle(T0)
    assert SYM in state.pending_entries and SYM not in state.positions
    assert SYM not in state.paused                  # NOT treated as a foreign position
    ex.lose_response, ex.order_visible = None, True
    state = eng.cycle(T0 + pd.Timedelta(minutes=5))
    assert not state.pending_entries and SYM not in state.paused
    assert list(ex.stops) == [state.positions[SYM].stop_client_order_id]


def test_pending_entry_that_never_executed_is_dropped_after_grace(tmp_path):
    ex, sig = FakeExchange(), {"sig": _signal(1, last_closed_bar_open(T0))}
    ex.reject_entry, ex.order_visible = TimeoutError("read timed out"), False
    eng = _engine(tmp_path, ex, sig)
    assert SYM in eng.cycle(T0).pending_entries
    assert SYM in eng.cycle(T0 + pd.Timedelta(minutes=1)).pending_entries    # still within the grace period
    state = eng.cycle(T0 + pd.Timedelta(minutes=5))
    assert not state.pending_entries and not state.positions and ex.position == 0


def test_definite_rejection_is_not_left_pending(tmp_path):
    from binance.exceptions import BinanceAPIException
    resp = type("R", (), {"text": ""})()
    ex, sig = FakeExchange(), {"sig": _signal(1, last_closed_bar_open(T0))}
    ex.reject_entry = BinanceAPIException(resp, 400, '{"code": -2019, "msg": "Margin is insufficient."}')
    state = _engine(tmp_path, ex, sig).cycle(T0)
    assert not state.pending_entries and not state.positions
    assert state.last_processed_bar is not None     # an entry failure doesn't stall the candle


def test_entry_blocked_unless_isolated_at_bot_leverage(tmp_path):
    ex, sig = FakeExchange(), {"sig": _signal(1, last_closed_bar_open(T0))}
    ex.margin_problem = "CROSSED 20x (expected ISOLATED 3x)"
    eng = _engine(tmp_path, ex, sig)
    state = eng.cycle(T0)
    assert not state.positions and ex.calls == []
    alerts = StateStore("testnet", root=tmp_path).read_jsonl(StateStore("testnet", root=tmp_path).alerts_path)
    assert "CROSSED 20x" in alerts[-1]["message"]


def test_one_symbol_failing_does_not_block_the_others(tmp_path):
    other = "ADAUSDT"
    config = replace(CONFIG, universe=(SYM, other))
    filters = {**FILTERS, other: SymbolFilters(other, Decimal("0.0001"), Decimal("1"), Decimal("1"), Decimal("5"))}

    class TwoCoins(FakeExchange):
        def mark_price(self, symbol):
            if symbol == SYM:
                raise ConnectionError("XRP endpoint down")
            return 1.50

    ex = TwoCoins()
    sigs = {SYM: _signal(1, last_closed_bar_open(T0)),
            other: LiveSignal(other, last_closed_bar_open(T0), close=1.5, signal=1, atr=0.06,
                              chandelier_long=1.3, chandelier_short=1.7)}
    eng = LiveEngine(config, ex, lambda s: sigs[s], filters, StateStore("testnet", root=tmp_path))
    state = eng.cycle(T0)
    assert other in state.positions and SYM not in state.positions


def test_failed_exit_leaves_candle_for_retry(tmp_path):
    ex, sig = FakeExchange(), {"sig": _signal(1, last_closed_bar_open(T0))}
    eng = _engine(tmp_path, ex, sig)
    eng.cycle(T0)
    t1 = T0 + pd.Timedelta("12h")
    sig["sig"] = _signal(-1, last_closed_bar_open(t1))
    real_market = ex.market_order
    ex.market_order = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down"))
    state = eng.cycle(t1)
    assert state.last_processed_bar == last_closed_bar_open(T0).isoformat()   # not advanced
    assert SYM in state.positions and len(ex.stops) == 1                       # stop still protects
    ex.market_order = real_market
    state = eng.cycle(t1 + pd.Timedelta(minutes=5))
    assert state.last_processed_bar == last_closed_bar_open(t1).isoformat()
    assert state.positions[SYM].side == -1                                     # exit + reversal done


def test_clock_is_synced_every_cycle(tmp_path):
    ex, sig = FakeExchange(), {"sig": _signal(0, last_closed_bar_open(T0))}
    eng = _engine(tmp_path, ex, sig)
    eng.cycle(T0)
    eng.cycle(T0 + pd.Timedelta(minutes=5))
    assert ex.clock_syncs == 2


def test_set_capital_command_resizes_next_entries(tmp_path):
    ex, sig = FakeExchange(), {"sig": _signal(0, last_closed_bar_open(T0))}
    eng = _engine(tmp_path, ex, sig)
    eng.cycle(T0)
    cmdq.CommandQueue(StateStore("testnet", root=tmp_path).dir).submit(cmdq.SET_CAPITAL, value=1_000.0)
    t1 = T0 + pd.Timedelta("12h")
    sig["sig"] = _signal(1, last_closed_bar_open(t1))
    state = eng.cycle(t1)
    assert state.assigned_capital == 1_000.0
    assert state.positions[SYM].qty == "25.0"            # half of the $2,000 case: 1000*0.3%/8% = $37.5 -> 25 XRP


def test_set_capital_validation():
    import pytest as _pytest
    q = cmdq.CommandQueue
    with _pytest.raises(ValueError):
        q.__new__(q).submit(cmdq.SET_CAPITAL, value=0)


def test_one_minute_cycles_keep_equity_log_at_five_minutes(tmp_path):
    ex, sig = FakeExchange(), {"sig": _signal(0, last_closed_bar_open(T0))}
    eng = _engine(tmp_path, ex, sig)
    for minute in range(11):                         # 11 one-minute cycles: 0..10
        eng.cycle(T0 + pd.Timedelta(minutes=minute))
    times = [r["time"] for r in eng.store.read_jsonl(eng.store.equity_path)]
    assert len(times) == 3                           # minutes 0, 5 and 10
    assert ex.clock_syncs == 11                      # but every cycle still runs


def test_default_cycle_is_one_minute():
    from xrp_futures.config.live import config_for
    assert config_for("testnet").cycle_seconds == config_for("production").cycle_seconds == 60
