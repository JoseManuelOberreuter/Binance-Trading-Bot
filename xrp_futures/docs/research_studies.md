# Catálogo detallado de estudios — XRPUSDT Perpetual

Un registro por estudio: objetivo, metodología exacta, script, resultado y
conclusión. `project_status.md` es el resumen narrativo; este archivo es el
detalle para cuando haga falta reconstruir *cómo* se llegó a cada número.

Convención: donde el resultado numérico exacto se preservó en la conversación
que generó este archivo, se transcribe tal cual. Donde solo la metodología y
la conclusión cualitativa se conservaron (estudios anteriores a un resumen de
contexto), se marca explícitamente — para esos, los números exactos requieren
volver a correr el script.

---

## Congelamiento de configuración (Donchian / Chandelier / timeframe)

**Objetivo**: elegir timeframe, ventana de Donchian, y ventana/multiplicador
del Chandelier como prueba de robustez (¿el resultado se sostiene en un rango
de valores razonables?), no como búsqueda del mejor número.

**Metodología**: sweep de Chandelier window en {5,10,15,20,30} y luego en un
rango más corto {2,3,4,5,6,7}, manteniendo Donchian 40/60 y multiplicador 4x
ATR fijos; comparación de métricas walk-forward OOS entre combinaciones.

**Resultado**: números exactos no preservados en el contexto actual (estudio
previo a un resumen de conversación). Conclusión cualitativa conservada: el
resultado se sostuvo de forma razonablemente estable dentro de esos rangos,
lo que llevó al congelamiento de Donchian 40 / Chandelier ventana=5,
multiplicador=4x / timeframe 12h.

**Para reproducir**: no hay script dedicado conservado con ese nombre exacto;
el patrón (`evaluate_fixed_config_walk_forward` variando `chandelier_window`)
está en `backtest/walk_forward.py` y se puede rearmar siguiendo el mismo
patrón que `scripts/run_risk_scaling_reality_check.py` pero variando
`chandelier_window` en vez de `risk_pct`.

---

## Validación final del config congelado (Monte Carlo + stress test)

**Objetivo**: validar la configuración ya congelada (sin más cambios de
parámetros) con Monte Carlo sobre retornos OOS agrupados y stress testing de
costos/funding.

**Metodología**: `backtest/monte_carlo.py` — resampling de `trade_returns_pct`
agrupados de las 4 ventanas walk-forward (cada ventana calculada contra su
propio equity local antes de agrupar); percentiles p5/p50/p95 sobre
`final_multiple` y `max_drawdown` (convención: p95 = escenario severo, p5 =
escenario leve — ver el bug de severidad corregido abajo). `backtest/
stress_test.py` — escenarios de costo (OPTIMISTIC/BASE/PESSIMISTIC) y
funding shock, individuales y combinados.

**Bug encontrado y corregido durante este estudio**: `MonteCarloResult.
percentiles[p]["max_drawdown"]` se calculaba con `np.percentile` directo
sobre el array de drawdowns (valores negativos), lo que ponía el percentil
95 en la cola LEVE y el 5 en la SEVERA — al revés de la convención estándar
de VaR. Corregido tomando el percentil sobre `abs(max_drawdowns)` y
re-signando. Tests de regresión agregados en `tests/test_monte_carlo.py`.

**Resultado**: números exactos (percentiles concretos, resultado combinado
del stress test) no preservados en el contexto actual. Conclusión cualitativa
conservada: la config sobrevive los escenarios de estrés individuales, pero
se degrada en el escenario combinado más extremo (costos pesimistas +
funding shock simultáneos) — suficiente para el usuario considerarlo
validado, no para asumir que el peor caso histórico es el piso real.

**Para reproducir**: `python -m xrp_futures.backtest.monte_carlo` /
`stress_test.py` no son scripts ejecutables directos — hay que armar un
script en `scripts/` que llame `run_monte_carlo_from_returns()` y las
funciones de `stress_test.py` sobre el pool de trades OOS de
`evaluate_fixed_config_walk_forward`, igual que hacen los scripts de Stage 3+.

