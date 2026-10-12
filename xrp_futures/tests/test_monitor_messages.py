from datetime import timedelta, timezone

import pandas as pd
import pytest

from xrp_futures.config.live import LiveConfig
from xrp_futures.execution import commands as cmdq
from xrp_futures.execution import events as ev
from xrp_futures.execution.state import BotState, Position
from xrp_futures.monitor import messages as m

TZ = timezone(timedelta(hours=-3))
T = "2026-09-24T21:50:00+00:00"


def _render(kind, **data):
    return m.render({"time": T, "type": kind, **data}, TZ)


def test_no_emojis():
    samples = [
        _render(ev.BOT_STARTED, environment="testnet", simulated_capital=250, interval_s=300),
        _render(ev.POSITION_OPENED, symbol="XRPUSDT", side=1, qty="5.5", price=1.5384, notional=8.46,
                stop=1.4028, stop_pct=0.0881, risk_usdt=0.7458, equity=250.0),
        _render(ev.KILL_SWITCH, equity=170, drawdown=-0.305),
        _render(ev.ALERT, symbol="LINKUSDT", code="UNKNOWN_POSITION", message="raw"),
        _render(ev.COMMAND_APPLIED, command=cmdq.PAUSE_BOT, symbol=None, ok=True),
    ]
    for msg in samples:
        assert all(ord(ch) < 0x2000 or ch in "—−" for ch in msg.plain()), msg.plain()


def test_position_opened():
    msg = _render(ev.POSITION_OPENED, symbol="XRPUSDT", side=1, qty="5.5", price=1.5384, notional=8.46,
                  stop=1.4028, stop_pct=0.0881, risk_usdt=0.7458, equity=250.0)
    assert msg.text == "Abrí LONG en XRP: 5.5 a 1.5384."
    assert msg.detail == "Stop en 1.4028 (−8.8%), riesgo 0.75 (0.3% del capital)."
    short = _render(ev.POSITION_OPENED, symbol="LINKUSDT", side=-1, qty="1", price=12.2, notional=12.2,
                    stop=13.0, stop_pct=0.066, risk_usdt=0.8, equity=250)
    assert short.text.startswith("Abrí SHORT en LINK") and "(+6.6%)" in short.detail


def test_stop_moves():
    up = _render(ev.STOP_MOVED, symbol="SOLUSDT", side=1, old_stop=109.44, new_stop=118.0, locked_pnl=0.09)
    assert up.plain() == "Subí el stop de SOL de 109.44 a 118.00. Ahora aseguro +0.09 aunque el precio se dé vuelta."
    down = _render(ev.STOP_MOVED, symbol="LINKUSDT", side=-1, old_stop=14.0, new_stop=13.5, locked_pnl=-0.3)
    assert down.text.startswith("Bajé") and down.detail == "Si salta, la pérdida máxima baja a 0.30."


@pytest.mark.parametrize("reason, pnl, pct, level, expected", [
    ("signal", 0.42, 0.027, "good", "Cerré BNB (LONG) por cambio de señal: +0.42 (+2.7%). Duró 3 días."),
    ("stop", -0.75, -0.048, "bad", "Saltó el stop de BNB a 742.97: −0.75 (−4.8%). Duró 3 días."),
    ("manual", 0.10, 0.006, "good", "Cerré BNB a pedido tuyo: +0.10 (+0.6%). Duró 3 días."),
])
def test_position_closed(reason, pnl, pct, level, expected):
    msg = _render(ev.POSITION_CLOSED, symbol="BNBUSDT", side=1, reason=reason, entry_price=780.32,
                  exit_price=742.97, net_pnl=pnl, pnl_pct=pct, hours=72)
    assert msg.level == level and msg.plain() == expected
    assert m.AMOUNT in msg.text     # the dashboard colors only the amount


def test_closed_with_unknown_pnl():
    msg = _render(ev.POSITION_CLOSED, symbol="ADAUSDT", side=1, reason="stop", entry_price=0.25, exit_price=None,
                  net_pnl=None, pnl_pct=None, hours=None)
    assert msg.level == "info" and "resultado desconocido" in msg.plain()


def test_entry_skipped():
    msg = _render(ev.ENTRY_SKIPPED, symbol="BTCUSDT", side=1, reason="below_minimum", target_usdt=17.83,
                  limit_usdt=84.43)
    assert msg.plain() == ("BTC tiene señal LONG, pero no entré. La orden mínima de Binance (84.43) supera lo "
                           "que corresponde según el riesgo (17.83).")
    assert _render(ev.ENTRY_SKIPPED, symbol="XRPUSDT", side=1, reason="bot_paused").detail == "El bot está en pausa."


