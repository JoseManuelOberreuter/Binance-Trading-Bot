# Plan v2: bot multi-activo (8 perpetuos) — testnet → real

Estado: **diseñado, no construido**. Reemplaza al plan v1 (solo XRPUSDT, ver
historial de git). Cambios respecto a v1: el bot opera un **portafolio de 8
monedas** con la misma estrategia congelada, tamaño de posición sobre el
equity **total** de la cuenta, y apalancamiento usado solo como herramienta de
margen. Ver `project_status.md` para la evidencia detrás de cada decisión.

## Contexto (qué se decidió y por qué)

- **Estrategia congelada, sin cambios**: Donchian(40) + stop inicial 2×ATR +
  trailing Chandelier(5, 4×ATR), velas de 12h, LONG+SHORT, sin filtros. Se
  evaluaron alternativas (entrar/salir antes, 4h, confirmación de reversión,
  x10-x20, micro-mercado) y se descartaron: o ya estaban cubiertas por los
  barridos de robustez, o la evidencia apunta a peor resultado neto.
- **Diversificación = la palanca principal**. Portafolio XRP+ETH+BTC (misma
  config, cero retuning) 2020-2026: Sharpe 0.37 → 0.70, ~17-18% anual
  compuesto en backtest, peor caída mark-to-market -21%. Con correlación media
  0.31 entre activos, 8 monedas capturan ~75% del beneficio máximo teórico de
  diversificar; más allá de 8 el aporte marginal es mínimo.
- **Universo (fijo, sin memecoins)**: BTCUSDT, ETHUSDT, XRPUSDT, BNBUSDT,
  SOLUSDT, ADAUSDT, LTCUSDT, LINKUSDT. Elegido por regla (grandes, líquidas,
  con Futures desde ~2020), no por resultado de backtest.
- **Referencia de backtest (Fase 0, hecha)** — 8 monedas, 0.3%/operación,
  fee 0.05%, equity mark-to-market, stop inicial con el ATR de la última vela
  cerrada (igual que el bot): $1.000 (ene-2020) → $2.376 (sep-2026), ~14%
  anual compuesto, peor caída -20.8%. Por año: 2020 +23.0%, 2021 +51.9%,
  2022 -5.1%, 2023 +16.4%, 2024 -0.2%, 2025 +3.1%, 2026 (parcial) +11.9%.
  `python -m xrp_futures.scripts.run_multi_asset_portfolio`.
- **Por qué 0.3% y no más**: el riesgo es *por posición* y con 8 monedas
  pueden estar las 8 abiertas y detenerse juntas en un crash. Medido:
  1%/op → peor caída -53%; 2%/op → -77%; 0.5% → -32%; 0.4% → -27%.
  0.3% es el mayor nivel que deja la peor caída histórica (-21%) con margen
  bajo el kill-switch de 30%.
- **Expectativa realista en vivo**: por debajo del backtest (~6-10% anual),
  años negativos posibles.

## Decisiones de diseño

1. **Tamaño de posición sobre el equity total** (confirmado por el usuario):
   `notional = equity_total × 0.3% / distancia_al_stop_inicial`. Cada
   operación arriesga ~0.3% de la cuenta; con stop típico ~6-9%, cada
   posición es ~3.5-5% del equity. (El backtest de
   portafolio usó sub-cuentas de 1/N rebalanceadas anualmente; esto es su
   equivalente con rebalanceo continuo — diferencia menor.)
2. **Apalancamiento = margen, no riesgo** (confirmado: **3x**). Margen
   aislado. Con 8 posiciones de ~4% del equity, el notional total ronda ~30%
   del equity → ~10% del capital como margen. Liquidación a ~30% del precio
   de entrada, lejos del stop (~6-9%).
3. **Gate de liquidación con el dato real de Binance**: tras cada entrada,
   leer `liquidationPrice` de `futures_position_information()`; si la
   distancia a liquidación es < 2× la distancia al stop, alertar y cerrar.
   No se reimplementa la fórmula de márgenes (se descarta
   `approx_liquidation_distance_pct()` para uso en vivo).