---

## Estudio de position sizing (risk_pct 0.25%-2.00%)

**Objetivo**: estudiar el efecto de escalar el tamaño de posición (no la
señal) sobre CAGR/Sharpe/drawdown.

**Metodología**: mismo config congelado, variando solo `BacktestConfig.
risk_pct` en {0.25%, 0.50%, 0.75%, 1.00%, 1.50%, 2.00%}.

**Resultado exacto**: no preservado. Hallazgo cualitativo (real, verificado
más tarde con el estudio ampliado de la sección siguiente): **el Sharpe es
prácticamente constante a través de risk_pct** — porque el escalamiento de
notional es lineal, así que CAGR y drawdown se mueven juntos en la misma
proporción. También se estableció que el riesgo de liquidación depende del
apalancamiento y la distancia del stop, no de risk_pct directamente.

**Estudio ampliado (2026-09-22, dentro de esta sesión)** — ver
`scripts/run_risk_scaling_reality_check.py`, rango extendido a 1%-10% para
responder concretamente "qué haría falta para 50% CAGR":

| risk_pct | 2023 | 2024 | 2025 | 2026 (parcial) | Promedio | Peor MDD |
|---|---|---|---|---|---|---|
| 1% | -3.5% | +25.3% | -0.9% | +5.7% | 6.7% | -8.5% |
| 2% | -7.7% | +45.4% | -2.1% | +11.1% | 11.7% | -16.3% |
| 4% | -17.8% | +72.3% | -5.8% | +20.5% | 17.3% | -30.3% |
| 7% | -34.4% | +86.6% | -13.3% | +30.8% | 17.4% | -47.6% |
| 10% | -50.4% | +79.9% | -22.6% | +35.9% | 10.7% | -61.4% |

**Conclusión**: el CAGR promedio tiene un techo real (~17%, entre 4-7% de
riesgo) y luego *cae* al seguir subiendo el riesgo, por el "volatility drag"
del compounding sobre años malos (2023, 2025). Ningún nivel de risk_pct
acerca el sistema a 50% CAGR — no es una cuestión de tolerancia al riesgo,
es un límite matemático del propio mecanismo de compounding.

---

## Etapa 1 — medición de captura en movimientos grandes históricos

**Objetivo**: cuantificar qué porcentaje de cada movimiento grande (≥30%)
de XRP en el período OOS realmente captura la estrategia.

**Metodología**: `backtest/swing_capture.py::identify_major_swings()` (zigzag,
`min_move_pct=0.30`) sobre XRP 12h desde 2023-01-01; `detailed_swing_report()`
cruza cada swing con las operaciones OOS agrupadas (`evaluate_fixed_config_
walk_forward`), calculando `capture_of_move_pct` (dirección-ajustado) y
`time_in_move_pct` por swing. Script: `scripts/run_stage1_capture_analysis.py`.

**Resultado exacto**: no preservado (tabla completa de 20 swings pre-fix, ver
nota de bug abajo). Conclusión cualitativa: captura muy variable, entre 7% y
60% del movimiento según el swing — sin patrón obvio a simple vista, lo que
motivó las Etapas 2-3.

**Bug encontrado en este estudio** (corregido después, durante Stage 2c):
`identify_major_swings()` emitía un primer swing espurio (una oscilación de
apenas -1.1%) porque el criterio de confirmación medía la magnitud de la
REVERSIÓN desde el extremo, no la magnitud del propio tramo pivote→extremo —
explotable solo en el primer swing de la serie, cuyo pivote es un punto
arbitrario de inicio en vez de un extremo ya confirmado. Redujo los swings
válidos de 20 a 19 en el período OOS. Test de regresión agregado en
`tests/test_swing_capture.py`.

---

## Etapa 2 — Trend Strength (score 0-4)

**Objetivo**: construir un score simple y no optimizado de "fuerza de
tendencia" para ver si predice cuándo una salida fue prematura.

