# Plan: capa de ejecución live/testnet + dashboard de monitoreo

Estado: **diseñado, no construido**. Nada de lo descripto acá existe en el
repo todavía — es el plan acordado para cuando se retome esta parte. Ver
`project_status.md` §5 para el resto de los hilos abiertos.

## Contexto

`xrp_futures/` fue, hasta ahora, un sistema puro de investigación/backtest —
ningún código habla con Binance. La estrategia (Donchian(40) + Chandelier
trailing stop(5, 4x), 12h, long+short, riesgo 1%/operación) tiene un
resultado OOS walk-forward validado (CAGR ~7.0%, Sharpe 0.31, peor MaxDD
-8.5%, bajo costos BASE) y pasó Monte Carlo + stress testing en una ronda
anterior (ver `research_studies.md`). Un hilo de investigación paralelo que
intentaba *mejorar* ese edge (Trend Strength → clasificación de fallas →
mecanismo de confirmación de reversión) todavía no produjo una mejora
validada (Stage 4b empeoró Sharpe/MaxDD) — así que este plan despliega **el
baseline ya validado tal cual**, no la variante experimental.

El objetivo es pasar de "solo backtest" a un bot real: conectar a Binance
**Futures** (no Spot — una superficie de API distinta a la del bot legacy),
primero en testnet, más un dashboard de monitoreo básico en Streamlit,
**solo lectura**, para no operar el bot exclusivamente desde consola. Este
plan no coloca ninguna orden — solo construye la capacidad, con testnet
como default obligatorio.

**Patrones reutilizados** (de `legacy/spot_grid_bot/`, confirmados leyendo el
código):
- `exchange/client.py`: toggle `ENVIRONMENT=testnet|production` vía `.env`,
  `python-binance` `Client`, override manual de la URL para testnet. Para
  Futures el equivalente es `client.FUTURES_URL` (constante de clase
  `FUTURES_TESTNET_URL = "https://testnet.binancefuture.com/fapi"` en la
  versión instalada, `python-binance==1.0.35`, confirmada).
- `bot.py`: loop simple `while True` + `try/except Exception` (nunca
  crashea el loop) + `time.sleep(N)`, `rich.console.Console()` para status,
  log de operaciones en archivo plano (no DB — `logs/trades.db` ya se había
  abandonado en favor de un archivo plano), helpers locales de
  redondeo/filtros (`round_price`/`round_qty`/`ensure_min_notional`)
  sacados de los filtros del exchange.
- `xrp_futures/risk/engine.py`'s `RiskEngine`/`liquidation_gate` existen
  pero **no** son parte del baseline validado (ese resultado usó
  `risk_engine=None`) — ver decisión de diseño #1 abajo.

## Decisiones de diseño clave

1. **No usar `RiskEngine`.** El número validado (7.0% CAGR / Sharpe 0.31) se
   generó con `risk_engine=None`. Agregarlo en vivo significaría correr algo
   que nunca se backteó. En su lugar: un **kill-switch operacional**
   independiente y simple (piso de equity desde el inicio de la sesión —
   bloquea nuevas entradas, alerta, sigue gestionando la posición existente,
   si el equity cae más allá de un % configurable) que nunca toca el
   tamaño de posición — es una red de seguridad separada de la lógica de
   estrategia.
2. **El trailing stop es una orden real `STOP_MARKET reduceOnly` del lado
   del exchange**, cancelada/reemplazada cada vez que el trail del
   Chandelier se ajusta (una vez por vela de 12h cerrada — mismo ritmo que
   el backtest). No monitoreado por software: si el proceso del bot está
   caído, el stop tiene que poder dispararse igual del lado de Binance. No
   es negociable dado que "gestión de riesgo estricta" fue la premisa
   fundacional del proyecto.
3. **Seguridad de liquidación: leer el `liquidationPrice` real de Binance**
   desde `futures_position_information()` después de cada entrada/cambio de
   apalancamiento, en vez de tratar de mejorar la fórmula aproximada del
   backtest (`approx_liquidation_distance_pct()`). Binance ya calcula la
   cifra exacta del lado del servidor — no hace falta reimplementar su
   fórmula de márgenes escalonados. Gate: rechazar/alertar si
   `|liquidationPrice - markPrice| / markPrice` está por debajo de un
   buffer de seguridad.
4. **Sin websockets.** La estrategia opera en 12h. Un loop de polling simple
   (cada ~5 min, barato en rate limits) que chequea "¿cerró una vela de 12h
   nueva desde la última vez que actué?" alcanza, y coincide con el propio
   patrón de polling del bot legacy — no hace falta la complejidad extra de
   `ThreadedWebsocketManager`.
5. **Hacen falta credenciales nuevas de Futures testnet.** Las
   `TESTNET_API_KEY`/`TESTNET_SECRET` actuales en `.env` son de Binance
   **Spot** testnet (`testnet.binance.vision`) y no van a funcionar contra
   `testnet.binancefuture.com` — es un faucet/cuenta totalmente separado.
   Nuevas env vars: `FUTURES_TESTNET_API_KEY` / `FUTURES_TESTNET_SECRET`.
   Producción de Futures típicamente reusa la misma API key de cuenta
   (`BINANCE_API_KEY`/`BINANCE_SECRET`) con el permiso de Futures habilitado
   — este plan asume eso pero no requiere verificarlo hasta que producción
   esté sobre la mesa, lo cual no es parte del alcance de este plan.
6. **El dashboard es completamente desacoplado y de solo lectura.** Lee
   archivos planos que el motor en vivo escribe (`state.json`,
   `trades.jsonl`, `equity_log.jsonl`) — no importa el motor en vivo ni
   tiene sus propias credenciales de trading. No puede colocar, modificar ni
   cancelar nada. Coincide con la elección explícita del usuario (solo
   monitoreo, Streamlit).
