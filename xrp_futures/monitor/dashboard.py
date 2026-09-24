"""
Control dashboard (Phase 5, docs/execution_plan.md decision 23).

  streamlit run xrp_futures/monitor/dashboard.py

Shows what the bot is doing from the files it writes under live_state/<env>/, and
offers 4 basic controls. It holds NO API keys and never talks to Binance: controls
are queued in commands.jsonl and the bot applies them at its next cycle (<= 5 min),
with its own safety rules.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # `streamlit run` only adds this file's folder

import altair as alt
import pandas as pd
import streamlit as st

from xrp_futures.config.live import DEFAULT as CONFIG
from xrp_futures.execution import commands as cmdq
from xrp_futures.execution.state import StateStore
from xrp_futures.monitor import data

st.set_page_config(page_title="Bot XRP Futures", page_icon="📈", layout="wide")

COMMAND_LABELS = {
    cmdq.PAUSE_BOT: "Pausar bot", cmdq.RESUME_BOT: "Reanudar bot", cmdq.CLOSE_POSITION: "Cerrar posición",
    cmdq.PAUSE_SYMBOL: "Pausar moneda", cmdq.RESUME_SYMBOL: "Reanudar moneda",
    cmdq.RESET_KILL_SWITCH: "Reactivar kill-switch",
}


def submit(env: str, kind: str, symbol: str | None = None) -> None:
    cmdq.CommandQueue(StateStore(env).dir).submit(kind, symbol)
    st.session_state["flash"] = (f"📨 Enviado: {COMMAND_LABELS[kind]}{' ' + symbol if symbol else ''}. "
                                 f"El bot lo aplica en su próximo ciclo (≤ 5 min).")
    st.rerun(scope="fragment")   # redraw now so the pending-command banner shows immediately


# ---------------------------------------------------------------- sidebar ----
with st.sidebar:
    st.title("📈 Bot XRP Futures")
    envs = data.environments() or ["testnet"]
    default_env = os.getenv("FUTURES_ENVIRONMENT", "testnet")
    env = st.selectbox("Entorno", envs, index=envs.index(default_env) if default_env in envs else 0)
    if env == "production":
        st.error("PRODUCCIÓN — dinero real")
    else:
        st.info("TESTNET — dinero ficticio")
    auto = st.toggle("Auto-actualizar cada 30 s", value=True)

    st.subheader("Configuración")
    st.markdown(
        f"- Monedas: {', '.join(s.replace('USDT', '') for s in CONFIG.universe)}\n"
        f"- Velas: **{CONFIG.strategy.timeframe}**, Donchian **{CONFIG.strategy.donchian_window}**\n"
        f"- Stop: **{CONFIG.initial_stop_atr_multiplier:g}×ATR** inicial, trailing Chandelier "
        f"**{CONFIG.chandelier_window}×{CONFIG.chandelier_multiplier:g} ATR**\n"
        f"- Riesgo por operación: **{CONFIG.risk_pct:.1%}**\n"
        f"- Apalancamiento: **{CONFIG.leverage}x** aislado\n"
        f"- Kill-switch: **-{CONFIG.kill_switch_drawdown:.0%}** desde el máximo\n"
        f"- Capital simulado: **{'$' + format(CONFIG.equity_override_usdt, ',.0f') if CONFIG.equity_override_usdt else 'real'}**"
    )


# ------------------------------------------------------------- main view ----
@st.fragment(run_every="30s" if auto else None)
def main_view() -> None:
    view = data.load(env, CONFIG)
    state = view.state
    now = pd.Timestamp.now(tz="UTC")
    if "flash" in st.session_state:
        st.success(st.session_state.pop("flash"))

    if state is None:
        st.warning(f"El bot todavía no ha corrido en **{env}**. Inícialo con:")
        st.code("python -m xrp_futures.scripts.run_live_bot --once", language="bash")
        return

    alive, beat = data.heartbeat_status(state, now)
    last = view.equity.iloc[-1] if not view.equity.empty else None
    equity = float(last["equity"]) if last is not None else (state.simulated_capital or 0.0)

    c = st.columns(5)
    c[0].metric("Bot", "🟢 Activo" if alive else "🔴 Detenido", beat, delta_color="off")
    c[1].metric("Capital", f"${equity:,.2f}",
                f"{equity - state.simulated_capital:+,.2f}" if state.simulated_capital else None)
    c[2].metric("PnL realizado", f"${state.realized_pnl:+,.2f}")
    c[3].metric("Caída desde máximo", f"{state.kill_switch.drawdown(equity):.1%}")
    c[4].metric("Posiciones abiertas", len(state.positions))

    if state.kill_switch.tripped:
        st.error(f"🛑 KILL-SWITCH ACTIVO: caída de {CONFIG.kill_switch_drawdown:.0%} desde el máximo. "
                 "No se abren operaciones nuevas; las abiertas siguen con sus stops.")
    if state.bot_paused:
        st.warning("⏸️ Bot en pausa: no abre operaciones nuevas. Los stops y salidas siguen funcionando.")
    if state.paused:
        st.warning("Monedas en pausa: " + "; ".join(f"**{s}** ({r})" for s, r in state.paused.items()))
    if view.commands_pending:
        st.info("📨 Comandos pendientes (se aplican en el próximo ciclo): "
                + ", ".join(f"{COMMAND_LABELS[c['kind']]} {c.get('symbol') or ''}" for c in view.commands_pending))

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Posiciones")
        positions = data.positions_frame(state)
        if positions.empty:
            st.caption("Sin posiciones abiertas.")
        else:
            st.dataframe(positions, hide_index=True, use_container_width=True,
                         column_config={"Distancia stop": st.column_config.NumberColumn(format="percent")})

        st.subheader("Capital")
        if len(view.equity) > 1:
            chart = alt.Chart(view.equity).mark_line().encode(
                x=alt.X("time:T", title=None),
                y=alt.Y("equity:Q", title="USDT", scale=alt.Scale(zero=False)),   # small moves stay visible
                tooltip=[alt.Tooltip("time:T", format="%Y-%m-%d %H:%M"), alt.Tooltip("equity:Q", format=",.2f")],
            ).properties(height=220)
            st.altair_chart(chart, use_container_width=True)
        else:
            st.caption("La curva aparece después de algunos ciclos del bot.")

    with right:
        controls(state)

    st.subheader("Operaciones cerradas")
    summary = data.trade_summary(view.trades)
    s = st.columns(4)
    s[0].metric("Operaciones", summary["n"])
    s[1].metric("Ganadoras", f"{summary['win_rate']:.0%}")
    s[2].metric("PnL neto", f"${summary['net_pnl']:+,.2f}")
    s[3].metric("Mejor / peor (USDT)", f"{summary['best']:+.2f} / {summary['worst']:+.2f}")
    if not view.trades.empty:
        cols = [c for c in ["exit_time", "symbol", "side", "qty", "entry_price", "exit_price", "reason",
                            "commission", "funding", "net_pnl"] if c in view.trades]
        st.dataframe(view.trades[cols].sort_values("exit_time", ascending=False), hide_index=True,
                     use_container_width=True)

    a, b = st.columns(2)
    with a:
        st.subheader("Alertas")
        if view.alerts.empty:
            st.caption("Sin alertas.")
        else:
            st.dataframe(view.alerts.sort_values("time", ascending=False), hide_index=True, use_container_width=True)
    with b:
        st.subheader("Comandos aplicados")
        if not view.commands_done:
            st.caption("Ninguno todavía.")
        for c in reversed(view.commands_done[-10:]):
            icon = "✅" if c.get("ok") else "⚠️"
            st.write(f"{icon} {COMMAND_LABELS.get(c['kind'], c['kind'])} {c.get('symbol') or ''} — {c.get('message')}")

    with st.expander("Log del bot (últimas 200 líneas)"):
        st.code("\n".join(view.log_tail) or "(vacío)", language="log")

    st.caption(f"Última vela procesada: {state.last_processed_bar or '—'} · Actualizado {now:%H:%M:%S} UTC")


def controls(state) -> None:
    st.subheader("Controles")
    st.caption("Se aplican en el próximo ciclo del bot (≤ 5 min). El panel no tiene claves ni habla con Binance.")

    if state.bot_paused:
        if st.button("▶️ Reanudar bot", use_container_width=True):
            submit(env, cmdq.RESUME_BOT)
    elif st.button("⏸️ Pausar bot (sin nuevas entradas)", use_container_width=True):
        submit(env, cmdq.PAUSE_BOT)

    with st.container(border=True):
        st.markdown("**Cerrar una posición**")
        if state.positions:
            sym = st.selectbox("Posición", list(state.positions), key="close_sym")
            ok = st.checkbox("Confirmo cerrar a mercado", key="close_ok")
            if st.button("Cerrar posición", disabled=not ok, type="primary", use_container_width=True):
                submit(env, cmdq.CLOSE_POSITION, sym)
            st.caption("Si la señal sigue activa, el bot puede volver a entrar en la próxima vela. "
                       "Para evitarlo, pausa también la moneda.")
        else:
            st.caption("No hay posiciones abiertas.")

    with st.container(border=True):
        st.markdown("**Pausar / reanudar una moneda**")
        sym = st.selectbox("Moneda", list(CONFIG.universe), key="pause_sym")
        p1, p2 = st.columns(2)
        if sym in state.paused:
            if p2.button("Reanudar moneda", use_container_width=True):
                submit(env, cmdq.RESUME_SYMBOL, sym)
            p1.caption(f"En pausa: {state.paused[sym]}")
        elif p1.button("Pausar moneda", use_container_width=True):
            submit(env, cmdq.PAUSE_SYMBOL, sym)
        st.caption("En pausa el bot no gestiona esa moneda; su stop actual sigue activo en Binance.")

    if state.kill_switch.tripped:
        with st.container(border=True):
            st.markdown("**Reactivar kill-switch**")
            ok = st.checkbox("Revisé la caída y quiero reanudar las entradas", key="ks_ok")
            if st.button("Reactivar", disabled=not ok, type="primary", use_container_width=True):
                submit(env, cmdq.RESET_KILL_SWITCH)


main_view()

with st.expander("¿Cómo funciona el bot?"):
    st.markdown(f"""
1. **Cada 5 minutos** el bot compara lo que cree tener con lo que Binance dice (posiciones y stops),
   corrige diferencias y aplica los comandos de este panel.
2. **Cuando cierra una vela de {CONFIG.strategy.timeframe}** (00:00 y 12:00 UTC), para cada moneda:
   - Si hay posición y la señal **cambió** → la cierra.
   - Si la señal **sigue** → sube el stop (Chandelier), nunca lo afloja.
   - Si no hay posición y hay señal → calcula el tamaño para arriesgar **{CONFIG.risk_pct:.1%}** del capital,
     entra a mercado y pone el **stop en Binance** de inmediato.
3. La señal es una **ruptura de {CONFIG.strategy.donchian_window} velas** (Donchian): LONG si el precio rompe
   el máximo de las últimas {CONFIG.strategy.donchian_window} velas, SHORT si rompe el mínimo.
4. Si una orden queda bajo el **mínimo de Binance**, se omite (no se agranda el riesgo).
5. Si el capital cae **{CONFIG.kill_switch_drawdown:.0%}** desde su máximo, el **kill-switch** bloquea entradas nuevas
   hasta que lo reactives aquí.
6. Los stops viven **en Binance**: protegen las posiciones aunque el bot o el PC se apaguen.
""")