**Metodología**: `strategies/trend_strength.py::compute_trend_strength()` —
4 condiciones binarias (ADX>25, |pendiente EMA50|>2% en 20 barras, ruptura
Donchian(20) hace ≤10 barras, momentum 30 días >10%), sumadas a un score
0-4. Retrospectivo sobre los 3 swings nombrados (2023-01-06 +145%,
2024-08-05 +533%, 2025-04-07 +101%) vía `scripts/
run_stage2_trend_strength_diagnostic.py`: Trend Strength en el momento de
cada salida, flag "prematura" si quedaba >15% del movimiento y TS≥3.

**Resultado**: evidencia **mixta/no concluyente** — parcialmente soportada en
el caso 2023, contradicha en 2024/2025. Números exactos por operación no
preservados.

**Conclusión**: Trend Strength por sí solo no explica de forma confiable las
salidas prematuras — no se usó como filtro en ningún mecanismo posterior
(de hecho se descartó explícitamente como base para la Opción 1 del
mecanismo de permanencia de la Etapa 4, en favor de la Opción 2, justamente
por este resultado débil).

---

## Etapa 2b — diagnóstico de la fase inicial/ruidosa

**Objetivo**: entender por qué la fase ruidosa al inicio de un movimiento
grande duró 9 operaciones en 2023 pero solo 3 en 2024 y 2025.

**Metodología**: `scripts/run_stage2b_early_phase_diagnostic.py` — compara,
para los 3 swings nombrados, las primeras 15 barras / 10 operaciones en ADX,
pendiente EMA, precio-vs-EMA, ruptura Donchian, ATR%, ratio de expansión de
volatilidad, momentum, MAE, y avance de precio 5 barras después de cada stop.

**Resultado**: tabla comparativa completa no preservada en el contexto
actual. Sirvió como base exploratoria para la hipótesis de volatilidad
inicial probada formalmente en la Etapa 2c.

---

## Etapa 2c — validación de la hipótesis de volatilidad inicial

**Objetivo**: probar formalmente, contra los 19 swings (no solo los 3
nombrados), si la volatilidad ya expandida al inicio de un movimiento
predice menos stops/reentradas hasta capturarlo.

**Metodología**: `scripts/run_stage2c_volatility_hypothesis_validation.py` —
ratio de expansión de volatilidad (ATR% actual / mediana de 60 barras) al
inicio de cada swing, agrupado en 3 buckets (baja/normal <1.2x, moderada
1.2-1.6x, muy expandida >1.6x); correlaciones de Pearson entre ese ratio y
(stops antes de captura, días hasta captura, % del movimiento finalmente
capturado).