4. **Límite de exposición del portafolio** — *propuesto, falta confirmar
   valor*: notional total abierto ≤ 100% del equity (con 0.3% no debería
   activarse nunca; es una red de seguridad ante un bug de tamaño). Si una
   entrada lo superaría, se omite y se registra.
5. **Kill-switch operacional** (confirmado: **30%**, independiente de la
   estrategia): si el equity cae 30% desde su máximo, se bloquean nuevas entradas, se alerta, y las
   posiciones abiertas siguen gestionadas por sus stops. Reanudar es una
   decisión manual. No se usa `RiskEngine` (no fue parte del resultado
   validado).
6. **Stop real del lado del exchange**: `STOP_MARKET` `reduceOnly` por
   posición, reemplazado en cada cierre de vela de 12h cuando el Chandelier
   se ajusta (solo se mueve a favor). Si el bot se cae, el stop sigue vivo en
   Binance.
7. **Binance es la fuente de verdad**: al arrancar y en cada ciclo, el bot
   reconcilia su estado local con las posiciones y órdenes abiertas reales
   (posición sin stop → coloca stop; stop huérfano → lo cancela; diferencia
   inexplicable → alerta y no opera ese símbolo).
8. **Polling, sin websockets**: loop cada ~5 min; actúa solo cuando cerró
   una vela de 12h nueva (00:00 y 12:00 UTC), procesando los 8 símbolos en
   orden. Modo de posición one-way (no hedge).
9. **Señal en vivo = la misma función del backtest**
   (`strategy_a_trend.generate` con params congelados) sobre velas recién
   descargadas. Test de regresión garantiza señal idéntica en vivo y en
   backtest.
10. **Filtros por símbolo leídos del exchange** (`futures_exchange_info()`):
    tick size, step size, notional mínimo. Orden bajo el mínimo → se omite y
    se registra.
11. **Costos**: taker fee real actual 0.05% (VIP0) — ya aplicado en
    `backtest/costs.py` (Fase 0), así la comparación vivo-vs-backtest es justa.
12. **Dashboard desacoplado y de solo lectura** (Streamlit): lee
    `state.json`, `trades.jsonl`, `equity_log.jsonl`; no tiene credenciales
    de trading ni puede enviar órdenes.
13. **Credenciales**: nuevas `FUTURES_TESTNET_API_KEY` /
    `FUTURES_TESTNET_SECRET` (testnet.binancefuture.com, distintas de las de
    Spot testnet). Producción: `BINANCE_API_KEY` / `BINANCE_SECRET` con
    permiso de Futures, **sin permiso de retiro**.
14. **El Spot del usuario (holding) queda fuera de alcance**, garantizado por
    Binance y no solo por el código: la API key de producción tiene solo
    permiso de Futures (Spot trading y retiros desactivados); el bot no hace
    transferencias entre billeteras (el USDT se pasa a Futures a mano); la
    cuenta NO usa Portfolio Margin (que tomaría el Spot como colateral).
15. **Capital mínimo por los mínimos de orden reales** (consultados en
    `fapi/v1/exchangeInfo`, sep-2026): BTC ~$86 (0.001 BTC; minNotional 50),
    ETH/LTC/LINK $20, XRP/SOL/ADA $5, BNB ~$8. A 0.3% de riesgo, capital
    para operar cada moneda ~90% del tiempo: XRP/BNB/SOL/ADA ~$200-220, ETH
    ~$710, LTC ~$850, LINK ~$900, BTC ~$1.950 → **~$2.000 para las 8**.
    Con menos capital el bot omite (y registra) las órdenes bajo el mínimo.

16. **Señal con datos del mercado real** (confirmado): velas de producción
    (públicas, sin claves) aun operando en testnet; los stops se disparan con
    los precios de la testnet.
17. **Capital simulado dinámico** (confirmado): en testnet el equity para
    dimensionar = $250 + PnL realizado del bot desde que arrancó (no fijo).
