"""
Control dashboard (Phase 5/5b, docs/execution_plan.md decisions 23-26).

  streamlit run xrp_futures/monitor/dashboard.py

Editorial, near-monochrome layout (user-chosen): no emojis; color only on gains,
losses and warnings. Status line and a live "what is the bot doing" line on top; tabs
Actividad (activity feed + positions + controls), Historial, Capital, Configuración.
Reads only the files the bot writes under live_state/<env>/; holds NO API keys and
never talks to Binance — controls go through the command inbox and the bot applies
them at its next cycle (<= 5 min).
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

GOOD, BAD, WARN, MUTED = "#4fb286", "#e5675f", "#d6a74a", "#8a919c"
st.markdown(f"""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400&display=swap');
.stApp {{ font-family: 'Inter', sans-serif; }}   /* inherited; icon fonts keep their own family */
header[data-testid="stHeader"], [data-testid="stSidebarCollapsedControl"] {{ display: none; }}
.block-container {{ padding-top: 2.2rem; max-width: 1080px; }}
.num, .t {{ font-family: 'JetBrains Mono', monospace; font-variant-numeric: tabular-nums; }}
.muted {{ color: {MUTED}; }}
.good {{ color: {GOOD}; }} .bad {{ color: {BAD}; }} .warn {{ color: {WARN}; }}
.dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:8px; vertical-align:middle; }}
.status {{ font-size: 0.95rem; margin-bottom: 0.2rem; }}
.standby {{ color: {MUTED}; font-size: 0.9rem; margin-bottom: 1.2rem; }}
.feed-item {{ display:flex; gap:18px; padding:10px 0; border-bottom:1px solid #1f242c; }}
.feed-item .t {{ color:{MUTED}; font-size:0.8rem; min-width:78px; padding-top:2px; }}
.feed-item .detail {{ color:{MUTED}; font-size:0.86rem; margin-top:2px; }}
table.pos {{ width:100%; border-collapse:collapse; font-size:0.88rem; border:none; }}
table.pos th, table.pos td {{ border:none; border-bottom:1px solid #1f242c; text-align:left; }}
table.pos th {{ color:{MUTED}; font-weight:500; padding:6px 4px; }}
table.pos td {{ padding:7px 4px; }}
h3 {{ font-weight:600; letter-spacing:-0.01em; }}
</style>""", unsafe_allow_html=True)

TZ = datetime.now().astimezone().tzinfo    # show times in this PC's local time
LEVEL_CLASS = {"good": "good", "bad": "bad", "warn": "warn", "info": ""}
LABELS = {
    cmdq.PAUSE_BOT: "Pausar bot", cmdq.RESUME_BOT: "Reanudar bot", cmdq.CLOSE_POSITION: "Cerrar",
    cmdq.PAUSE_SYMBOL: "Pausar", cmdq.RESUME_SYMBOL: "Reanudar", cmdq.RESET_KILL_SWITCH: "Reactivar kill-switch",
}


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def signed(x: float | None) -> str:
    """Money amount colored by sign."""
    if x is None:
        return "—"
    cls = "good" if x > 0 else "bad" if x < 0 else ""
    return f"<span class='num {cls}'>{msg.money(x, sign=True)}</span>"


def when(ts: pd.Timestamp, now: pd.Timestamp) -> str:
    local = ts.tz_convert(TZ)
    return local.strftime("%H:%M") if local.date() == now.tz_convert(TZ).date() else local.strftime("%d-%m %H:%M")


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
label = f"<span class='bad'>producción · dinero real</span>" if env == "production" else "<span class='muted'>testnet</span>"
head.markdown(f"<h3 style='margin:0'>Bot Futures <small style='font-size:0.9rem;font-weight:400'>&nbsp;{label}</small></h3>",
              unsafe_allow_html=True)


@st.fragment(run_every="30s")
def main_view() -> None:
    view = data.load(env, CONFIG)
    state, now = view.state, pd.Timestamp.now(tz="UTC")
    if "flash" in st.session_state:
        st.caption(st.session_state.pop("flash"))

    if state is None:
        st.markdown(f"<p class='muted'>El bot todavía no ha corrido en {env}. Inícialo con "
                    "<code>python -m xrp_futures.scripts.run_live_bot</code>.</p>", unsafe_allow_html=True)
        return

    alive, beat = data.heartbeat_status(state, now)
    last = view.equity.iloc[-1] if not view.equity.empty else None
    equity = float(last["equity"]) if last is not None else (state.simulated_capital or 0.0)
    unrealized = float(last["unrealized_pnl"]) if last is not None and "unrealized_pnl" in last else None
    change = f" ({signed(equity - state.simulated_capital)})" if state.simulated_capital else ""

    dot = GOOD if alive else BAD
    st.markdown(
        f"<div class='status'><span class='dot' style='background:{dot}'></span>"
        f"{'Activo' if alive else 'Detenido'} <span class='muted'>· {beat}</span>"
        f" &nbsp;&nbsp; Capital <span class='num'>{msg.money(equity)}</span>{change}"
        f" &nbsp;&nbsp; Realizado {signed(state.realized_pnl)}</div>"
        f"<div class='standby'>{esc(msg.standby_line(state, unrealized, view.last_candle, alive, now, TZ))}</div>",
        unsafe_allow_html=True)
    if view.commands_pending:
        st.caption("Pendiente: " + ", ".join(f"{LABELS[c['kind']]} {msg.coin(c.get('symbol'))}".strip()
                                             for c in view.commands_pending))

    activity, history, capital, config = st.tabs(["Actividad", "Historial", "Capital", "Configuración"])

    with activity:
        feed, side = st.columns([3, 2], gap="large")
        with feed:
            render_feed(view, now)
        with side:
            positions_panel(state)

    with history:
        s = data.trade_summary(view.trades)
        st.markdown(f"<p><span class='num'>{s['n']}</span> operaciones cerradas &nbsp;·&nbsp; "
                    f"<span class='num'>{s['win_rate']:.0%}</span> ganadoras &nbsp;·&nbsp; "
                    f"PnL neto {signed(s['net_pnl']) if s['n'] else '—'}</p>", unsafe_allow_html=True)
        if view.trades.empty:
            st.markdown("<p class='muted'>Todavía no hay operaciones cerradas.</p>", unsafe_allow_html=True)
        else:
            t = view.trades.sort_values("exit_time", ascending=False)
            st.dataframe(pd.DataFrame({
                "Cierre": t["exit_time"].map(lambda x: when(x, now) if pd.notna(x) else "—"),
                "Moneda": t["symbol"].map(msg.coin), "Lado": t["side"],
                "Entrada": t["entry_price"], "Salida": t["exit_price"],
                "Motivo": t["reason"].map({"signal": "señal", "stop": "stop", "manual": "manual"}).fillna(t["reason"]),
                "PnL neto": t["net_pnl"],
            }), hide_index=True, use_container_width=True,
                column_config={"PnL neto": st.column_config.NumberColumn(format="%+.2f")})

    with capital:
        if len(view.equity) > 1:
            st.altair_chart(equity_chart(view.equity, state.simulated_capital or float(view.equity["equity"].iloc[0])),
                            use_container_width=True)
            st.caption("Línea punteada: capital inicial. Horas en la zona horaria de este PC.")
        else:
            st.markdown("<p class='muted'>La curva aparece después de algunos ciclos del bot.</p>",
                        unsafe_allow_html=True)

    with config:
        config_panel(view)


def equity_chart(equity: pd.DataFrame, baseline: float) -> alt.Chart:
    """
    Equity curve that doesn't exaggerate: the y-range spans at least ±2% of the starting
    capital (a -0.1% wiggle must not look like a crash), with a dashed baseline. Times are
    converted to this PC's local time and made tz-naive, so the browser can't re-shift them.
    """
    df = equity[["time", "equity"]].copy()
    df["time"] = df["time"].dt.tz_convert(TZ).dt.tz_localize(None)
    lo = min(df["equity"].min(), baseline * 0.98)
    hi = max(df["equity"].max(), baseline * 1.02)
    y = alt.Y("equity:Q", title=None, scale=alt.Scale(domain=[lo, hi], nice=False),
              axis=alt.Axis(gridColor="#1f242c", labelColor=MUTED, domain=False, format=",.0f", tickCount=5))
    line = alt.Chart(df).mark_line(color="#c9ced6", strokeWidth=1.5).encode(
        x=alt.X("time:T", title=None, axis=alt.Axis(grid=False, labelColor=MUTED, domainColor="#1f242c",
                                                    format="%d-%m %H:%M", labelAngle=0, tickCount=6)),
        y=y,
        tooltip=[alt.Tooltip("time:T", title="Hora", format="%d-%m %H:%M"),
                 alt.Tooltip("equity:Q", title="Capital", format=",.2f")])
    base = alt.Chart(pd.DataFrame({"equity": [baseline]})).mark_rule(
        color=MUTED, strokeDash=[4, 4], strokeWidth=1).encode(y=y)
    return (base + line).properties(height=260).configure_view(strokeWidth=0)


def render_feed(view, now: pd.Timestamp) -> None:
    items = [msg.render(e, TZ) for e in reversed(view.events[-60:])]
    if not items:
        st.markdown("<p class='muted'>Aún no hay actividad registrada.</p>", unsafe_allow_html=True)
        return
    rows = []
    for m in items:
        amount = f"<span class='num {LEVEL_CLASS[m.level]}'>{esc(m.amount or '')}</span>"
        head = esc(m.text).replace(esc(msg.AMOUNT), amount)
        if m.level == "warn" or (m.level == "bad" and msg.AMOUNT not in m.text):
            head = f"<span class='{LEVEL_CLASS[m.level]}'>{head}</span>"
        detail = f"<div class='detail'>{esc(m.detail)}</div>" if m.detail else ""
        rows.append(f"<div class='feed-item'><div class='t'>{when(m.time, now)}</div>"
                    f"<div><div>{head}</div>{detail}</div></div>")
    st.markdown("".join(rows), unsafe_allow_html=True)


def positions_panel(state) -> None:
    if not state.positions:
        st.markdown("<p class='muted'>Sin posiciones abiertas.</p>", unsafe_allow_html=True)
    else:
        body = "".join(
            f"<tr><td>{msg.coin(p.symbol)}</td><td class='muted'>{msg.side_name(p.side)}</td>"
            f"<td class='num'>{msg.num(p.entry_price)}</td><td class='num'>{msg.num(float(p.stop_price))}</td>"
            f"<td class='num muted'>{msg.pct((float(p.stop_price) - p.entry_price) / p.entry_price)}</td></tr>"
            for p in state.positions.values())
        st.markdown("<table class='pos'><tr><th>Posición</th><th></th><th>Entrada</th><th>Stop</th><th></th></tr>"
                    f"{body}</table>", unsafe_allow_html=True)
    if state.paused:
        st.markdown(f"<p class='muted' style='margin-top:.6rem'>En pausa: "
                    f"{', '.join(msg.coin(s) for s in state.paused)}</p>", unsafe_allow_html=True)

    st.write("")
    with st.popover("Controles", use_container_width=True):
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


def config_panel(view) -> None:
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
    st.markdown("<table class='pos'>" + "".join(f"<tr><td class='muted' style='width:34%'>{k}</td><td>{esc(v)}</td></tr>"
                                                for k, v in rows) + "</table>", unsafe_allow_html=True)
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