**Resultado exacto**: valores de correlación (`r`) y promedios por bucket no
preservados en el contexto actual. Conclusión cualitativa explícitamente
conservada, incluyendo el criterio de rigor pedido por el usuario ("no
quiero una conclusión optimista por defecto"): correlaciones **débiles y no
monotónicas** — la hipótesis **fue rechazada**, no se sostiene con n=19.

**Nota**: este estudio fue el que expuso el bug de `identify_major_swings()`
descrito en la sección de la Etapa 1 (un outlier de -662% en `TotCap%` sobre
un swing cuyo "Move" mostrado era de apenas -1%), corregido antes de seguir.

---

## Etapa 3 — clasificación estructural de fallas (A-G + OK)

**Objetivo**: en vez de seguir buscando correlaciones aisladas, clasificar
objetivamente CADA uno de los 19 swings según por qué la estrategia no lo
capturó bien.

**Metodología**: árbol de decisión determinista en `scripts/
run_stage3_failure_classification.py::classify()`, evaluado en este orden
por swing: (1) sin operaciones → A (nunca confirmó) o E (movimiento ≤20
días, demasiado rápido para Donchian(40)); (2) ≥50% del movimiento ya
ocurrido al entrar → D; (3) captura total ≥40% → OK; (4) ≥3 stops → C
(ruido/whipsaw); (5) MFE máximo ≥30% y captura <20% → B (salida prematura);
(6) señal opuesta apareció entre dos operaciones propias → F (multi-fase);
(7) si nada de lo anterior → G.

**Resultado exacto** (19 swings, período OOS 2023-2026):

```
2023-01-06 LONG  +145% C   7%  13  108%  -3%  -> 13 stops/reentradas por ruido (captura final 7%)
2023-07-19 SHORT -43%  OK  49%  2   39%  -2%  -> capturó 49% con 2 operaciones — sin fallo relevante
2023-09-11 LONG  +52%  G   29%  1   31%  -6%  -> ninguna causa domina (captura 29%, 1 trade, 1 stop, MFE máx 31%)
2023-11-06 SHORT -31%  C  -48%  3   11%  -2%  -> 3 stops/reentradas (captura final -48%)
2024-02-01 LONG  +47%  C  -2%   3   26%  -3%  -> 3 stops/reentradas (captura final -2%)
2024-03-11 SHORT -42%  C   28%  6   25%  -5%  -> 6 stops/reentradas (captura final 28%)
2024-07-05 LONG  +56%  G   19%  1   29%  -0%  -> ninguna causa domina (captura 19%, 1 trade, 1 stop, MFE máx 29%)
2024-07-31 SHORT -30%  A    0%  0    0%   0%  -> señal correcta apareció pero nunca se abrió posición (caso raro) [ver Stage 3b]
2024-08-05 LONG +533%  OK  60%  4  396%  -4%  -> capturó 60% con 4 operaciones — sin fallo relevante
2024-12-03 SHORT -31%  E    0%  0    0%   0%  -> Donchian(40) nunca confirmó; movimiento de solo 17d
2024-12-20 LONG  +69%  G    8%  1   21%  -0%  -> ninguna causa domina (captura 8%, 1 trade, 0 stops, MFE máx 21%)
2025-01-20 SHORT -39%  C  -20%  3   29% -10%  -> 3 stops/reentradas (captura final -20%)
2025-02-28 LONG  +45%  A    0%  0    0%   0%  -> señal correcta apareció pero nunca se abrió posición (caso raro) [ver Stage 3b]
2025-03-02 SHORT -40%  G   -8%  1   20%  -8%  -> ninguna causa domina (captura -8%, 1 trade, 1 stop, MFE máx 20%)
2025-04-07 LONG +101%  C   28%  4   54%  -1%  -> 4 stops/reentradas (captura final 28%) [ver Stage 4a]
2025-07-22 SHORT -49%  C   31%  5   28%  -2%  -> 4 stops/reentradas (captura final 31%)
2025-12-18 LONG  +32%  D    7%  1   15%  -1%  -> 51% del movimiento ya había ocurrido antes de la entrada correcta
2026-01-06 SHORT -58%  OK  40%  7   39%  -6%  -> capturó 40% con 7 operaciones — sin fallo relevante
2026-08-16 LONG  +53%  OK  57%  1   54%  -2%  -> capturó 57% con 1 operación — sin fallo relevante
```

| Categoría | N | % | Pérdida potencial (pp) |
|---|---|---|---|
| A) Nunca entró en dirección correcta | 2 | 11% | 0 |
| C) Ruido — múltiples stops/reentradas | 7 | 37% | 262 |
| D) Entró demasiado tarde | 1 | 5% | 9 |
| E) Demasiado rápido para Donchian(40) | 1 | 5% | 0 |
| G) Otro motivo, no concluyente | 4 | 21% | 53 |
| OK) Capturó razonablemente | 4 | 21% | 337 |

(B y F: 0 swings cada una.)

**Conclusión inicial**: C domina claramente. Ver Etapa 3b para la revisión
de la categoría A.

---

## Etapa 3b — investigación de los 2 swings "A"

**Objetivo**: entender por qué esos 2 swings, donde la señal correcta
"apareció" según el clasificador, nunca tuvieron una operación real.

**Metodología**: `scripts/run_stage3b_category_a_boundary_check.py` — se
descartó primero la hipótesis de que fuera un artefacto de corte del
walk-forward, corriendo un backtest continuo sobre todo el historial (sin
slicing por ventana) y comparando contra el resultado agrupado.