def test_candle_processed_uses_local_time():
    msg = _render(ev.CANDLE_PROCESSED, bar_open_time="2026-09-24T12:00:00+00:00",
                  bar_close_time="2026-09-25T00:00:00+00:00",
                  signals={"A": 1, "B": 1, "C": -1}, actions={"opened": 0, "closed": 1, "stops_moved": 2, "skipped": 0})
    assert msg.plain() == "Cerró la vela de las 21:00. Señales: 2 alcistas, 1 bajistas. Acciones: cerré 1, moví 2 stops."
    quiet = _render(ev.CANDLE_PROCESSED, bar_open_time="x", bar_close_time="2026-09-25T00:00:00+00:00",
                    signals={"A": 1}, actions={"opened": 0, "closed": 0, "stops_moved": 0, "skipped": 0})
    assert quiet.detail.endswith("Sin cambios.")


def test_commands_alerts_kill_switch_errors():
    assert _render(ev.COMMAND_APPLIED, command=cmdq.PAUSE_BOT, symbol=None, ok=True).text.startswith("Pausaste el bot")
    assert _render(ev.COMMAND_APPLIED, command=cmdq.CLOSE_POSITION, symbol="XRPUSDT", ok=False).level == "warn"
    alert = _render(ev.ALERT, symbol="LINKUSDT", code="UNKNOWN_POSITION", message="raw")
    assert alert.text.startswith("Aviso. LINK: Hay una posición en Binance que el bot no abrió")
    assert "cayó 30.5%" in _render(ev.KILL_SWITCH, equity=170, drawdown=-0.305).text
    err = _render(ev.CYCLE_ERROR, error="ConnectionError: timeout", retry_s=300)
    assert "reintento en 5 min" in err.text and err.level == "warn"


def test_standby_line():
    state = BotState.new("testnet", LiveConfig())
    state.positions["XRPUSDT"] = Position("XRPUSDT", 1, "5.5", 1.5, "2026-09-24T18:50:00+00:00", "1.4")
    now = pd.Timestamp("2026-09-24T21:47:00+00:00")
    line = m.standby_line(state, -0.18, {"signals": {"A": 1, "B": 1, "C": -1}}, True, now, TZ)
    assert line == ("En espera. Próxima vela a las 21:00, en 2h 13m. 1 posición abierta. "
                    "No realizado −0.18. Mercado: 2 de 3 alcistas.")
    state.bot_paused = True
    assert m.standby_line(state, 0.0, None, True, now, TZ).startswith("En pausa")
    assert m.standby_line(state, 0.0, None, False, now, TZ).startswith("El bot no responde")


def test_status_summary():
    msg = _render(ev.STATUS, equity=249.77, realized_pnl=0.0, unrealized_pnl=-0.23, open_positions=4,
                  market_up=7, market_down=1, market_total=8, bot_paused=False, kill_switch=False)
    assert msg.plain() == ("Sigo activo. Mercado: 7 de 8 al alza. 4 posiciones abiertas, no realizado −0.23. "
                           "Capital 249.77.")
    quiet = _render(ev.STATUS, equity=250, unrealized_pnl=0, open_positions=0, market_total=0,
                    bot_paused=True, kill_switch=False)
    assert quiet.plain() == "Sigo activo, en pausa: no abro operaciones nuevas. Sin posiciones abiertas. Capital 250.00."


def test_set_capital_message():
    from datetime import timezone
    from xrp_futures.execution import commands as cmdq
    from xrp_futures.monitor import messages as m
    e = {"time": "2026-10-11T12:00:00+00:00", "type": "COMMAND_APPLIED", "command": cmdq.SET_CAPITAL, "ok": True}
    assert "100.00" in m.render({**e, "value": 100.0}, timezone.utc).text
    assert "todo el saldo" in m.render({**e, "value": None}, timezone.utc).text


def test_set_risk_message():
    from datetime import timezone
    from xrp_futures.execution import commands as cmdq
    from xrp_futures.monitor import messages as m
    e = {"time": "2026-10-11T12:00:00+00:00", "type": "COMMAND_APPLIED", "command": cmdq.SET_RISK, "ok": True}
    assert "1.5%" in m.render({**e, "value": 0.015}, timezone.utc).text
    assert "por defecto" in m.render({**e, "value": None}, timezone.utc).text