18. **Posiciones ajenas: no tocar y avisar** (confirmado): toda orden del bot
    lleva `newClientOrderId` con prefijo `xfb_`; el bot nunca modifica ni
    cancela órdenes sin ese prefijo. Una posición que el bot no abrió deja esa
    moneda en pausa + alerta; no se cierra.

## Fase 3 — diseño (lógica pura, sin red)

- `sizing.py` — `plan_entry(...)` → `EntryPlan(qty, notional, stop_price)` o
  `Skip(reason)`: notional = equity × risk_pct / (2×ATR_última_vela / precio);
  redondeo con los filtros; `Skip` si queda bajo el mínimo, si superaría el
  tope de exposición, o si el kill-switch está activo. Stop inicial = precio
  ∓ 2×ATR, redondeado al tick.
- `kill_switch.py` — `KillSwitch.update(equity)`: registra el máximo; al caer
  30% desde él queda **bloqueado** (latch) hasta un reset manual explícito.
- `state.py` — `BotState` (posiciones con qty/entrada/stop/id de orden de
  stop, última vela procesada, máximo de equity, kill-switch, PnL realizado)
  en `live_state/<entorno>/state.json` (gitignored), escritura atómica
  (archivo temporal + rename); `trades.jsonl` y `equity_log.jsonl` append-only.
- `reconcile.py` — `reconcile(state, posiciones_exchange, ordenes_exchange)` →
  lista de acciones, sin ejecutarlas: `PLACE_STOP` (posición sin stop),
  `RECORD_CLOSED` (el stop saltó con el bot apagado), `CANCEL_ORPHAN_STOP`
  (stop `xfb_` sin posición), `CANCEL_DUPLICATE_STOP`, `PAUSE_SYMBOL` +
  alerta (posición ajena o cantidad distinta). Ignora órdenes sin `xfb_`.
- Tests por módulo, incluida una tabla de escenarios de reconciliación.

## Fase 4 — diseño (primeras órdenes, solo testnet)

**Hallazgo que cambia el diseño:** desde 2025-12-09 Binance USDⓈ-M enruta las
órdenes condicionales (`STOP_MARKET`, etc.) al servicio de *algo orders*: se
crean con `clientAlgoId` (no `newClientOrderId`), se listan con
`futures_get_open_algo_orders()` (NO aparecen en `futures_get_open_orders()`)
y se cancelan con `futures_cancel_algo_order()`. `reconcile` y
`check_futures_connectivity` deben leer los stops desde ahí.

Decisiones confirmadas:
19. **Trigger del stop = último precio** (`workingType=CONTRACT_PRICE`), igual
    que el backtest (máximos/mínimos de vela). `priceProtect` desactivado
    (podría impedir que el stop se dispare en un crash).
20. **Prueba inicial autorizada** (testnet): abrir ~$6 de XRP, poner un stop
    algo, verificar que aparece en `openAlgoOrders`, cancelarlo y cerrar —
    confirma el formato real de las respuestas antes de construir el motor.
21. **Arranque:** 1 ciclo `--dry-run` revisado juntos, luego testnet real.
22. **Alertas:** consola + `live_state/<entorno>/bot.log` + `alerts.jsonl`
    (Telegram después).
23. **Frontend de control (Streamlit, después del motor)** — reemplaza el
    dashboard "solo lectura" de la decisión 12 por uno con 4 operaciones:
    pausar/reanudar el bot, cerrar una posición, pausar/reanudar una moneda,
    reactivar el kill-switch. El frontend **no tiene claves ni habla con
    Binance**: escribe en `live_state/<entorno>/commands.jsonl`
    (`execution/commands.py`) y el bot los aplica en su siguiente ciclo con
    sus propias reglas; el resultado queda en `commands_done.jsonl`.

Módulos:
- `order_manager.py` — `setup_symbol` (margen aislado + 3x; tolera "no need
  to change"), `open_market` (MARKET, `newClientOrderId` `xfb_e_…`, lee
  precio/cantidad ejecutados reales), `place_stop` (algo `STOP_MARKET`,
  `reduceOnly`, `quantity` exacta, `clientAlgoId` `xfb_s_…`), `replace_stop`
  (**crea el nuevo antes de cancelar el viejo** — nunca sin protección),
  `close_market` (MARKET `reduceOnly`), `cancel_stop`.