**Resultado exacto**:
- **2024-07-31 SHORT -30% → 2024-08-05**: la estrategia estaba LONG desde el
  2024-07-13 (+111 neto), se detuvo por stop el 2024-08-02, reabrió LONG de
  inmediato y esa reentrada perdió -103 en 2 días, deteniéndose exactamente
  el 2024-08-05 (fin del swing por definición del pivote zigzag).
- **2025-02-28 LONG +45% → 2025-03-02**: la estrategia estaba SHORT desde el
  2025-02-14 (+108 neto), se detuvo por stop exactamente el 2025-03-02 (fin
  del swing).

**Conclusión**: en ambos casos la estrategia estuvo del lado **equivocado**
durante todo el movimiento y su stop coincidió casi exactamente con el
extremo que define el fin del swing — mecánicamente no había ventana
temporal para una entrada correcta *dentro* de esos límites. No es "Donchian
nunca confirmó"; es la misma familia de falla que C (mal ubicado en una
reversión). Revisa el bucket "genuinamente incapturable por Donchian(40)" a
solo 1/19 (5%, categoría E).

---

## Etapa 3c — cronometraje de los stops dentro de los swings "C"

**Objetivo**: saber si los stops de las 7 swings "C" ocurren rápido después
de entrar (ruido inmediato) o tardan (estructura de pierna real).

**Metodología**: `scripts/run_stage3c_whipsaw_timing.py` — para cada una de
las 36 operaciones detenidas por stop dentro de los 7 swings "C", barras
transcurridas desde la entrada hasta el stop.

**Resultado exacto**: media 21 barras (~10.5 días), mediana 19 barras
(~9.5 días); solo 19% de los stops ocurre dentro de 3 barras (1.5 días),
25% dentro de 5 barras (2.5 días). La primera operación de cada swing tarda
prácticamente lo mismo en detenerse (media 23.1 barras) que las siguientes
(20.5 barras).

**Conclusión**: **refuta** la hipótesis de "el trailing corta ruido
inmediato tras entrar". El patrón real es de piernas genuinas (MFEs de
12%-39%+ antes del stop) cerradas en un retroceso real tras ~1.5-2 semanas.

---

## Etapa 4a — origen de las brechas largas entre operaciones dentro de "C"

**Objetivo**: confirmar si las brechas de varios días entre operaciones de
la misma dirección dentro de un swing "C" se deben a una reversión real de
la señal (fakeout) o a otra causa.

**Metodología**: `scripts/run_stage4a_signal_gap_check.py` — para cada brecha
>1.5 barras entre operaciones consecutivas de la misma dirección dentro de
un swing "C", chequea si la señal persistida pasó por la dirección opuesta
durante la brecha, y si hubo una operación real del lado opuesto abierta en
ese lapso.

**Resultado exacto**: 3 brechas largas encontradas (todas dentro de los
swings 2023 +145% y 2025 +101%), **las 3 explicadas al 100%** por una
reversión real de Donchian(40):
- 2023, brecha 2023-02-09→2023-03-22 (40d): 3 operaciones SHORT, netos
  -112.3, +45.9, -65.7 (neto -132).
- 2023, brecha 2023-04-21→2023-05-29 (38d): 3 operaciones SHORT más, -49.9,
  -52.6, -53.2 (neto -156).
- 2025, brecha 2025-05-30→2025-07-09 (40d): 6 operaciones SHORT, -106.1,
  -20.3, +48.0, -37.8, -136.8, -142.4 (neto ≈ -395), mientras XRP seguía en
  tendencia alcista real.

**Conclusión**: 0 brechas sin explicar. No es demora en reconfirmar — es que
el sistema toma operaciones contrarias reales (y mayormente perdedoras)
durante tendencias fuertes, antes de volver a la dirección correcta. Esta
evidencia definió el diseño de la Etapa 4b (Opción 2, elegida sobre la
Opción 1 basada en Trend Strength — descartada por el resultado débil de
la Etapa 2).

---

## Etapa 4b — mecanismo de confirmación de reversión (Opción 2)

