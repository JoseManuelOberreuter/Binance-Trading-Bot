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
  fee 0.05%, equity mark-to-market: $1.000 (ene-2020) → $2.305 (sep-2026),
  ~13% anual compuesto, peor caída -21%. Por año: 2020 +23%, 2021 +50%,
  2022 -4%, 2023 +15%, 2024 -2%, 2025 +3%, 2026 (parcial) +12%.
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
| 3 | `sizing`, `kill_switch`, `state`, `reconcile` + tests (lógica pura) | Tests pasando |
| 4 | `order_manager` + `engine` + `run_live_bot` en **testnet** | Un ciclo de 12h supervisado: entradas, stops y reemplazos correctos |
| 5 | Dashboard Streamlit | Muestra equity, posiciones y operaciones desde los archivos reales |
| 6 | Testnet desatendido, **1-2 meses**, en PC siempre encendido o VPS | Sin errores de estado; operaciones coinciden con un backtest de la misma ventana |
| 7 | **Real con capital chico**, 3-6 meses (monto a decidir: ~$2.000 para operar las 8; con menos se omiten monedas — ver decisión 15) | Slippage/comisiones reales cercanos al modelo; resultado dentro de lo esperable |
| 8 | Decisión de escalar capital | Decisión manual, con los datos de la Fase 7 |

**Regla de detención (fijada antes de empezar):** caída de 30% desde el
máximo → kill-switch y revisión manual; no se "ajustan parámetros" en
caliente para recuperar pérdidas.

## Verificación

- `python -m pytest xrp_futures/tests/` — sin llamadas de red en la suite.
- `python -m xrp_futures.scripts.check_futures_connectivity` contra testnet.
- Testnet: comparar `trades.jsonl` contra un backtest de la misma ventana.
- Revisión de código: el dashboard no puede enviar ninguna acción.
