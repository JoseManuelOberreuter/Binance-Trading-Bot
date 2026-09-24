import json

import pytest

from xrp_futures.config.live import LiveConfig
from xrp_futures.execution.state import BotState, Position, StateStore

CONFIG = LiveConfig()   # simulated capital $250


def _position(**kw):
    base = dict(symbol="XRPUSDT", side=1, qty="50.0", entry_price=1.5, entry_time="2026-09-24T12:00:00+00:00",
                stop_price="1.3800", stop_client_order_id="xfb_stop_1")
    return Position(**{**base, **kw})


def test_new_state_when_no_file(tmp_path):
    state = StateStore("testnet", root=tmp_path).load(CONFIG)
    assert state.simulated_capital == 250.0 and state.positions == {} and not state.kill_switch.tripped


def test_roundtrip(tmp_path):
    store = StateStore("testnet", root=tmp_path)
    state = BotState.new("testnet", CONFIG)
    state.positions["XRPUSDT"] = _position()
    state.paused["LINKUSDT"] = "unknown position"
    state.realized_pnl = 12.5
    state.kill_switch.update(262.5)
    store.save(state)
    loaded = store.load(CONFIG)
    assert loaded == state
    assert not (tmp_path / "testnet" / "state.json.tmp").exists()   # atomic write leaves no temp file


def test_testnet_and_production_never_mix(tmp_path):
    StateStore("testnet", root=tmp_path).save(BotState.new("testnet", CONFIG))
    # A production store reads its own folder -> fresh state, never the testnet one.
    assert StateStore("production", root=tmp_path).load(CONFIG).positions == {}
    # And a state file for the wrong environment is refused.
    (tmp_path / "production").mkdir()
    (tmp_path / "production" / "state.json").write_text(
        (tmp_path / "testnet" / "state.json").read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError):
        StateStore("production", root=tmp_path).load(CONFIG)


def test_simulated_equity_follows_bot_pnl():
    state = BotState.new("testnet", CONFIG)
    state.realized_pnl = 20.0
    assert state.sizing_equity(wallet_equity=5_000.0, unrealized_pnl=-5.0) == pytest.approx(265.0)
    state.simulated_capital = None
    assert state.sizing_equity(wallet_equity=5_000.0) == 5_000.0


def test_signed_qty_and_open_notional():
    state = BotState.new("testnet", CONFIG)
    state.positions["XRPUSDT"] = _position(side=-1, qty="10.0")
    assert str(state.positions["XRPUSDT"].signed_qty) == "-10.0"
    assert state.open_notional({"XRPUSDT": 1.5}) == pytest.approx(15.0)


def test_jsonl_logs_append(tmp_path):
    store = StateStore("testnet", root=tmp_path)
    store.append_trade({"symbol": "XRPUSDT", "net_pnl": 1.0})
    store.append_trade({"symbol": "BNBUSDT", "net_pnl": -0.5})
    rows = store.read_jsonl(store.trades_path)
    assert [r["symbol"] for r in rows] == ["XRPUSDT", "BNBUSDT"]
    assert json.loads(store.trades_path.read_text(encoding="utf-8").splitlines()[0])["net_pnl"] == 1.0