**Objetivo**: probar si exigir que una ruptura de Donchian(40) en dirección
opuesta persista `confirm_bars` barras consecutivas (en vez de aceptarla con
una sola barra) reduce el ruido sin romper el resto del sistema.

**Metodología**: nuevo módulo `strategies/reversal_confirmation.py` —
`confirmed_donchian_signal()`, que envuelve el mismo `donchian_channel()`
sin tocarlo; `confirm_bars=1` reproduce exactamente el baseline (verificado
por test e invariante de diseño). Tests en `tests/
test_reversal_confirmation.py` (3, todos pasando). Comparación vía `scripts/
run_stage4b_confirmation_test.py` contra `confirm_bars` ∈ {1 (control), 2, 3}.

**Resultado exacto**:

| Variante | Trades | Sharpe | CAGR | Peor MDD | Swings operados | Captura prom. |
|---|---|---|---|---|---|---|
| baseline (frozen) | 125 | 0.31 | 7.0% | -8.5% | 16 | 18% |
| confirm_bars=1 (control) | 125 | 0.31 | 7.0% | -8.5% | 16 | 18% |
| confirm_bars=2 | 127 | 0.12 | 8.9% | -12.0% | 11 | 29% |
| confirm_bars=3 | 122 | 0.20 | 7.2% | -16.1% | 10 | 28% |

Distribución de categorías (Stage 3) por variante:

| Variante | A | B | C | D | E | F | G | OK |
|---|---|---|---|---|---|---|---|---|
| baseline / confirm_bars=1 | 2 | 0 | 7 | 1 | 1 | 0 | 4 | 4 |
| confirm_bars=2 | 6 | 0 | 2 | 0 | 2 | 0 | 5 | 4 |
| confirm_bars=3 | 7 | 0 | 2 | 1 | 2 | 0 | 3 | 4 |

**Conclusión**: control (`confirm_bars=1`) reproduce el baseline exacto
(125/125 trades, mismas categorías) — el wiring es correcto. Pero el
mecanismo **no es una mejora limpia**: reduce el whipsaw (C: 7→2) a costa de
casi triplicar las entradas perdidas (A: 2→6-7) y empeorar Sharpe/drawdown.
**Mecanismo archivado** — no integrado a nada en producción ni al baseline.

---

## Prueba cruzada de activo (ETHUSDT)

**Objetivo**: ¿la config congelada generaliza a otro activo, o es un
artefacto específico de XRP?

**Metodología**: `scripts/run_eth_cross_asset_test.py` — exactamente el
mismo config (Donchian 40, Chandelier 5x4, 12h, riesgo 1%), sin ningún
cambio de parámetro, corrido sobre ETHUSDT en paralelo a XRPUSDT.

**Resultado exacto**:

| | XRPUSDT | ETHUSDT |
|---|---|---|
| Datos | 2020-01-06 → 2026-09-20 (4890 barras) | 2020-01-01 → 2026-09-20 (4910 barras) |
| 2023 | -3.5% | -3.5% |
| 2024 | +25.3% | +9.2% |
| 2025 | -0.9% | +3.9% |
| 2026 (parcial) | +7.1% | +18.0% |
| CAGR promedio | 7.0% | 6.9% |
| Sharpe promedio | 0.31 | 0.66 |
| Peor MaxDD | -8.5% | -13.5% |
| Swings ≥30% con ≥1 trade correcto | 16/19 (84%) | 9/9 (100%) |
| Captura promedio cuando operó | 18% | 37% |

**Nota metodológica**: el rango de datos se pidió igual (desde 2020-01-01)
para ambos símbolos — no se probó si ETH tiene historial utilizable más
atrás (su listado en Binance Futures es anterior al de XRP). Punto abierto,
no resuelto.

**Conclusión**: CAGR muy similar sin retunear nada es evidencia (con poca
muestra: 4 ventanas no independientes, 9-19 swings) de que el mecanismo no
es un fluke de XRP. El peor drawdown de ETH (-13.5%) es peor que el de XRP
(-8.5%) — no hay un ganador claro, pero tampoco evidencia de que sea
XRP-específico.
