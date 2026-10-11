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


def test_equity_every_keeps_last_point_per_bucket():
    times = pd.date_range("2026-09-24 00:00", periods=12 * 24, freq="5min", tz="UTC")   # 24 h of 5-min cycles
    eq = pd.DataFrame({"time": times, "equity": range(len(times))})
    four_h = data.equity_every(eq, "4h")
    assert list(four_h["time"].dt.strftime("%H:%M")) == ["00:00", "03:55", "07:55", "11:55", "15:55", "19:55", "23:55"]
    assert four_h["equity"].iloc[-1] == eq["equity"].iloc[-1]          # newest point = current equity
    assert len(data.equity_every(eq, "1D")) == 2                         # start + end of the day
    # Only 35 min of history (the live case right after launch): start + now -> a drawable line.
    assert len(data.equity_every(eq.iloc[:8], "4h")) == 2
    assert len(data.equity_every(eq.iloc[:1], "4h")) == 1               # a single snapshot isn't duplicated
    assert data.equity_every(eq.iloc[:0], "4h").empty


def test_time_axis_aligns_to_interval():
    first, last = pd.Timestamp("2026-09-24 18:50"), pd.Timestamp("2026-09-24 19:30")
    start, end, ticks = data.time_axis(first, last, "4h")
    assert (start, end) == (pd.Timestamp("2026-09-24 16:00"), pd.Timestamp("2026-09-25 16:00"))
    assert [t.strftime("%H:%M") for t in ticks] == ["16:00", "20:00", "00:00", "04:00", "08:00", "12:00", "16:00"]
    start, end, ticks = data.time_axis(first, last, "1D")
    assert start == pd.Timestamp("2026-09-24") and end == pd.Timestamp("2026-10-01")
    assert all(t.hour == 0 for t in ticks) and len(ticks) == 8
    # A long history is thinned to at most 12 ticks, still on day boundaries.
    _, _, ticks = data.time_axis(first, first + pd.Timedelta(days=60), "1D")
    assert len(ticks) <= data.MAX_TICKS and all(t.hour == 0 for t in ticks)


def test_latest_marks(tmp_path):
    store = StateStore("testnet", root=tmp_path)
    store.save(BotState.new("testnet", CONFIG))
    store.append_equity({"time": "2026-09-24T21:50:00+00:00", "equity": 250.0})     # older row, no marks
    assert data.latest_marks(data.load("testnet", CONFIG, root=tmp_path)) == {}
    store.append_equity({"time": "2026-09-24T21:55:00+00:00", "equity": 249.9,
                         "positions": {"XRPUSDT": {"price": 1.53, "unrealized": -0.05}}})
    assert data.latest_marks(data.load("testnet", CONFIG, root=tmp_path))["XRPUSDT"]["price"] == 1.53


def test_per_coin_cumulative_result():
    state = BotState.new("testnet", CONFIG)                     # simulated capital 250
    state.positions["XRPUSDT"] = Position("XRPUSDT", 1, "5.5", 1.5, "2026-09-24T18:50:00+00:00", "1.4")
    trades = pd.DataFrame({"symbol": ["XRPUSDT", "BNBUSDT", "BNBUSDT"], "net_pnl": [1.0, 0.5, -0.25]})
    marks = {"XRPUSDT": {"price": 1.49, "unrealized": -0.05}}
    r = data.per_coin(state, trades, marks, ["XRPUSDT", "BNBUSDT", "BTCUSDT"])
    assert r["XRPUSDT"]["total"] == pytest.approx(0.95) and r["XRPUSDT"]["pct"] == pytest.approx(0.95 / 250)
    assert r["BNBUSDT"]["total"] == pytest.approx(0.25) and r["BNBUSDT"]["n_trades"] == 2
    assert r["BTCUSDT"]["traded"] is False and r["BTCUSDT"]["pct"] is None
    assert data.per_coin(state, pd.DataFrame(), {}, ["BTCUSDT"])["BTCUSDT"]["total"] == 0.0


def test_starting_capital_simulated_or_first_snapshot():
    import pandas as pd
    from xrp_futures.config.live import config_for
    from xrp_futures.execution.state import BotState
    from xrp_futures.monitor.data import starting_capital
    eq = pd.DataFrame({"equity": [80.5, 82.0]})
    assert starting_capital(BotState.new("testnet", config_for("testnet")), eq) == 250.0
    assert starting_capital(BotState.new("production", config_for("production")), eq) == 80.5
    assert starting_capital(BotState.new("production", config_for("production")), eq.iloc[:0]) is None
