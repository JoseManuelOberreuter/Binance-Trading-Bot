"""
Control dashboard (docs/execution_plan.md decisions 23-28).

  streamlit run xrp_futures/monitor/dashboard.py

Tabs (user-defined): Inicio (big total capital + each coin's cumulative result, nothing
else), Consola (plain-language activity feed with a 30-min "still running" summary, plus
the controls), Monedas (per-coin detail), Capital (equity detail and chart), Configuración.
Editorial, near-monochrome, no emojis; color only on gains/losses/warnings.

Two separate pages, never mixed: "Bot" reads only the files the bot writes under
live_state/<env>/ (no API keys, never talks to Binance — controls go through the command
inbox and the bot applies them at its next cycle, <= 1 min). "Cuenta Binance" shows the
user's REAL account read-only via monitor/account.py (production key from .env).
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

from xrp_futures.config.live import DEFAULT as CONFIG, config_for
from xrp_futures.execution import commands as cmdq
from xrp_futures.execution.state import StateStore
from xrp_futures.monitor import account as acc
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
    cmdq.SET_CAPITAL: "Cambiar capital",
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


def submit(env: str, kind: str, symbol: str | None = None, value: float | None = None) -> None:
    cmdq.CommandQueue(StateStore(env).dir).submit(kind, symbol, value=value)
    st.session_state["flash"] = (f"{LABELS[kind]}{' ' + msg.coin(symbol) if symbol else ''}: enviado. "
                                 "El bot lo aplica en su próximo ciclo (1 min como máximo).")
    st.rerun(scope="fragment")


# ------------------------------------------------------------------ pages ----
# Two pages that never share a screen, picked with the selector at the top right:
#   Testnet — the bot on testnet (test money).
#   Binance — the bot on the REAL account (production) plus a read-only "Cuenta" tab.
VIEWS = ("Testnet", "Binance")


def page_header(title: str, label: str, current: str) -> None:
    head, select_col = st.columns([5, 1], vertical_alignment="center")
    head.markdown(f"<h3 style='margin:0'>{title} <small style='font-size:0.9rem;font-weight:400'>&nbsp;{label}</small></h3>",
                  unsafe_allow_html=True)
    choice = select_col.selectbox("Vista", VIEWS, index=VIEWS.index(current), key=f"view_{current}",
                                  label_visibility="collapsed")
    if choice != current:
        st.switch_page(TESTNET_PAGE if choice == "Testnet" else BINANCE_PAGE)


def testnet_page() -> None:
    page_header("Bot Futures", "<span class='muted'>testnet · dinero de prueba</span>", "Testnet")
    main_view("testnet")


def binance_page() -> None:
    page_header("Bot Futures", "<span class='bad'>Binance · dinero real</span>", "Binance")
    main_view("production")


@st.fragment(run_every="30s")
def main_view(env: str) -> None:
    config = config_for(env)
    view = data.load(env, config)
    state, now = view.state, pd.Timestamp.now(tz="UTC")
    if "flash" in st.session_state:
        st.caption(st.session_state.pop("flash"))
    real = env == "production"

    if state is None:
        if not real:
            html_block("<p class='muted'>El bot todavía no ha corrido en testnet. Inícialo con "
                       "<code>python -m xrp_futures.scripts.run_live_bot</code>.</p>")
            return
        home, account = st.tabs(["Inicio", "Cuenta"])
        with home:
            html_block("<p class='muted' style='margin-top:1.4rem'>El bot real todavía no se ha iniciado. "
                       "Cuando lo inicies arrancará <strong>en pausa</strong>: no abrirá nada hasta que lo actives aquí.</p>")
        with account:
            account_tab(now)
        return

    alive, _ = data.heartbeat_status(state, now)
    last = view.equity.iloc[-1] if not view.equity.empty else None
    equity = float(last["equity"]) if last is not None else (state.simulated_capital or 0.0)
    unrealized = float(last["unrealized_pnl"]) if last is not None and "unrealized_pnl" in last else 0.0
    capital0 = data.starting_capital(state, equity, unrealized)
    marks = data.latest_marks(view)
    coins = data.per_coin(state, view.trades, marks, config.universe, capital0)

    names = ["Inicio", "Consola", "Monedas", "Capital"] + (["Cuenta"] if real else []) + ["Configuración"]
    tabs = dict(zip(names, st.tabs(names)))
    with tabs["Inicio"]:
        home_tab(state, equity, capital0, coins, alive, now, env)
    with tabs["Consola"]:
        console_tab(view, state, now, env)
    with tabs["Monedas"]:
        coins_detail_tab(view, state, coins, marks, now)
    with tabs["Capital"]:
        capital_tab(view, state, equity, capital0, unrealized)
    if real:
        with tabs["Cuenta"]:
            account_tab(now)
    with tabs["Configuración"]:
        config_tab(view, config, state, equity, env)


# ------------------------------------------------------------------ Inicio ----
def home_tab(state, equity: float, capital0: float, coins: dict, alive: bool, now: pd.Timestamp, env: str) -> None:
    change = equity - capital0
    html_block(
        "<div class='hero'><div class='label'>Capital total</div>"
        f"<div class='value'>{msg.money(equity)}</div>"
        f"<div class='change'><span class='num {cls_of(change)}'>{msg.money(change, sign=True)} "
        f"({msg.pct(change / capital0, 2) if capital0 else '—'})</span> <span class='muted'>desde el inicio</span></div></div>")

    cells = []
    for symbol, c in coins.items():
        pct = (f"<div class='pct {cls_of(c['total'])}'>{msg.pct(c['pct'], 2)}</div>" if c["traded"]
               else "<div class='pct muted'>—</div>")
        cells.append(f"<div class='coin'><div class='name'>{msg.coin(symbol)}</div>{pct}</div>")
    html_block(f"<div class='coins'>{''.join(cells)}</div>")

    nxt = msg.next_candle_close(now)
    dot = GOOD if alive else BAD
    status = "Activo" if alive else "Detenido"
    if state.kill_switch.tripped:
        status = "Kill-switch activo"
    elif state.bot_paused:
        status = "En pausa"
    html_block(f"<div class='footer-line'><span class='dot' style='background:{dot}'></span>{status} · "
               f"próxima vela {nxt.tz_convert(TZ):%H:%M}</div>")
    power_switch(state, env)


def power_switch(state, env: str) -> None:
    """On/off for new trades. Off = pause: opens nothing new, but stops and exits keep protecting
    any open position (closing them is a separate, explicit control in Consola)."""
    pending = {c["kind"] for c in cmdq.CommandQueue(StateStore(env).dir).pending()}
    _, mid, _ = st.columns([2, 1, 2])
    if pending & {cmdq.PAUSE_BOT, cmdq.RESUME_BOT}:
        mid.caption("Cambio enviado; se aplica en el próximo ciclo.")
    elif state.bot_paused:
        if mid.button("Activar bot", type="primary", use_container_width=True, key=f"on_{env}"):
            submit(env, cmdq.RESUME_BOT)
    elif mid.button("Desactivar bot", use_container_width=True, key=f"off_{env}"):
        submit(env, cmdq.PAUSE_BOT)


# ----------------------------------------------------------------- Consola ----
def console_tab(view, state, now: pd.Timestamp, env: str) -> None:
    _, ctrl = st.columns([4, 1])
    with ctrl.popover("Controles", use_container_width=True):
        controls(state, env)
    if view.commands_pending:
        st.caption("Pendiente: " + ", ".join(f"{LABELS[c['kind']]} {msg.coin(c.get('symbol'))}".strip()
                                             for c in view.commands_pending))
    render_feed([e for e in view.events if not is_minimum_skip(e)][-120:], now)


def is_minimum_skip(e: dict) -> bool:
    """'No entré: la orden mínima…' repeats for the same coins every candle — noise in the console."""
    return e.get("type") == "ENTRY_SKIPPED" and e.get("reason") == "below_minimum"


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


def controls(state, env: str) -> None:
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
        "<div class='metrics' style='grid-template-columns:repeat(3,1fr)'>"
        f"<div class='metric'><div class='label'>Resultado acumulado</div><div class='value {cls_of(c['total'])}'>"
        f"{msg.money(c['total'], sign=True) if c['traded'] else '—'}</div></div>"
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
                ("Entrada", f"<span class='num'>{msg.num(pos.entry_price)}</span>")]
        if mark:
            notional = float(pos.qty) * pos.entry_price
            rows += [("Precio actual", f"<span class='num'>{msg.num(mark['price'])}</span>"),
                     ("PnL no realizado", f"{signed(mark['unrealized'])} "
                                          f"<span class='num muted'>({msg.pct(mark['unrealized'] / notional)})</span>")]
        ref = mark["price"] if mark else pos.entry_price
        rows += [("Stop", f"<span class='num'>{msg.num(stop)}</span> "
                          f"<span class='num muted'>({msg.pct((stop - ref) / ref)})</span>")]
        html_block("<table class='kv'>" + "".join(f"<tr><th style='width:38%'>{k}</th><td>{v}</td></tr>"
                                                  for k, v in rows) + "</table>")
    else:
        html_block("<p class='muted'>Sin posición abierta en esta moneda.</p>")

    st.markdown("**Historial de la moneda**")
    mine = [e for e in view.events if e.get("symbol") == symbol]
    if mine:
        render_feed(mine[-40:], now)
    else:
        html_block("<p class='muted'>Sin actividad todavía.</p>")


# ----------------------------------------------------------------- Capital ----
def capital_tab(view, state, equity: float, capital0: float, unrealized: float) -> None:
    dd = state.kill_switch.drawdown(equity)
    html_block(
        "<div class='metrics'>"
        f"<div class='metric'><div class='label'>Capital inicial</div><div class='value'>{msg.money(capital0)}</div></div>"
        f"<div class='metric'><div class='label'>Realizado</div><div class='value {cls_of(state.realized_pnl)}'>"
        f"{msg.money(state.realized_pnl, sign=True)}</div></div>"
        f"<div class='metric'><div class='label'>No realizado</div><div class='value {cls_of(unrealized)}'>"
        f"{msg.money(unrealized, sign=True)}</div></div>"
        f"<div class='metric'><div class='label'>Caída desde el máximo</div>"
        f"<div class='value {cls_of(dd)}'>{msg.pct(dd)}</div></div></div>")

    if len(view.equity) < 2:
        html_block("<p class='muted'>La curva aparece después de algunos ciclos del bot.</p>")
        return
    range_col, step_col, _ = st.columns([3, 2, 3])
    span = range_col.segmented_control("Rango", list(data.CHART_RANGES), default="30 días",
                                       key="equity_range", label_visibility="collapsed") or "30 días"
    step = step_col.segmented_control("Intervalo", ["4 horas", "1 día"], default="1 día",
                                      key="equity_step", label_visibility="collapsed") or "1 día"
    rule = "4h" if step == "4 horas" else "1D"
    curve = data.performance_curve(view.equity, capital0)   # deposits/transfers removed
    curve["time"] = curve["time"].dt.tz_convert(TZ).dt.tz_localize(None)   # bucket by this PC's calendar
    now = pd.Timestamp.now(tz=TZ).tz_localize(None)
    points = data.chart_points(curve, capital0, rule, data.CHART_RANGES[span], now)
    if len(points) < 2:
        html_block("<p class='muted'>Todavía no hay suficientes datos en este rango.</p>")
        return
    marks = data.trade_marks(view.trades, state.positions, curve, TZ)
    marks = marks[marks["time"] >= points["time"].iloc[0]]
    st.altair_chart(capital_chart(points, marks, capital0, rule), use_container_width=True)
    if points["segment"].nunique() > 1:
        st.caption("Los cortes en la línea son períodos en que el bot estuvo apagado.")


def _vl_datetime(ts: pd.Timestamp) -> alt.DateTime:
    """Local, tz-naive timestamp -> Vega-Lite DateTime (interpreted in local time, no re-shift)."""
    return alt.DateTime(year=ts.year, month=ts.month, date=ts.day, hours=ts.hour, minutes=ts.minute)


def capital_chart(points: pd.DataFrame, marks: pd.DataFrame, baseline: float, rule: str) -> alt.VConcatChart:
    """
    Capital curve + drawdown strip. The curve doesn't exaggerate (y spans at least ±2% of
    the starting capital); green area above the starting capital, red below; $ on the left
    axis and the same scale in % on the right; the line breaks where the bot was off
    (`segment`); entries (hollow) and exits (green win / red loss) are marked on the curve.
    Times are local tz-naive; the x axis is aligned to the chosen interval (data.time_axis).
    """
    start, end, ticks = data.time_axis(points["time"].iloc[0], points["time"].iloc[-1], rule)
    label = "%d-%m %H:%M" if rule == "4h" else "%d-%m"
    x = alt.X("time:T", title=None, scale=alt.Scale(domain=[_vl_datetime(start), _vl_datetime(end)]),
              axis=alt.Axis(grid=False, labelColor=MUTED, domainColor=LINE, format=label, labelAngle=0,
                            values=[_vl_datetime(t) for t in ticks]))
    lo = min(points["equity"].min(), baseline * 0.98)
    hi = max(points["equity"].max(), baseline * 1.02)
    pts = points.assign(base=baseline, up=points["equity"].clip(lower=baseline),
                        down=points["equity"].clip(upper=baseline))
    dollars = alt.Scale(domain=[lo, hi], nice=False)
    span = hi - lo                           # small accounts need decimals or labels repeat (80, 80, 81…)
    fmt = ",.0f" if span >= 20 else ",.1f" if span >= 2 else ",.2f"
    y_axis = alt.Axis(gridColor=LINE, labelColor=MUTED, domain=False, format=fmt, tickCount=5)
    chart = alt.Chart(pts)
    gain = chart.mark_area(color=GOOD, opacity=0.18).encode(
        x=x, y=alt.Y("base:Q", scale=dollars, title=None, axis=y_axis), y2="up:Q", detail="segment:N")
    loss = chart.mark_area(color=BAD, opacity=0.18).encode(x=x, y=alt.Y("base:Q", scale=dollars), y2="down:Q",
                                                           detail="segment:N")
    tooltip = [alt.Tooltip("time:T", title="Hora", format="%d-%m %H:%M"),
               alt.Tooltip("equity:Q", title="Capital", format=",.2f"),
               alt.Tooltip("pct:Q", title="Resultado", format="+.2%"),
               alt.Tooltip("drawdown:Q", title="Caída desde el máximo", format=".2%")]
    line = chart.mark_line(color="#c9ced6", strokeWidth=1.5, point=alt.OverlayMarkDef(color="#c9ced6", size=18)
                           ).encode(x=x, y=alt.Y("equity:Q", scale=dollars), detail="segment:N", tooltip=tooltip)
    rule_line = alt.Chart(pd.DataFrame({"v": [baseline]})).mark_rule(
        color=MUTED, strokeDash=[4, 4], strokeWidth=1).encode(y=alt.Y("v:Q", scale=dollars))
    layers = [gain, loss, rule_line, line]
    if not marks.empty:
        mark_tooltip = [alt.Tooltip("time:T", title="Hora", format="%d-%m %H:%M"),
                        alt.Tooltip("kind:N", title="Operación"), alt.Tooltip("symbol:N", title="Moneda"),
                        alt.Tooltip("side:N", title="Lado"), alt.Tooltip("pnl:Q", title="Resultado", format="+,.2f")]
        color = alt.Color("result:N", legend=None,
                          scale=alt.Scale(domain=["good", "bad", "neutral"], range=[GOOD, BAD, MUTED]))
        m = alt.Chart(marks)
        layers += [
            m.transform_filter("datum.kind == 'Entrada'").mark_point(shape="circle", filled=False, size=70,
                                                                     strokeWidth=1.5, color=MUTED).encode(
                x=x, y=alt.Y("equity:Q", scale=dollars), tooltip=mark_tooltip),
            m.transform_filter("datum.kind == 'Salida'").mark_point(shape="circle", filled=True, size=70).encode(
                x=x, y=alt.Y("equity:Q", scale=dollars), color=color, tooltip=mark_tooltip),
        ]
    # Right axis: the same scale expressed as % of the starting capital.
    pct_axis = alt.Chart(pts).mark_line(opacity=0).encode(
        x=x, y=alt.Y("pct:Q", title=None, scale=alt.Scale(domain=[lo / baseline - 1, hi / baseline - 1], nice=False),
                     axis=alt.Axis(orient="right", labelColor=MUTED, domain=False, grid=False, format="+.0%",
                                   tickCount=5)))
    main = alt.layer(alt.layer(*layers), pct_axis).resolve_scale(y="independent").properties(height=260)

    dd_floor = min(points["drawdown"].min(), -0.05)
    dd_scale = alt.Scale(domain=[dd_floor, 0], nice=False)
    dd = chart.mark_area(color=BAD, opacity=0.3, line={"color": BAD, "strokeWidth": 1}).encode(
        x=x, y=alt.Y("drawdown:Q", scale=dd_scale, title=None,
                     axis=alt.Axis(gridColor=LINE, labelColor=MUTED, domain=False, format=".0%", tickCount=3)),
        detail="segment:N",
        tooltip=[alt.Tooltip("time:T", title="Hora", format="%d-%m %H:%M"),
                 alt.Tooltip("drawdown:Q", title="Caída desde el máximo", format=".2%")]).properties(
        height=80, title=alt.TitleParams("Caída desde el máximo", color=MUTED, fontSize=11, fontWeight="normal",
                                         anchor="start"))
    return alt.vconcat(main, dd, spacing=14).resolve_scale(x="shared").configure_view(strokeWidth=0)


# ---------------------------------------------------- Cuenta Binance (real) ----
@st.cache_data(ttl=60, show_spinner=False)
def account_snapshot() -> acc.AccountSnapshot | str:
    """Real Binance account, read-only, refreshed at most once a minute. str = error message."""
    try:
        return acc.fetch_snapshot(acc.readonly_client())
    except acc.AccountError as e:
        return str(e)


@st.cache_data(ttl=600, show_spinner=False)
def account_income() -> pd.DataFrame | str:
    """Futures income history (~13 requests), refreshed every 10 minutes."""
    try:
        return acc.fetch_income(acc.readonly_client())
    except acc.AccountError as e:
        return str(e)


def qty_text(q: float) -> str:
    return f"{q:,.0f}" if q >= 1000 else f"{q:,.8g}"


@st.cache_data(ttl=3600, show_spinner=False)
def holding_closes(asset: str) -> pd.Series | None:
    """Daily closes for the exit-rule indicator (daily candles: once an hour is plenty)."""
    try:
        return acc.fetch_daily_closes(acc.readonly_client(), asset)
    except acc.AccountError:
        return None


def holding_rules(spot: pd.DataFrame) -> None:
    """Reference only: what the two backtested exit rules say today. Never places orders."""
    rows = []
    for asset in acc.rule_assets(spot):
        closes = holding_closes(asset)
        price = float(spot.loc[spot["asset"] == asset, "price"].iloc[0])
        if closes is None or not len(closes):
            continue
        sig = acc.holding_signal(closes, price)
        if sig["sma200"] is None:
            sma = "<span class='muted'>poca historia</span>"
        else:
            state = "<span class='good'>sobre</span>" if sig["sma_in"] else "<span class='bad'>bajo</span>"
            sma = f"{state} <span class='num muted'>({msg.pct(sig['vs_sma'])})</span>"
        if sig["trail_in"] is None:
            trail = "<span class='muted'>poca historia</span>"
        elif sig["trail_in"]:
            trail = (f"<span class='good'>mantener</span> · saldría bajo <span class='num'>{msg.num(sig['exit_level'])}</span>"
                     f" <span class='num muted'>({msg.pct(sig['level_dist'])})</span>")
        else:
            trail = (f"<span class='bad'>fuera</span> · volvería sobre <span class='num'>{msg.num(sig['reentry_level'])}</span>"
                     f" <span class='num muted'>({msg.pct(sig['level_dist'])})</span>")
        rows.append(f"<tr><td>{esc(asset)}</td><td>{sma}</td><td>{trail}</td></tr>")
    if not rows:
        return
    st.write("")
    html_block("<p><strong>Reglas de salida</strong> <span class='muted'>· referencia histórica, "
               "no son órdenes ni asesoría</span></p>")
    head = "<tr><th style='width:16%'>Activo</th><th style='width:30%'>Media 200 días</th><th>Stop móvil −25%</th></tr>"
    html_block(f"<table class='kv'>{head}{''.join(rows)}</table>")


def metric(label: str, value: str, css: str = "") -> str:
    return f"<div class='metric'><div class='label'>{label}</div><div class='value {css}'>{value}</div></div>"


def account_tab(now: pd.Timestamp) -> None:
    snap = account_snapshot()
    if isinstance(snap, str):
        html_block(f"<p class='bad'>No se pudo leer tu cuenta de Binance.</p><p class='muted'>{esc(snap)}</p>")
        return
    fut = snap.futures
    html_block(
        "<div class='hero' style='margin:1.6rem 0 1.8rem'><div class='label'>Saldo total en Binance</div>"
        f"<div class='value'>{msg.money(snap.total)}</div>"
        f"<div class='change muted'>Spot {msg.money(snap.spot_total)} · Futures {msg.money(fut.get('margin_balance', 0))}"
        f" · USD, actualizado {when(snap.fetched_at, now)}</div></div>")
    for err in snap.errors:
        html_block(f"<p class='warn'>{esc(err)}</p>")

    st.markdown("**Futures**")
    unreal = fut.get("unrealized", 0.0)
    html_block("<div class='metrics'>"
               + metric("Billetera", msg.money(fut.get("wallet", 0)))
               + metric("No realizado", msg.money(unreal, sign=True), cls_of(unreal))
               + metric("Saldo de margen", msg.money(fut.get("margin_balance", 0)))
               + metric("Disponible", msg.money(fut.get("available", 0))) + "</div>")
    if snap.positions.empty:
        html_block("<p class='muted'>Sin posiciones abiertas en Futures.</p>")
    else:
        head = "".join(f"<th>{h}</th>" for h in ("Par", "Lado", "Cantidad", "Entrada", "Actual", "PnL",
                                                  "Apal.", "Liquidación"))
        rows = []
        for _, p in snap.positions.iterrows():
            pnl_pct = f" <span class='num muted'>({msg.pct(p['pnl_pct'])})</span>" if pd.notna(p["pnl_pct"]) else ""
            liq = (f"{msg.num(p['liq'])} <span class='muted'>({p['liq_dist']:.0%})</span>"
                   if pd.notna(p["liq"]) else "—")
            lev = f"{p['leverage']:.1f}x" if pd.notna(p["leverage"]) else "—"
            rows.append(f"<tr><td>{esc(p['symbol'])}</td><td>{msg.side_name(int(p['side']))}</td>"
                        f"<td class='num'>{p['qty']:g}</td><td class='num'>{msg.num(p['entry'])}</td>"
                        f"<td class='num'>{msg.num(p['mark'])}</td><td>{signed(p['pnl'])}{pnl_pct}</td>"
                        f"<td class='num'>{lev}</td><td class='num'>{liq}</td></tr>")
        html_block(f"<table class='kv'><tr>{head}</tr>{''.join(rows)}</table>")

    st.write("")
    st.markdown("**Spot**")
    spot = snap.spot
    if spot.empty:
        html_block("<p class='muted'>Sin saldos en Spot.</p>")
    else:
        big = spot[spot["value"].fillna(0) >= acc.DUST_USD]
        small = spot[spot["value"].fillna(0) < acc.DUST_USD]
        rows = [f"<tr><td>{esc(r['asset'])}</td><td class='num'>{qty_text(r['qty'])}</td>"
                f"<td class='num'>{msg.num(r['price'])}</td><td class='num'>{msg.money(r['value'])}</td>"
                f"<td class='num muted'>{r['share']:.1%}</td></tr>" for _, r in big.iterrows()]
        if not small.empty:
            rows.append(f"<tr><td class='muted'>Otros ({len(small)})</td><td></td><td></td>"
                        f"<td class='num muted'>{msg.money(float(small['value'].fillna(0).sum()))}</td><td></td></tr>")
        head = "".join(f"<th>{h}</th>" for h in ("Activo", "Cantidad", "Precio", "Valor", "% Spot"))
        html_block(f"<table class='kv'><tr>{head}</tr>{''.join(rows)}</table>")
        holding_rules(spot)

    st.write("")
    html_block(f"<p><strong>Historial Futures</strong> <span class='muted'>· últimos {acc.INCOME_DAYS} días</span></p>")
    income = account_income()
    if isinstance(income, str):
        html_block(f"<p class='warn'>{esc(income)}</p>")
        return
    if income.empty:
        html_block("<p class='muted'>Sin movimientos de Futures en este período.</p>")
        return
    by_type = income.groupby("type")["amount"].sum()
    net = float(by_type.sum())
    html_block("<div class='metrics'>"
               + "".join(metric(name, msg.money(float(by_type.get(t, 0.0)), sign=True), cls_of(by_type.get(t, 0.0)))
                         for t, name in acc.INCOME_TYPES.items())
               + metric("Neto", msg.money(net, sign=True), cls_of(net)) + "</div>")

    days = acc.income_by_day(income, TZ).rename("neto").reset_index().rename(columns={"time": "dia"})
    if len(days) > 1:
        bars = alt.Chart(days).mark_bar(size=6).encode(
            x=alt.X("dia:T", title=None, axis=alt.Axis(grid=False, labelColor=MUTED, domainColor=LINE,
                                                       format="%d-%m", labelAngle=0)),
            y=alt.Y("neto:Q", title=None, axis=alt.Axis(gridColor=LINE, labelColor=MUTED, domain=False,
                                                       format=",.0f", tickCount=5)),
            color=alt.condition("datum.neto >= 0", alt.value(GOOD), alt.value(BAD)),
            tooltip=[alt.Tooltip("dia:T", title="Día", format="%d-%m-%Y"),
                     alt.Tooltip("neto:Q", title="Neto", format=",.2f")])
        st.altair_chart(bars.properties(height=200).configure_view(strokeWidth=0), use_container_width=True)

    table = acc.income_by_symbol(income)
    head = "<tr><th>Par</th>" + "".join(f"<th>{n}</th>" for n in acc.INCOME_TYPES.values()) + "<th>Neto</th></tr>"
    rows = [f"<tr><td>{esc(sym or '—')}</td>" + "".join(f"<td>{signed(float(r[t]))}</td>" for t in acc.INCOME_TYPES)
            + f"<td>{signed(float(r['net']))}</td></tr>" for sym, r in table.iterrows()]
    html_block(f"<table class='kv'>{head}{''.join(rows)}</table>")


# ----------------------------------------------------------- Configuración ----
def capital_control(state, equity: float, env: str) -> None:
    """How much of the account the bot uses. Applied by the bot at its next cycle."""
    assigned = state.assigned_capital if state.assigned_capital is not None else (
        state.simulated_capital if state.simulated_capital is not None else None)
    now = (f"{msg.money(assigned)} asignados · hoy opera con <span class='num'>{msg.money(equity)}</span>"
           if assigned is not None else f"Todo el saldo de Futures · hoy <span class='num'>{msg.money(equity)}</span>")
    html_block(f"<p><strong>Capital del bot</strong><br><span class='muted'>{now}</span></p>")
    if equity < 5:
        html_block("<p class='warn'>Hay menos de $5 en USDT en Futures: el bot no puede abrir operaciones. "
                   "Opera pares USDT, así que el saldo debe estar en USDT (no USDC).</p>")
    pending = [c for c in cmdq.CommandQueue(StateStore(env).dir).pending() if c["kind"] == cmdq.SET_CAPITAL]
    if pending:
        v = pending[-1].get("value")
        st.caption(f"Cambio enviado ({msg.money(v) if v is not None else 'todo el saldo'}); "
                   "se aplica en el próximo ciclo.")
        return
    amount_col, apply_col, all_col = st.columns([2, 1, 1], vertical_alignment="bottom")
    amount = amount_col.number_input("Monto en USDT", min_value=1.0, step=10.0, format="%.2f",
                                     value=max(1.0, float(round(assigned if assigned is not None else equity, 2))),
                                     key=f"capital_{env}")
    if apply_col.button("Aplicar", type="primary", use_container_width=True, key=f"capital_apply_{env}"):
        submit(env, cmdq.SET_CAPITAL, value=float(amount))
    if all_col.button("Usar todo el saldo", use_container_width=True, key=f"capital_all_{env}"):
        submit(env, cmdq.SET_CAPITAL, value=None)
    st.caption("El bot opera con este monto más lo que gane o pierda desde ahora, sin pasar del saldo real. "
               "No mueve dinero: solo limita el tamaño de las operaciones.")
    st.write("")


def config_tab(view, config, state, equity: float, env: str) -> None:
    capital_control(state, equity, env)
    rows = [
        ("Monedas", ", ".join(msg.coin(s) for s in config.universe)),
        ("Señal", f"Ruptura de {config.strategy.donchian_window} velas de {config.strategy.timeframe} (Donchian)"),
        ("Stop", f"{config.initial_stop_atr_multiplier:g}×ATR inicial, luego trailing Chandelier "
                 f"({config.chandelier_window} velas, {config.chandelier_multiplier:g}×ATR)"),
        ("Riesgo por operación", f"{config.risk_pct:.1%} del capital"),
        ("Apalancamiento", f"{config.leverage}x, margen aislado"),
        ("Kill-switch", f"−{config.kill_switch_drawdown:.0%} desde el máximo"),
    ]
    html_block("<table class='kv'>" + "".join(f"<tr><th style='width:34%'>{k}</th><td>{esc(v)}</td></tr>"
                                              for k, v in rows) + "</table>")
    st.write("")
    with st.expander("Cómo funciona"):
        st.markdown(f"""
Cada minuto el bot comprueba que lo que cree tener coincida con Binance y aplica tus controles.

Al cerrar cada vela de {config.strategy.timeframe} (00:00 y 12:00 UTC), revisa cada moneda: si la señal cambió,
cierra; si sigue, sube el stop (nunca lo afloja); si no hay posición y hay señal, entra arriesgando
{config.risk_pct:.1%} del capital.

Los stops viven en Binance, así que protegen las posiciones aunque el bot o el PC se apaguen. Si una orden
queda bajo el mínimo de Binance, se omite en vez de agrandar el riesgo.
""")
    with st.expander("Log técnico"):
        st.code("\n".join(view.log_tail[-100:]) or "(vacío)", language="log")


TESTNET_PAGE = st.Page(testnet_page, title="Testnet", url_path="testnet", default=True)
BINANCE_PAGE = st.Page(binance_page, title="Binance", url_path="binance")
st.navigation([TESTNET_PAGE, BINANCE_PAGE], position="hidden").run()
