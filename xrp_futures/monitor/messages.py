"""
Plain-language (Spanish) rendering of bot events for the dashboard's activity feed
(Phase 5b, docs/execution_plan.md decisions 25-26). Pure functions, no I/O.

Editorial style (user-chosen): no emojis, short sentences, a headline plus an optional
detail line; the only color is on money amounts (gain/loss), which the dashboard
applies through `Message.amount` / `Message.level`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import tzinfo

import pandas as pd

from xrp_futures.execution import commands as cmdq
from xrp_futures.execution import events as ev
from xrp_futures.execution.state import BotState

AMOUNT = "{amount}"   # placeholder in Message.text where the (colored) amount goes


@dataclass(frozen=True)
class Message:
    time: pd.Timestamp
    text: str                   # headline; may contain AMOUNT
    detail: str = ""            # optional second line
    amount: str | None = None   # e.g. "+0.42 (+2.7%)"
    level: str = "info"         # "info" | "good" | "bad" | "warn"

    def plain(self) -> str:
        head = self.text.replace(AMOUNT, self.amount or "")
        return f"{head} {self.detail}".strip()


def coin(symbol: str | None) -> str:
    return (symbol or "").replace("USDT", "")


def num(x: float | None) -> str:
    """Price with sensible precision, no currency sign."""
    if x is None:
        return "—"
    if x >= 100:
        return f"{x:,.2f}"
    if 0 < abs(x) < 0.01:
        return f"{x:.4g}" if abs(x) >= 1e-4 else f"{x:.8f}".rstrip("0")   # meme-coin prices: keep 4 significant digits
    return f"{x:,.4f}"


def money(x: float | None, sign: bool = False) -> str:
    if x is None:
        return "—"
    s = "+" if sign and x >= 0 else "−" if x < 0 else ""
    return f"{s}{abs(x):,.2f}"


def pct(x: float, decimals: int = 1) -> str:
    return f"{'+' if x >= 0 else '−'}{abs(x):.{decimals}%}"


def duration(hours: float | None) -> str:
    if hours is None:
        return ""
    if hours < 48:
        return f"{hours:.0f} h"
    return f"{hours / 24:.0f} días"


def side_name(side: int) -> str:
    return "LONG" if side == 1 else "SHORT"


def local_hhmm(iso: str, tz: tzinfo) -> str:
    return pd.Timestamp(iso).tz_convert(tz).strftime("%H:%M")


SKIP_REASONS = {
    "kill_switch": "El kill-switch está activo.",
    "bot_paused": "El bot está en pausa.",
    "invalid_atr": "Faltan datos de volatilidad.",
    "exposure_cap": "Superaría el tope de exposición total.",
}

ALERTS = {
    "STOP_REPLACED": "La posición no tenía stop en Binance; lo volví a poner.",
    "STOP_FAILED": "No pude poner el stop después de entrar; cerré la posición y pausé la moneda.",
    "STOP_CROSSED": "Faltaba el stop y el precio ya lo había cruzado; cerré la posición a mercado.",
    "CLOSED_EXTERNALLY": "La posición se cerró en Binance, pero no por el stop del bot. No conozco el resultado exacto.",
    "UNKNOWN_POSITION": "Hay una posición en Binance que el bot no abrió. No la toco; pausé la moneda hasta que la revises.",
    "QTY_MISMATCH": "La cantidad en Binance no coincide con la del bot. Pausé la moneda hasta que la revises.",
}

COMMANDS = {
    cmdq.PAUSE_BOT: "Pausaste el bot. No abrirá operaciones nuevas; los stops siguen activos.",
    cmdq.RESUME_BOT: "Reanudaste el bot.",
    cmdq.CLOSE_POSITION: "Pediste cerrar {coin}.",
    cmdq.PAUSE_SYMBOL: "Pausaste {coin}. El bot deja de gestionarla; su stop sigue en Binance.",
    cmdq.RESUME_SYMBOL: "Reanudaste {coin}.",
    cmdq.RESET_KILL_SWITCH: "Reactivaste el kill-switch. El bot vuelve a abrir operaciones.",
}


def render(e: dict, tz: tzinfo) -> Message:
    t = pd.Timestamp(e["time"])
    kind = e["type"]

    if kind == ev.BOT_STARTED:
        where = "producción (dinero real)" if e.get("environment") == "production" else "testnet"
        cap = e.get("simulated_capital")
        return Message(t, f"Bot iniciado en {where}" + (f" con {money(cap)} simulados." if cap else "."),
                       f"Reviso el mercado cada {int(e.get('interval_s', 300)) // 60} min.")

    if kind == ev.CANDLE_PROCESSED:
        sigs = list(e.get("signals", {}).values())
        up, down, flat = sigs.count(1), sigs.count(-1), sigs.count(0)
        trend = f"{up} alcistas, {down} bajistas" + (f", {flat} neutras" if flat else "")
        a = e.get("actions", {})
        done = [f"abrí {a['opened']}" if a.get("opened") else "",
                f"cerré {a['closed']}" if a.get("closed") else "",
                f"moví {a['stops_moved']} stop{'s' if a.get('stops_moved', 0) > 1 else ''}" if a.get("stops_moved") else "",
                f"omití {a['skipped']}" if a.get("skipped") else ""]
        done = [d for d in done if d]
        actions = ("Acciones: " + ", ".join(done) + ".") if done else "Sin cambios."
        return Message(t, f"Cerró la vela de las {local_hhmm(e['bar_close_time'], tz)}.",
                       f"Señales: {trend}. {actions}")

    if kind == ev.POSITION_OPENED:
        risk = f"riesgo {money(e['risk_usdt'])}"
        if e.get("equity"):
            risk += f" ({e['risk_usdt'] / e['equity']:.1%} del capital)"
        stop_move = -e["stop_pct"] if e["side"] == 1 else e["stop_pct"]
        return Message(t, f"Abrí {side_name(e['side'])} en {coin(e['symbol'])}: {e['qty']} a {num(e['price'])}.",
                       f"Stop en {num(e['stop'])} ({pct(stop_move)}), {risk}.")

    if kind == ev.STOP_MOVED:
        verb = "Subí" if e["side"] == 1 else "Bajé"
        locked = e.get("locked_pnl")
        detail = ""
        if locked is not None:
            detail = (f"Ahora aseguro {money(locked, sign=True)} aunque el precio se dé vuelta." if locked >= 0
                      else f"Si salta, la pérdida máxima baja a {money(abs(locked))}.")
        return Message(t, f"{verb} el stop de {coin(e['symbol'])} de {num(e['old_stop'])} a {num(e['new_stop'])}.",
                       detail)

    if kind == ev.POSITION_CLOSED:
        pnl, p = e.get("net_pnl"), e.get("pnl_pct")
        amount = (money(pnl, sign=True) + (f" ({pct(p)})" if p is not None else "")) if pnl is not None \
            else "resultado desconocido"
        took = f"Duró {duration(e.get('hours'))}." if e.get("hours") is not None else ""
        c, reason = coin(e["symbol"]), e.get("reason")
        if reason == "signal":
            head = f"Cerré {c} ({side_name(e['side'])}) por cambio de señal: {AMOUNT}."
        elif reason == "stop":
            head = f"Saltó el stop de {c} a {num(e.get('exit_price'))}: {AMOUNT}."
        elif reason == "manual":
            head = f"Cerré {c} a pedido tuyo: {AMOUNT}."
        else:
            head = f"Cerré {c} porque no pude protegerla con un stop: {AMOUNT}."
        level = "good" if (pnl or 0) > 0 else "bad" if pnl is not None and pnl < 0 else "info"
        return Message(t, head, took, amount, level)

    if kind == ev.ENTRY_SKIPPED:
        c, s, reason = coin(e["symbol"]), side_name(e["side"]), e.get("reason")
        if reason == "below_minimum":
            why = (f"La orden mínima de Binance ({money(e.get('limit_usdt'))}) supera lo que corresponde "
                   f"según el riesgo ({money(e.get('target_usdt'))}).")
        else:
            why = SKIP_REASONS.get(reason, str(reason))
        return Message(t, f"{c} tiene señal {s}, pero no entré.", why)

    if kind == ev.COMMAND_APPLIED:
        if not e.get("ok", True):
            return Message(t, f"No se pudo aplicar tu comando ({e.get('command')} {coin(e.get('symbol'))}).",
                           level="warn")
        text = COMMANDS.get(e.get("command"), "Comando {command} aplicado.")
        return Message(t, text.format(coin=coin(e.get("symbol")), command=e.get("command")))

    if kind == ev.ALERT:
        who = f"{coin(e.get('symbol'))}: " if e.get("symbol") else ""
        return Message(t, f"Aviso. {who}{ALERTS.get(e.get('code'), e.get('message', ''))}", level="warn")

    if kind == ev.KILL_SWITCH:
        return Message(t, f"Kill-switch activado: el capital cayó {abs(e['drawdown']):.1%} desde su máximo.",
                       "No abriré operaciones nuevas hasta que lo reactives. Las posiciones siguen con sus stops.",
                       level="bad")

    if kind == ev.CYCLE_ERROR:
        return Message(t, "Tuve un problema en este ciclo; reintento en "
                          f"{int(e.get('retry_s', 300)) // 60} min. Los stops siguen activos en Binance.",
                       str(e.get("error", ""))[:120], level="warn")

    if kind == ev.STATUS:
        if e.get("kill_switch"):
            head = "Sigo activo, con el kill-switch activado: solo gestiono las posiciones abiertas."
        elif e.get("bot_paused"):
            head = "Sigo activo, en pausa: no abro operaciones nuevas."
        else:
            head = "Sigo activo."
        parts = []
        if e.get("market_total"):
            parts.append(f"Mercado: {e['market_up']} de {e['market_total']} al alza.")
        n = e.get("open_positions", 0)
        if n:
            parts.append(f"{n} {'posición abierta' if n == 1 else 'posiciones abiertas'}, "
                         f"no realizado {money(e.get('unrealized_pnl'), sign=True)}.")
        else:
            parts.append("Sin posiciones abiertas.")
        parts.append(f"Capital {money(e.get('equity'))}.")
        return Message(t, head, " ".join(parts))

    return Message(t, kind)


def next_candle_close(now: pd.Timestamp, timeframe: str = "12h") -> pd.Timestamp:
    return now.floor(timeframe) + pd.Timedelta(timeframe)


def countdown(delta: pd.Timedelta) -> str:
    minutes = max(int(delta.total_seconds() // 60), 0)
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes} min"


def standby_line(state: BotState, unrealized: float | None, last_candle: dict | None, alive: bool,
                 now: pd.Timestamp, tz: tzinfo) -> str:
    """The single live 'what is the bot doing right now' line (decision 26)."""
    if not alive:
        return "El bot no responde. Revisa que el proceso siga corriendo; los stops siguen activos en Binance."
    if state.kill_switch.tripped:
        head = "Kill-switch activo: solo gestiono las posiciones abiertas."
    elif state.bot_paused:
        head = "En pausa: no abro operaciones nuevas."
    else:
        head = "En espera."
    nxt = next_candle_close(now)
    n = len(state.positions)
    parts = [f"Próxima vela a las {nxt.tz_convert(tz):%H:%M}, en {countdown(nxt - now)}.",
             f"{n} {'posición abierta' if n == 1 else 'posiciones abiertas'}."]
    if unrealized is not None and n:
        parts.append(f"No realizado {money(unrealized, sign=True)}.")
    if last_candle:
        sigs = list(last_candle.get("signals", {}).values())
        parts.append(f"Mercado: {sigs.count(1)} de {len(sigs)} alcistas.")
    return " ".join([head, *parts])
