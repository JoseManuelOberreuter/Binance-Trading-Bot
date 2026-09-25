"""
Control dashboard (docs/execution_plan.md decisions 23-28).

  streamlit run xrp_futures/monitor/dashboard.py

Tabs (user-defined): Inicio (big total capital + each coin's cumulative result, nothing
else), Consola (plain-language activity feed with a 30-min "still running" summary, plus
the controls), Monedas (per-coin detail), Capital (equity detail and chart), Configuración.
Editorial, near-monochrome, no emojis; color only on gains/losses/warnings.

Reads only the files the bot writes under live_state/<env>/; holds NO API keys and never
talks to Binance — controls go through the command inbox and the bot applies them at its
next cycle (<= 5 min).
"""

from __future__ import annotations

import html
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # `streamlit run` only adds this file's folder

import altair as alt
import pandas as pd
import streamlit as st

from xrp_futures.config.live import DEFAULT as CONFIG
from xrp_futures.execution import commands as cmdq
from xrp_futures.execution.state import StateStore
from xrp_futures.monitor import data
from xrp_futures.monitor import messages as msg

st.set_page_config(page_title="Bot Futures", layout="wide", initial_sidebar_state="collapsed")

GOOD, BAD, WARN, MUTED, LINE = "#4fb286", "#e5675f", "#d6a74a", "#8a919c", "#1f242c"
st.markdown(f"""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');
.stApp {{ font-family: 'Inter', sans-serif; }}   /* inherited; icon fonts keep their own family */
header[data-testid="stHeader"], [data-testid="stSidebarCollapsedControl"] {{ display: none; }}
.block-container {{ padding-top: 2.2rem; max-width: 1000px; }}
.num, .t {{ font-family: 'JetBrains Mono', monospace; font-variant-numeric: tabular-nums; }}
.muted {{ color: {MUTED}; }}
.good {{ color: {GOOD}; }} .bad {{ color: {BAD}; }} .warn {{ color: {WARN}; }}
.dot {{ display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:7px; vertical-align:middle; }}
.hero {{ text-align:center; margin: 2.6rem 0 2.4rem; }}
.hero .label {{ color:{MUTED}; font-size:0.85rem; letter-spacing:0.08em; text-transform:uppercase; }}
.hero .value {{ font-family:'JetBrains Mono', monospace; font-size:3.4rem; font-weight:500; line-height:1.15; }}
.hero .change {{ font-size:1rem; }}
.coins {{ display:grid; grid-template-columns: repeat(4, 1fr); border-top:1px solid {LINE}; }}
.coin {{ padding:18px 8px; border-bottom:1px solid {LINE}; text-align:center; }}
.coin .name {{ font-weight:600; font-size:0.95rem; }}
.coin .pct {{ font-family:'JetBrains Mono', monospace; font-size:1.35rem; margin-top:4px; }}
.coin .sub {{ color:{MUTED}; font-size:0.78rem; margin-top:2px; }}
.footer-line {{ color:{MUTED}; font-size:0.85rem; text-align:center; margin-top:1.6rem; }}
.feed-item {{ display:flex; gap:18px; padding:10px 0; border-bottom:1px solid {LINE}; }}
.feed-item .t {{ color:{MUTED}; font-size:0.8rem; min-width:78px; padding-top:2px; }}
.feed-item .detail {{ color:{MUTED}; font-size:0.86rem; margin-top:2px; }}
.feed-item.status .head {{ color:{MUTED}; }}
table.kv {{ width:100%; border-collapse:collapse; font-size:0.9rem; border:none; }}
table.kv th, table.kv td {{ border:none; border-bottom:1px solid {LINE}; text-align:left; padding:8px 4px; }}
table.kv th {{ color:{MUTED}; font-weight:500; }}
.metrics {{ display:grid; grid-template-columns: repeat(4, 1fr); gap:0; border-top:1px solid {LINE};
            border-bottom:1px solid {LINE}; margin: 0.4rem 0 1.4rem; }}
.metric {{ padding:14px 6px; }}
.metric .label {{ color:{MUTED}; font-size:0.78rem; }}
.metric .value {{ font-family:'JetBrains Mono', monospace; font-size:1.25rem; margin-top:3px; }}
h3 {{ font-weight:600; letter-spacing:-0.01em; }}
</style>""", unsafe_allow_html=True)