- `engine.py` — ciclo cada ~5 min: (1) snapshot de Binance → `reconcile` →
  ejecutar acciones (`RECORD_CLOSED` calcula el PnL real con
  `futures_account_trades` + funding de `futures_income_history`); (2) si
  cerró una vela de 12h nueva, por símbolo no pausado, con las reglas del
  backtest: salida si la señal cambió (cerrar + cancelar stop), trailing del
  Chandelier si sigue (solo a favor → `replace_stop`), entrada si está plano
  y la señal ≠ 0 (incluye re-entrada tras un stop, como el backtest); salidas
  antes que entradas; (3) equity simulado, kill-switch, logs, `state.save()`.
- `scripts/run_live_bot.py` — `--dry-run` (decide y muestra, no envía),
  `--once` (un ciclo). Producción exige `FUTURES_ENVIRONMENT=production`
  **y** un flag explícito.
- Tests con un Binance falso en memoria: entrada + stop, trailing, cambio de
  dirección, stop ejecutado con el bot apagado, kill-switch, dry-run sin
  órdenes.

Diferencia conocida con el backtest: el backtest entra al precio de apertura
de la vela; el bot entra hasta ~5 min después (polling) a precio de mercado.

## Fase 5b — rediseño minimalista + bitácora (✅ construido)

Decisiones confirmadas por el usuario:
24. **Una página + pestañas.** Arriba: línea de estado (🟢 Activo · capital ·
    PnL · próxima vela en Xh Ym) y línea de espera en vivo. Pestaña
    **Actividad** (por defecto): bitácora + posiciones compactas con los
    controles en un **menú plegable** al lado. Pestañas **Historial**
    (operaciones cerradas), **Capital** (gráfico) y **Configuración** (parámetros,
    "cómo funciona", log técnico). Se elimina la barra lateral cargada y las
    5 métricas.
25. **Bitácora en lenguaje humano, basada en eventos, no en el log.** El
    motor escribe eventos estructurados en `live_state/<env>/events.jsonl`
    (`execution/events.py`); el panel los traduce a español
    (`monitor/messages.py`, funciones puras con tests). `bot.log` queda
    solo para depurar.
26. **Espera = línea en vivo arriba**, recalculada cada refresco (no se
    escribe una entrada por ciclo): "💤 En espera — próxima vela en 2h 13m ·
    4 posiciones · no realizado +$0.12 · mercado: 7 de 8 alcistas".

27. **Estilo editorial, sin emojis** (elegido por el usuario tras revisar
    referencias de dashboards de trading casi monocromáticos): tema oscuro
    neutro (`.streamlit/config.toml`), Inter + números en monoespaciado,
    color solo en ganancias/pérdidas (verde/rojo) y avisos (ámbar), frases
    cortas con titular + línea de detalle, sin cabecera ni botón "Deploy".
    Los mensajes de ejemplo de abajo son del primer borrador (con emojis);
    el texto vigente está en `monitor/messages.py` y sus tests.

28. **Reestructura en 5 pestañas** (el inicio anterior resultaba recargado):
    **Inicio** = solo el capital total en grande + el resultado acumulado de
    cada una de las 8 monedas (cerradas + abierta, como % del capital
    inicial) + una línea de estado; **Consola** = bitácora + resumen
    `STATUS` cada 30 min ("sigo activo…", `LiveConfig.status_every_min`) +
    controles; **Monedas** = detalle por moneda (posición, precio y PnL en
    vivo del último ciclo, stop, señal, historial); **Capital** = realizado,
    no realizado, caída desde el máximo y gráfico 4 h / 1 día;
    **Configuración**. El bot guarda además, en cada ciclo, precio y PnL por
    posición (`equity_log.positions`) y las señales de la última vela
    (`state.last_signals`).

