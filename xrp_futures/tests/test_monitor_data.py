import pandas as pd
import pytest

from xrp_futures.config.live import LiveConfig
from xrp_futures.execution.commands import PAUSE_BOT, CommandQueue
from xrp_futures.execution.state import BotState, Position, StateStore
from xrp_futures.monitor import data

CONFIG = LiveConfig()


def test_load_empty_environment(tmp_path):
    view = data.load("testnet", CONFIG, root=tmp_path)
    assert view.state is None and view.equity.empty and view.trades.empty and view.log_tail == []
    assert data.heartbeat_status(view.state, pd.Timestamp.now(tz="UTC")) == (False, "nunca ha corrido")


def test_load_reads_everything_the_bot_writes(tmp_path):
    store = StateStore("testnet", root=tmp_path)
    state = BotState.new("testnet", CONFIG)
    state.positions["XRPUSDT"] = Position("XRPUSDT", 1, "5.5", 1.5393, "2026-09-24T18:36:00+00:00", "1.4037", "xfb_s")
    state.last_cycle_at = "2026-09-24T18:36:00+00:00"
    store.save(state)
    store.append_equity({"time": "2026-09-24T18:36:00+00:00", "equity": 250.0})
    store.append_trade({"symbol": "BNBUSDT", "net_pnl": 0.5, "exit_time": "2026-09-24T19:00:00+00:00"})
    store.append_alert({"time": "2026-09-24T18:40:00+00:00", "symbol": None, "message": "x"})
    (store.dir / "bot.log").write_text("line1\nline2\n", encoding="utf-8")
    CommandQueue(store.dir).submit(PAUSE_BOT)

    view = data.load("testnet", CONFIG, root=tmp_path)
    assert view.state.positions["XRPUSDT"].qty == "5.5"
    assert len(view.equity) == 1 and view.trades["net_pnl"].iloc[0] == 0.5
    assert [c["kind"] for c in view.commands_pending] == [PAUSE_BOT]
    assert view.log_tail == ["line1", "line2"]
    assert data.environments(tmp_path) == ["testnet"]

    frame = data.positions_frame(view.state)
    assert frame.iloc[0]["Lado"] == "LONG"
    assert frame.iloc[0]["Distancia stop"] == pytest.approx((1.5393 - 1.4037) / 1.5393)


def test_heartbeat():
    state = BotState.new("testnet", CONFIG)
    state.last_cycle_at = "2026-09-24T18:00:00+00:00"
    assert data.heartbeat_status(state, pd.Timestamp("2026-09-24T18:06:00+00:00")) == (True, "último ciclo hace 6 min")
    assert data.heartbeat_status(state, pd.Timestamp("2026-09-24T18:30:00+00:00"))[0] is False


def test_trade_summary_ignores_unknown_pnl():
    trades = pd.DataFrame({"net_pnl": [1.0, -0.5, None, 2.0]})
    s = data.trade_summary(trades)
    assert s["n"] == 3 and s["wins"] == 2 and s["net_pnl"] == pytest.approx(2.5)
    assert data.trade_summary(pd.DataFrame())["n"] == 0