TZ = datetime.now().astimezone().tzinfo    # show times in this PC's local time
LEVEL_CLASS = {"good": "good", "bad": "bad", "warn": "warn", "info": ""}
LABELS = {
    cmdq.PAUSE_BOT: "Pausar bot", cmdq.RESUME_BOT: "Reanudar bot", cmdq.CLOSE_POSITION: "Cerrar",
    cmdq.PAUSE_SYMBOL: "Pausar", cmdq.RESUME_SYMBOL: "Reanudar", cmdq.RESET_KILL_SWITCH: "Reactivar kill-switch",
}
SIGNAL_NAME = {1: "LONG (al alza)", -1: "SHORT (a la baja)", 0: "sin señal"}


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def cls_of(x: float | None) -> str:
    return "good" if x and x > 0 else "bad" if x and x < 0 else ""


def signed(x: float | None) -> str:
    """Money amount colored by sign."""
    if x is None:
        return "—"
    return f"<span class='num {cls_of(x)}'>{msg.money(x, sign=True)}</span>"


def when(ts: pd.Timestamp, now: pd.Timestamp) -> str:
    local = ts.tz_convert(TZ)
    return local.strftime("%H:%M") if local.date() == now.tz_convert(TZ).date() else local.strftime("%d-%m %H:%M")