Eventos (tipo → mensaje):
- `BOT_STARTED` → "▶️ Bot iniciado en testnet con $250 simulados."
- `CANDLE_PROCESSED` (señales por moneda, acciones) → "🕛 Vela de las 12:00
  cerrada. Señales: 7 alcistas, 1 bajista. Acciones: 1 stop movido."
- `POSITION_OPENED` (qty, precio, stop, riesgo) → "🟢 Abrí LONG en XRP: 5.5 a
  $1.5384 (~$8.46). Stop $1.4028 (-8.8%); si salta pierdo ~$0.75 (0.3%)."
- `STOP_MOVED` (antes, después, ganancia asegurada) → "🔼 Subí el stop de SOL
  de $109.44 a $112.10: aseguro +$0.05."
- `POSITION_CLOSED` (motivo signal/stop/manual, PnL, duración) → "🔵 Cerré BNB
  por cambio de señal: +$0.42 (+2.7%) en 3 días." / "🔴 Saltó el stop de
  ADA a $0.2274: -$0.75."
- `ENTRY_SKIPPED` (motivo, montos) — **una vez por moneda y vela** →
  "⏭️ BTC con señal LONG, pero la orden mínima ($84) supera lo que
  corresponde ($17.8)."
- `COMMAND_APPLIED` → "⏸️ Pausaste el bot desde el panel."
- `ALERT` / `KILL_SWITCH` → "⚠️ …" / "🛑 Kill-switch activado …"
- `CYCLE_ERROR` (desde el loop de `run_live_bot`) → "⚠️ No pude conectar con
  Binance; reintento en 5 min. Tus stops siguen activos en Binance."

Datos para la línea de espera: `last_cycle_at`, posiciones y no realizado del
último `equity_log`, resumen de señales del último `CANDLE_PROCESSED`, y la
próxima vela calculada en el panel (00:00/12:00 UTC).

Tests: el motor emite los eventos esperados (Binance falso); cada evento se
traduce al mensaje esperado; los `ENTRY_SKIPPED` no se repiten dentro de una
misma vela.

## Infraestructura 24/7 — Oracle Cloud (decisión del usuario)

Para que el bot y el panel funcionen sin el PC encendido. Se evaluó Vercel +
Supabase y se descartó: Streamlit no corre en Vercel (serverless, sin
conexiones persistentes), el cron cada 5 min exige plan Pro, las funciones
salen por IPs de EE.UU. (Binance Futures responde 451) y no hay IP fija para
restringir la API key. Elegido: **VM Oracle Cloud Always Free** (Ubuntu 24.04,
Ampere A1, 1 OCPU / 6 GB), creada por el usuario.

29. **Acceso privado con Tailscale** (gratis): el panel escucha solo en
    `127.0.0.1:8501`; `tailscale serve` lo publica únicamente dentro de la red
    privada del usuario (PC y celular con la app). Ningún puerto abierto a
    internet salvo SSH.
30. **Zona horaria del servidor: America/Santiago** (horas del panel).
31. **En el servidor solo van las claves de Futures testnet** mientras dure la
    Fase 6; las de producción se agregan al pasar a la Fase 7, con la API key
    restringida a la IP fija de la VM.

Archivos en `deploy/`: `xfb-bot.service` y `xfb-dashboard.service` (systemd,
reinicio automático), `setup_server.sh` (instalación única en la VM),
`deploy.sh` (desde el PC: sube el código commiteado con `git archive`, nunca
`.env` ni `live_state/`, y reinicia los servicios).

Secuencia: (1) crear VM y probar que `fapi.binance.com` no responda 451 desde
ahí; (2) `deploy.sh` + `setup_server.sh`; (3) copiar `.env` (solo claves
testnet) y **detener el bot del PC y copiar `live_state/testnet`** para que el
servidor reconozca las posiciones abiertas; (4) arrancar servicios y
verificar un ciclo sin alertas; (5) `tailscale up` + `tailscale serve`.
Actualizaciones posteriores: commit en el PC y `deploy/deploy.sh <ip> <llave>`.