7. **El cómputo de señal reutiliza la función de estrategia del backtest sin
   modificarla** (`strategy_a_trend.generate` con los params congelados)
   sobre velas recién descargadas en cada ciclo — sin reimplementación
   incremental/con estado de Donchian/ATR/Chandelier, que sería un segundo
   lugar donde esas fórmulas podrían divergir de las del backtest. Un test
   de regresión asegura que el camino de código en vivo reproduce
   exactamente la misma serie de señal que el camino de backtest sobre los
   mismos datos históricos.

## Archivos nuevos

```
xrp_futures/execution/
    __init__.py
    client.py            # get_futures_client() -> Client, espejo de exchange/client.py
                          # del bot legacy pero para endpoints/URL testnet/env vars de Futures
    symbol_filters.py     # trae + cachea futures_exchange_info() filters; round_price/round_qty/
                          # ensure_min_notional, adaptado de los helpers de bot.py para filtros de futures
    live_signal.py         # trae las últimas ~200 velas de 12h vía futures_klines, corre la MISMA
                          # strategy_a_trend.generate(bars, FROZEN_PARAMS) del backtest,
                          # devuelve la señal de la barra actual + atr + nivel de trail del chandelier
    order_manager.py      # place_entry(), replace_stop_order(), close_position() — wrappers finos
                          # sobre futures_create_order con redondeo de symbol_filters y stops reduceOnly
    state.py              # StateStore: carga/guarda state.json (posición, última barra procesada,
                          # estado del kill-switch), agrega a trades.jsonl / equity_log.jsonl
    kill_switch.py         # función pura: dado equity de inicio de sesión + equity actual, devuelve
                          # si hay que bloquear nuevas entradas (independiente de RiskEngine)
    engine.py             # el loop principal: detección de cierre de vela -> live_signal -> comparar
                          # con el estado actual -> acciones de order_manager -> state.save(), espejo
                          # de la estructura while True / try-except-sleep / status en rich de bot.py

xrp_futures/scripts/
    check_futures_connectivity.py   # script de la Fase 1: chequeo de solo lectura (balance de cuenta,
                                     # info de posición, mark price) contra TESTNET — sin órdenes
    run_live_futures_bot.py         # entrypoint: `python -m xrp_futures.scripts.run_live_futures_bot`

xrp_futures/monitor/
    __init__.py
    dashboard.py           # app Streamlit: equity curve, posición actual, tabla de operaciones
                            # recientes, salud del bot/último heartbeat — lee solo los archivos que
                            # escribe engine.py

xrp_futures/tests/
    test_execution_symbol_filters.py   # matemática de redondeo/filtros, funciones puras, sin red
    test_execution_kill_switch.py      # función pura, tabla de escenarios de equity
    test_execution_live_signal.py      # regresión: el camino de live_signal reproduce la señal de
                                        # strategy_a_trend.generate sobre las mismas barras fixture
```

`.env.example` suma `FUTURES_TESTNET_API_KEY` / `FUTURES_TESTNET_SECRET`
(documentadas como "de testnet.binancefuture.com, separadas de las keys de
Spot testnet de arriba"). `requirements.txt` suma `streamlit`. `CLAUDE.md`
gana una subsección documentando los paquetes `execution`/`monitor` y sus
comandos de ejecución.

## Secuencia de construcción (cada paso es un checkpoint independiente —
ninguna orden real se coloca antes del paso 4, y aun ahí solo en testnet)

1. `execution/client.py` + `execution/symbol_filters.py` + `scripts/
   check_futures_connectivity.py` — probar que la conexión testnet y el
   wiring de credenciales funcionan (solo llamadas de lectura: balance,
   info de posición, filtros del exchange, mark price).
2. `execution/live_signal.py` + su test de regresión — probar que el camino
   de señal en vivo coincide exactamente con el del backtest, todavía sin
   escrituras de red más allá de klines públicos.
3. `execution/state.py` + `execution/kill_switch.py` + tests — lógica pura,
   sin red.
4. `execution/order_manager.py` + `execution/engine.py` + `scripts/
   run_live_futures_bot.py` — primer punto donde se pueden colocar órdenes
   reales en testnet. Correr solo contra **testnet**, tamaño chico, y
   supervisado manualmente por al menos un ciclo completo de 12h antes de
   considerarlo capaz de correr desatendido.
5. `monitor/dashboard.py` (`streamlit run xrp_futures/monitor/dashboard.py`)
   — una vez que el paso 4 esté produciendo archivos reales de estado/
   operaciones para leer.

## Verificación

- `python -m pytest xrp_futures/tests/` — todos los tests nuevos de lógica
  pura (filtros, kill-switch, regresión de live-signal) deben pasar junto
  al resto de la suite; sin llamadas de red en la suite de tests (mismo
  criterio que ya existe).
- `python -m xrp_futures.scripts.check_futures_connectivity` contra
  testnet — corrida manual, confirma que credenciales/URLs están bien
  conectadas antes de construir nada relacionado a órdenes.
- Observación manual en testnet: correr `run_live_futures_bot.py` a través
  de al menos un cierre de vela de 12h real, confirmar que la secuencia de
  entrada/reemplazo de stop/salida en `trades.jsonl` coincide con lo que un
  backtest sobre esa misma ventana en vivo hubiera producido.
- `streamlit run xrp_futures/monitor/dashboard.py` — confirmar visualmente
  que equity curve/posición/tabla de operaciones se renderizan desde los
  archivos que el motor realmente escribió, y que el dashboard no tiene
  forma de enviar ninguna acción (revisión de código, no solo chequeo
  visual).