def html_block(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def submit(env: str, kind: str, symbol: str | None = None) -> None:
    cmdq.CommandQueue(StateStore(env).dir).submit(kind, symbol)
    st.session_state["flash"] = (f"{LABELS[kind]}{' ' + msg.coin(symbol) if symbol else ''}: enviado. "
                                 "El bot lo aplica en su próximo ciclo (5 min como máximo).")
    st.rerun(scope="fragment")


# ---------------------------------------------------------------- header ----
head, env_col = st.columns([5, 1])
envs = data.environments() or ["testnet"]
default_env = os.getenv("FUTURES_ENVIRONMENT", "testnet")
env = env_col.selectbox("Entorno", envs, index=envs.index(default_env) if default_env in envs else 0,
                        label_visibility="collapsed")
label = "<span class='bad'>producción · dinero real</span>" if env == "production" else "<span class='muted'>testnet</span>"
head.markdown(f"<h3 style='margin:0'>Bot Futures <small style='font-size:0.9rem;font-weight:400'>&nbsp;{label}</small></h3>",
              unsafe_allow_html=True)


@st.fragment(run_every="30s")
def main_view() -> None:
    view = data.load(env, CONFIG)
    state, now = view.state, pd.Timestamp.now(tz="UTC")
    if "flash" in st.session_state:
        st.caption(st.session_state.pop("flash"))
    if state is None:
        html_block(f"<p class='muted'>El bot todavía no ha corrido en {env}. Inícialo con "
                   "<code>python -m xrp_futures.scripts.run_live_bot</code>.</p>")
        return

    alive, beat = data.heartbeat_status(state, now)
    last = view.equity.iloc[-1] if not view.equity.empty else None
    equity = float(last["equity"]) if last is not None else (state.simulated_capital or 0.0)
    unrealized = float(last["unrealized_pnl"]) if last is not None and "unrealized_pnl" in last else 0.0
    marks = data.latest_marks(view)
    coins = data.per_coin(state, view.trades, marks, CONFIG.universe)

    home, console, coins_tab, capital, config = st.tabs(["Inicio", "Consola", "Monedas", "Capital", "Configuración"])
    with home:
        home_tab(state, equity, coins, alive, beat, now)
    with console:
        console_tab(view, state, unrealized, alive, now)
    with coins_tab:
        coins_detail_tab(view, state, coins, marks, now)
    with capital:
        capital_tab(view, state, equity, unrealized)
    with config:
        config_tab(view)


# ------------------------------------------------------------------ Inicio ----
def home_tab(state, equity: float, coins: dict, alive: bool, beat: str, now: pd.Timestamp) -> None:
    capital0 = state.simulated_capital or equity
    change = equity - capital0
    html_block(
        "<div class='hero'><div class='label'>Capital total</div>"
        f"<div class='value'>{msg.money(equity)}</div>"
        f"<div class='change'><span class='num {cls_of(change)}'>{msg.money(change, sign=True)} "
        f"({msg.pct(change / capital0, 2) if capital0 else '—'})</span> <span class='muted'>desde el inicio</span></div></div>")

    cells = []
    for symbol, c in coins.items():
        if c["traded"]:
            pct = f"<div class='pct {cls_of(c['total'])}'>{msg.pct(c['pct'], 2)}</div>"
            sub = f"<div class='sub num'>{msg.money(c['total'], sign=True)}</div>"
        else:
            pct = "<div class='pct muted'>—</div>"
            sub = "<div class='sub'>sin operar</div>"
        cells.append(f"<div class='coin'><div class='name'>{msg.coin(symbol)}</div>{pct}{sub}</div>")
    html_block(f"<div class='coins'>{''.join(cells)}</div>")

    nxt = msg.next_candle_close(now)
    dot = GOOD if alive else BAD
    status = "Activo" if alive else "Detenido"
    if state.kill_switch.tripped:
        status = "Kill-switch activo"
    elif state.bot_paused:
        status = "En pausa"
    html_block(f"<div class='footer-line'><span class='dot' style='background:{dot}'></span>{status} · {beat} · "
               f"próxima vela a las {nxt.tz_convert(TZ):%H:%M}</div>")
    html_block("<div class='footer-line'>Porcentaje de cada moneda: lo que aportó al capital inicial, sumando "
               "operaciones cerradas y la posición abierta.</div>")


# ----------------------------------------------------------------- Consola ----
def console_tab(view, state, unrealized: float, alive: bool, now: pd.Timestamp) -> None:
    line, ctrl = st.columns([4, 1])
    line.markdown(f"<p class='muted' style='font-size:0.9rem;margin-top:6px'>"
                  f"{esc(msg.standby_line(state, unrealized, view.last_candle, alive, now, TZ))}</p>",
                  unsafe_allow_html=True)
    with ctrl.popover("Controles", use_container_width=True):
        controls(state)
    if view.commands_pending:
        st.caption("Pendiente: " + ", ".join(f"{LABELS[c['kind']]} {msg.coin(c.get('symbol'))}".strip()
                                             for c in view.commands_pending))
    render_feed(view.events[-120:], now)
    html_block(f"<p class='muted' style='font-size:0.8rem;margin-top:1rem'>Las acciones aparecen al instante; "
               f"un resumen cada {CONFIG.status_every_min} min confirma que el bot sigue activo.</p>")


def render_feed(events: list[dict], now: pd.Timestamp) -> None:
    items = [(e["type"], msg.render(e, TZ)) for e in reversed(events)]
    if not items:
        html_block("<p class='muted'>Aún no hay actividad registrada.</p>")
        return
    rows = []
    for kind, m in items:
        amount = f"<span class='num {LEVEL_CLASS[m.level]}'>{esc(m.amount or '')}</span>"
        head = esc(m.text).replace(esc(msg.AMOUNT), amount)
        if m.level == "warn" or (m.level == "bad" and msg.AMOUNT not in m.text):
            head = f"<span class='{LEVEL_CLASS[m.level]}'>{head}</span>"
        detail = f"<div class='detail'>{esc(m.detail)}</div>" if m.detail else ""
        extra = " status" if kind == "STATUS" else ""
        rows.append(f"<div class='feed-item{extra}'><div class='t'>{when(m.time, now)}</div>"
                    f"<div><div class='head'>{head}</div>{detail}</div></div>")
    html_block("".join(rows))


def controls(state) -> None:
    if state.bot_paused:
        if st.button("Reanudar bot", use_container_width=True):
            submit(env, cmdq.RESUME_BOT)
    elif st.button("Pausar bot (sin entradas nuevas)", use_container_width=True):
        submit(env, cmdq.PAUSE_BOT)

    st.divider()
    if state.positions:
        sym = st.selectbox("Cerrar posición", list(state.positions), format_func=msg.coin, key="close_sym")
        ok = st.checkbox("Confirmo cerrar a mercado", key="close_ok")
        if st.button("Cerrar", disabled=not ok, type="primary", use_container_width=True):
            submit(env, cmdq.CLOSE_POSITION, sym)
        st.caption("Si la señal sigue, puede volver a entrar en la próxima vela; pausa la moneda para evitarlo.")
        st.divider()

    sym = st.selectbox("Moneda", list(CONFIG.universe), format_func=msg.coin, key="pause_sym")
    if sym in state.paused:
        if st.button(f"Reanudar {msg.coin(sym)}", use_container_width=True):
            submit(env, cmdq.RESUME_SYMBOL, sym)
    elif st.button(f"Pausar {msg.coin(sym)}", use_container_width=True):
        submit(env, cmdq.PAUSE_SYMBOL, sym)

    if state.kill_switch.tripped:
        st.divider()
        ok = st.checkbox("Revisé la caída y quiero reanudar", key="ks_ok")
        if st.button("Reactivar kill-switch", disabled=not ok, type="primary", use_container_width=True):
            submit(env, cmdq.RESET_KILL_SWITCH)


# ----------------------------------------------------------------- Monedas ----
def coins_detail_tab(view, state, coins: dict, marks: dict, now: pd.Timestamp) -> None:
    universe = list(CONFIG.universe)
    first_open = next((s for s in universe if s in state.positions), universe[0])
    symbol = st.segmented_control("Moneda", universe, default=first_open, format_func=msg.coin,
                                  key="coin_pick", label_visibility="collapsed") or first_open
    c = coins[symbol]
    pos = state.positions.get(symbol)
    signal = state.last_signals.get(symbol)
    next_close = msg.next_candle_close(now).tz_convert(TZ)
    signal_text = SIGNAL_NAME[signal] if signal is not None else f"se sabrá a las {next_close:%H:%M}"

    html_block(
        "<div class='metrics'>"
        f"<div class='metric'><div class='label'>Resultado acumulado</div><div class='value {cls_of(c['total'])}'>"
        f"{msg.money(c['total'], sign=True) if c['traded'] else '—'}</div></div>"
        f"<div class='metric'><div class='label'>Aporte al capital</div><div class='value {cls_of(c['total'])}'>"
        f"{msg.pct(c['pct'], 2) if c['pct'] is not None else '—'}</div></div>"
        f"<div class='metric'><div class='label'>Operaciones cerradas</div><div class='value'>{c['n_trades']}</div></div>"
        f"<div class='metric'><div class='label'>Señal actual</div>"
        f"<div class='value' style=\"font-family:'Inter',sans-serif;font-size:1rem;padding-top:5px\">{signal_text}</div>"
        "</div></div>")

    if symbol in state.paused:
        html_block(f"<p class='warn'>En pausa: {esc(state.paused[symbol])}</p>")

    if pos:
        mark = marks.get(symbol)
        stop = float(pos.stop_price)
        rows = [("Posición", f"{msg.side_name(pos.side)} · {pos.qty}"),
                ("Entrada", f"<span class='num'>{msg.num(pos.entry_price)}</span> "
                            f"<span class='muted'>· {when(pd.Timestamp(pos.entry_time), now)}</span>")]
        if mark:
            notional = float(pos.qty) * pos.entry_price
            rows += [("Precio actual", f"<span class='num'>{msg.num(mark['price'])}</span>"),
                     ("PnL no realizado", f"{signed(mark['unrealized'])} "
                                          f"<span class='num muted'>({msg.pct(mark['unrealized'] / notional)})</span>")]
        ref = mark["price"] if mark else pos.entry_price
        rows += [("Stop", f"<span class='num'>{msg.num(stop)}</span> <span class='num muted'>"
                          f"({msg.pct((stop - ref) / ref)} desde el precio actual)</span>"),
                 ("Riesgo si salta el stop", signed(pos.side * (stop - pos.entry_price) * float(pos.qty)))]
        html_block("<table class='kv'>" + "".join(f"<tr><th style='width:38%'>{k}</th><td>{v}</td></tr>"
                                                  for k, v in rows) + "</table>")
        st.caption("Precio y PnL según Binance, actualizados en cada ciclo del bot (cada 5 min).")
    else:
        html_block("<p class='muted'>Sin posición abierta en esta moneda.</p>")

    st.markdown("**Historial de la moneda**")
    mine = [e for e in view.events if e.get("symbol") == symbol]
    if mine:
        render_feed(mine[-40:], now)
    else:
        html_block("<p class='muted'>Sin actividad todavía.</p>")


# ----------------------------------------------------------------- Capital ----
def capital_tab(view, state, equity: float, unrealized: float) -> None:
    capital0 = state.simulated_capital or equity
    peak = state.kill_switch.peak_equity or equity
    dd = state.kill_switch.drawdown(equity)
    html_block(
        "<div class='metrics'>"
        f"<div class='metric'><div class='label'>Capital inicial</div><div class='value'>{msg.money(capital0)}</div></div>"
        f"<div class='metric'><div class='label'>Realizado</div><div class='value {cls_of(state.realized_pnl)}'>"
        f"{msg.money(state.realized_pnl, sign=True)}</div></div>"
        f"<div class='metric'><div class='label'>No realizado</div><div class='value {cls_of(unrealized)}'>"
        f"{msg.money(unrealized, sign=True)}</div></div>"
        f"<div class='metric'><div class='label'>Caída desde el máximo ({msg.money(peak)})</div>"
        f"<div class='value {cls_of(dd)}'>{msg.pct(dd)}</div></div></div>")
    html_block(f"<p class='muted' style='font-size:0.85rem'>El kill-switch se activa con una caída de "
               f"{CONFIG.kill_switch_drawdown:.0%} desde el máximo.</p>")

    if len(view.equity) < 2:
        html_block("<p class='muted'>La curva aparece después de algunos ciclos del bot.</p>")
        return
    step = st.segmented_control("Intervalo", ["4 horas", "1 día"], default="1 día",
                                key="equity_step", label_visibility="collapsed") or "1 día"
    rule = "4h" if step == "4 horas" else "1D"
    local = view.equity[["time", "equity"]].copy()   # bucket by this PC's calendar, not UTC
    local["time"] = local["time"].dt.tz_convert(TZ).dt.tz_localize(None)
    points = data.equity_every(local, rule)
    if len(points) > 1:
        st.altair_chart(equity_chart(points, capital0, rule), use_container_width=True)
        st.caption(f"Un punto cada {step}, con el último valor de cada intervalo. "
                   "Línea punteada: capital inicial. Horas en la zona horaria de este PC.")
    else:
        html_block(f"<p class='muted'>Todavía hay un solo valor ({msg.money(float(points['equity'].iloc[-1]))}).</p>")


def _vl_datetime(ts: pd.Timestamp) -> alt.DateTime:
    """Local, tz-naive timestamp -> Vega-Lite DateTime (interpreted in local time, no re-shift)."""
    return alt.DateTime(year=ts.year, month=ts.month, date=ts.day, hours=ts.hour, minutes=ts.minute)


def equity_chart(points: pd.DataFrame, baseline: float, rule: str) -> alt.Chart:
    """
    Equity curve that doesn't exaggerate: the y-range spans at least ±2% of the starting
    capital (a -0.1% wiggle must not look like a crash), with a dashed baseline. `points`
    are already local, tz-naive times; the x axis is aligned to the chosen interval
    (4-hour or daily ticks) via data.time_axis.
    """
    start, end, ticks = data.time_axis(points["time"].iloc[0], points["time"].iloc[-1], rule)
    label = "%d-%m %H:%M" if rule == "4h" else "%d-%m"
    lo = min(points["equity"].min(), baseline * 0.98)
    hi = max(points["equity"].max(), baseline * 1.02)
    y = alt.Y("equity:Q", title=None, scale=alt.Scale(domain=[lo, hi], nice=False),
              axis=alt.Axis(gridColor=LINE, labelColor=MUTED, domain=False, format=",.0f", tickCount=5))
    line = alt.Chart(points).mark_line(color="#c9ced6", strokeWidth=1.5,
                                       point=alt.OverlayMarkDef(color="#c9ced6", size=22)).encode(
        x=alt.X("time:T", title=None,
                scale=alt.Scale(domain=[_vl_datetime(start), _vl_datetime(end)]),
                axis=alt.Axis(grid=False, labelColor=MUTED, domainColor=LINE, format=label, labelAngle=0,
                              values=[_vl_datetime(t) for t in ticks])),
        y=y,
        tooltip=[alt.Tooltip("time:T", title="Hora", format="%d-%m %H:%M"),
                 alt.Tooltip("equity:Q", title="Capital", format=",.2f")])
    base = alt.Chart(pd.DataFrame({"equity": [baseline]})).mark_rule(
        color=MUTED, strokeDash=[4, 4], strokeWidth=1).encode(y=y)
    return (base + line).properties(height=260).configure_view(strokeWidth=0)


# ----------------------------------------------------------- Configuración ----
def config_tab(view) -> None:
    cap = CONFIG.equity_override_usdt
    rows = [
        ("Monedas", ", ".join(msg.coin(s) for s in CONFIG.universe)),
        ("Señal", f"Ruptura de {CONFIG.strategy.donchian_window} velas de {CONFIG.strategy.timeframe} (Donchian)"),
        ("Stop", f"{CONFIG.initial_stop_atr_multiplier:g}×ATR inicial, luego trailing Chandelier "
                 f"({CONFIG.chandelier_window} velas, {CONFIG.chandelier_multiplier:g}×ATR)"),
        ("Riesgo por operación", f"{CONFIG.risk_pct:.1%} del capital"),
        ("Apalancamiento", f"{CONFIG.leverage}x, margen aislado"),
        ("Kill-switch", f"−{CONFIG.kill_switch_drawdown:.0%} desde el máximo"),
        ("Capital", f"{msg.money(cap)} simulados, más lo que gane o pierda el bot" if cap else "Saldo real de Futures"),
    ]
    html_block("<table class='kv'>" + "".join(f"<tr><th style='width:34%'>{k}</th><td>{esc(v)}</td></tr>"
                                              for k, v in rows) + "</table>")
    st.write("")
    with st.expander("Cómo funciona"):
        st.markdown(f"""
Cada 5 minutos el bot comprueba que lo que cree tener coincida con Binance y aplica tus controles.

Al cerrar cada vela de {CONFIG.strategy.timeframe} (00:00 y 12:00 UTC), revisa cada moneda: si la señal cambió,
cierra; si sigue, sube el stop (nunca lo afloja); si no hay posición y hay señal, entra arriesgando
{CONFIG.risk_pct:.1%} del capital.

Los stops viven en Binance, así que protegen las posiciones aunque el bot o el PC se apaguen. Si una orden
queda bajo el mínimo de Binance, se omite en vez de agrandar el riesgo.
""")
    with st.expander("Log técnico"):
        st.code("\n".join(view.log_tail[-100:]) or "(vacío)", language="log")


main_view()