## Fase 7 adelantada — bot real y vista de la cuenta (decisiones del usuario, 11-oct-2026)

El usuario decidió preparar el bot con dinero real sin esperar los 1-2 meses de
testnet de la Fase 6. Para que eso no implique operar sin querer, el bot real
**arranca en pausa** y solo abre posiciones cuando el usuario lo activa.

32. **Riesgo por operación: 0.6%** del equity total (antes 0.3%). Elegido tras
    el estudio de escalamiento con $250 en 4 monedas: peor caída histórica
    mark-to-market ~−24%, mejor año ~+96%. Sin recordatorio de retiro: se
    acumula el capital.
33. **Bot real = misma estrategia y las 8 monedas**, dimensionado sobre el
    **saldo real de Futures** (`config_for("production")`: sin capital
    simulado). Con ~$80 la mayoría de las señales se omiten por el mínimo de
    Binance (solo XRP, BNB, SOL y ADA caben); el usuario agregará capital.
34. **Arranca en pausa** (`start_paused`): el primer inicio no abre nada. El
    botón **Activar / Desactivar bot** de Inicio envía RESUME/PAUSE por el
    buzón de comandos. Desactivado = no abre nada nuevo; los stops y salidas
    siguen protegiendo lo abierto.
35. **Solo el bot opera la cuenta de Futures** (sin trading manual en
    paralelo), para que capital y resultados sean 100% del bot.
36. **Margen en USDT**: el bot opera pares USDT con margen aislado, así que
    el saldo debe estar en USDT. El modo Multi-Assets no sirve (exige margen
    cruzado).
37. **Dashboard: selector Testnet | Binance** arriba a la derecha; las dos
    vistas nunca comparten pantalla. Binance muestra el bot real y una pestaña
    **Cuenta** de solo lectura (`monitor/account.py`): Spot, billetera y
    posiciones de Futures (incluidas las manuales) e historial de 90 días
    (PnL, comisiones, funding), actualizado cada minuto. Usa
    `BINANCE_API_KEY`, restringida a la IP pública del PC; el módulo solo
    llama endpoints de lectura (verificado por un test).

Arranque: `FUTURES_ENVIRONMENT=production` + `run_live_bot --allow-production`,
luego **Activar bot** en la vista Binance. Simulación previa con
`--dry-run --once`: lee la cuenta real y no envía nada.

## Revisión de errores fatales (11-oct-2026, antes de activar dinero real)

Revisión del código más fallas conocidas de bots en Binance Futures (timeouts con
estado desconocido, crash del 10-oct-2025, reloj desfasado). Arreglado y con tests:

38. **Entrada con respuesta perdida**: la entrada se guarda como *pendiente* (con su
    `clientOrderId`) antes de enviarla. Si la respuesta se pierde (timeout, `-1007`,
    `-1006`, red), el bot consulta la orden por ese id; si se ejecutó la adopta y le pone
    su stop. Si Binance no la conoce tras 2 minutos, se da por no ejecutada. Un rechazo
    claro (p.ej. `-2019` margen insuficiente) no queda pendiente. Antes, ese caso dejaba
    una posición real **sin stop**, pausada como "posición ajena".
39. **Un solo bot por entorno**: candado del sistema operativo en
    `live_state/<env>/bot.lock`; un segundo proceso se niega a arrancar. Se libera solo
    si el proceso muere.
40. **Margen aislado obligatorio**: antes de cada entrada se verifica en Binance que la
    moneda esté en margen aislado al apalancamiento del bot; si no, no entra y avisa.
    En margen cruzado una liquidación puede vaciar la billetera, y con más
    apalancamiento la liquidación queda antes que el stop.
41. **Cada moneda falla por separado**: un error en una moneda no frena a las demás. Si
    falla una salida, la vela no se marca como procesada y se reintenta en el próximo
    ciclo (el stop sigue protegiendo).
42. **Reloj sincronizado con Binance** en cada ciclo (`timestamp_offset`), para evitar
    `-1021` cuando el PC se suspende o se desfasa.

Pendiente de decisión del usuario: stops por precio *last* (como el backtest) o por
*mark* (ignora mechas momentáneas). Conocido y aceptado: con el PC suspendido o con
cambio de IP pública el bot queda ciego (los stops siguen en Binance); los retiros de
Futures cuentan como caída para el kill-switch.

## Archivos

```
xrp_futures/config/live.py          # dataclass LiveConfig: universo, risk_pct, leverage,
                                    # kill-switch %, cap de exposición, entorno testnet|production
xrp_futures/execution/
    client.py            # get_futures_client(): testnet/producción según .env
    symbol_filters.py    # filtros por símbolo, round_price/round_qty/min_notional
    live_signal.py       # velas 12h recientes -> generate() -> señal, ATR, nivel Chandelier
    sizing.py            # notional sobre equity total + cap de exposición (función pura)
    order_manager.py     # entrada market, stop reduceOnly, reemplazo de stop, cierre
    reconcile.py         # estado local vs posiciones/órdenes reales en Binance
    state.py             # state.json + trades.jsonl + equity_log.jsonl
    kill_switch.py       # función pura: drawdown desde el máximo -> bloquear entradas
    engine.py            # loop principal: vela nueva -> por símbolo: señal -> acciones
xrp_futures/monitor/dashboard.py    # Streamlit solo lectura
xrp_futures/scripts/
    check_futures_connectivity.py   # Fase 1: solo lectura contra testnet
    run_live_bot.py                 # entrypoint
xrp_futures/tests/
    test_execution_sizing.py, test_execution_symbol_filters.py,
    test_execution_kill_switch.py, test_execution_reconcile.py,
    test_execution_live_signal.py   # todos sin red
```

## Fases (cada una es un checkpoint; nada de dinero real antes de la Fase 7)

| Fase | Qué | Criterio para avanzar |
|---|---|---|
| 0 | ✅ Hecha: fee 0.05%, equity mark-to-market en el motor, referencia del portafolio de 8 (ver Contexto) | — |
| 1 | ✅ Hecha: `client` + `symbol_filters` + `check_futures_connectivity` (testnet: 5.000 USDT, one-way, 8 símbolos OK) | — |
| 2 | ✅ Hecha: `live_signal` + test de regresión (>1.000 puntos sintéticos) + `show_live_signals --compare-cache` (datos reales: 100% igual en las 8) | — |
| 3 | ✅ Hecha: `sizing`, `kill_switch`, `state`, `reconcile` + 33 tests (lógica pura) | — |
| 4 | ✅ Hecha: `order_manager` + `engine` + `run_live_bot`; primer ciclo real en testnet (24-sep): 4 posiciones + 4 stops algo verificados en Binance, sin duplicados en ciclos siguientes. Pendiente: ver un trailing y una salida reales | — |
| 5 | ✅ Hecha: dashboard Streamlit con 4 controles vía buzón de comandos (`streamlit run xrp_futures/monitor/dashboard.py`) | — |
| 6 | Testnet desatendido, **1-2 meses**, en PC siempre encendido o VPS | Sin errores de estado; operaciones coinciden con un backtest de la misma ventana |
| 7 | **Adelantada (11-oct)**: bot real listo, arranca en pausa (decisiones 32-37). **Real con capital chico**, 3-6 meses (monto a decidir: ~$2.000 para operar las 8; con menos se omiten monedas — ver decisión 15) | Slippage/comisiones reales cercanos al modelo; resultado dentro de lo esperable |
| 8 | Decisión de escalar capital | Decisión manual, con los datos de la Fase 7 |

**Regla de detención (fijada antes de empezar):** caída de 30% desde el
máximo → kill-switch y revisión manual; no se "ajustan parámetros" en
caliente para recuperar pérdidas.

## Verificación

- `python -m pytest xrp_futures/tests/` — sin llamadas de red en la suite.
- `python -m xrp_futures.scripts.check_futures_connectivity` contra testnet.
- Testnet: comparar `trades.jsonl` contra un backtest de la misma ventana.
- Revisión de código: el dashboard no puede enviar ninguna acción.
