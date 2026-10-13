# 07 — Investigación de mercado y competencia

> Investigación de octubre 2026 con fuentes públicas (reseñas, informes de mercado, prensa).
> Muchas fuentes son reseñas con afiliados o informes comerciales con cifras dispares: se usan
> como **orden de magnitud**, no como datos exactos.

## Conclusión corta

- **El mercado de bots existe y es grande a nivel mundial, pero está saturado** y tiene
  competidores **gratuitos muy fuertes** (los bots propios de Binance y Pionex).
- **En Chile el mercado es pequeño:** se estiman ~500 mil usuarios de cripto y solo una
  fracción mínima usaría bots de Futures pagados. **Como negocio solo-Chile, probablemente
  no pasa de un proyecto paralelo** (ver estimación abajo).
- **No encontramos competidores latinoamericanos en español** con suscripción: puede ser una
  oportunidad (nicho sin atender) o una señal de poca demanda. Hay que validarlo.
- **La confianza requiere historial real:** el estándar práctico es **≥ 1 año de resultados
  en vivo verificados**, idealmente pasando por mercados alcistas y bajistas (2–3 años).
- **El mayor riesgo reputacional es la seguridad** (caso 3Commas) y el negocio es **cíclico**:
  cuando el mercado cripto cae, los usuarios se van (caso Shrimpy).

## 1. Competencia

### Plataformas internacionales (el usuario conecta su exchange con API key)

| Plataforma | Precio aproximado (USD/mes) | Enfoque |
|---|---|---|
| **3Commas** | ~20 / 50 / 140 (anual: 15 / 38 / 105) | DCA, grid, copy; la más conocida |
| **Cryptohopper** | ~29 / 69 / 129 | Estrategias por indicadores, marketplace, paper trading |
| **Bitsgap** | ~29 / 69 / 149 | Grid y multi-exchange |
| **Coinrule** | Gratis limitado · ~20–80 · plan alto ~975–995 | Reglas "si pasa X, haz Y" sin programar |
| **WunderTrading** | Básico gratis · Pro de pago | Bots y copy trading con Binance |
| **TradeSanta** | Planes de pago con demo | Bots simples |

### Competidores gratuitos (la amenaza principal)

- **Binance (bots nativos):** Spot Grid, DCA, Rebalancing, Auto-Invest y TWAP **sin costo
  extra** sobre la comisión normal. Están dentro de la misma app donde ya está el usuario.
- **Pionex:** un exchange con más de 16 bots gratuitos integrados; cobra solo la comisión de
  trading (0,05%).
- **Copy trading** de los exchanges: el usuario copia a otro trader sin pagar suscripción.

### Latinoamérica y Chile

- **No encontramos** startups latinoamericanas de bots con suscripción para Binance. El
  mercado hispano lo atienden plataformas internacionales con traducción.
- Exchanges locales en Chile (Buda, CryptoMKT, Orionx) ofrecen acceso fácil, pero **no bots
  de gestión de riesgo** como el nuestro.

### Dónde podríamos diferenciarnos

1. **Español nativo, soporte local y pagos en pesos** (Flow / Mercado Pago).
2. **Enfoque en riesgo, no en ganancia:** stops en Binance, riesgo por operación, kill-switch,
   capital asignado. La mayoría de la competencia vende "ganancias".
3. **Educación integrada** para quien entra por primera vez a Futures.
4. **Simplicidad:** pocas opciones bien explicadas, frente a plataformas con cientos de
   parámetros.

## 2. Tamaño del mercado

- **Bots de trading cripto (mundial, 2025):** las estimaciones van de ~USD 240 millones a
  ~USD 1.800 millones según cómo se defina el mercado (hay informes con cifras mayores pero
  poco consistentes). Los usuarios minoristas son la mayor parte.
- **Latinoamérica:** la adopción cripto creció **63%** en el año a junio 2025 (segunda región
  del mundo). En el informe 2026, **Brasil** es el **n.º 1 mundial** en adopción; la actividad
  regional fue de ~USD 594 mil millones con crecimiento de 9,8%. Más del 90% de la actividad
  regional es en stablecoins (ahorro frente a la inflación, no trading).
- **Chile:** no está entre los 20 primeros del índice de adopción. Las estimaciones de usuarios
  varían mucho: ~**500 mil personas** (2,5% de la población, Blockchain Academy) o 13% de los
  encuestados (Sherlock, 2024).

### Estimación gruesa para Chile (supuestos nuestros)

| Paso | Supuesto | Personas |
|---|---|---|
| Usuarios cripto en Chile | ~500.000 | 500.000 |
| Operan activamente y les interesa automatizar | ~2% | ~10.000 |
| Tienen capital suficiente (≥ ~1 millón CLP) para que la suscripción tenga sentido | ~30% | ~3.000 |
| Pagan una suscripción (conversión realista de un SaaS nuevo) | 3–5% | **~90–150** |

Con ~100 usuarios a ~7.500 CLP/mes → **~750.000 CLP/mes (~USD 800)**, antes de costos
(~USD 50–100/mes de infraestructura, pasarela de pago, legal). Es un **ingreso
complementario**, no una empresa. Para algo mayor hace falta **Latinoamérica hispana**
(México, Argentina, Colombia, Perú: decenas de millones de usuarios) y eventualmente
**Brasil** (portugués).

## 3. ¿Los usuarios de bots ganan dinero?

- **No encontramos estudios independientes** que midan si los usuarios de bots ganan. Las
  cifras disponibles vienen de las mismas plataformas o de blogs (por ejemplo, "mediana ~12%
  anual" o "60–70% pierde o gana poco"), sin datos verificables.
- Hay un **sesgo de supervivencia** fuerte: quien pierde apaga el bot en silencio y no aparece
  en las estadísticas.
- **Los costos pesan:** con capitales chicos, la suscripción + comisiones + funding pueden
  comerse la mayor parte de la ganancia. Ejemplo citado: 20% bruto → ~8,5% neto con USD 10.000.
- Implicancia: **recomendar un capital mínimo** y no prometer resultados.

## 4. Lecciones de la industria

- **3Commas (dic. 2022):** filtración de API keys de usuarios. Pérdidas reportadas entre
  ~USD 14,8 y 22 millones; la empresa negó responsabilidad al principio y luego la admitió;
  hubo investigación del FBI. → **La seguridad de las keys es existencial** (ver
  [05 — Seguridad](05_seguridad.md): keys sin retiro + IP restringida + cifrado).
- **Shrimpy (2023):** cerró su producto para consumidores por la **caída de la demanda
  minorista** en el mercado bajista y luego fue adquirida. → **Los ingresos siguen el ciclo
  cripto**: hay que tener costos bajos para sobrevivir los años malos.
- **Binance en Chile:** según fuentes locales, Binance **no está inscrita en la CMF** y opera
  como proveedor extranjero; la CMF advirtió esto pero no bloquea el acceso. → Riesgo de
  depender de un solo exchange con situación regulatoria incierta en el país. Considerar
  soportar más de un exchange a futuro.

## 5. ¿Cuánto historial hace falta para generar confianza?

- **Práctica común:** al menos **1 año de resultados en vivo** como mínimo; los sistemas
  automáticos suelen tener malas rachas cada 6–12 meses, así que menos de un año dice poco.
- **Inversión profesional (fondos):** suele exigir **5 años** o más, cubriendo distintos
  mercados.
- **Lo que no cuenta como historial:** backtests, capturas de pantalla, diarios de trading.
- **Lo que sí cuenta:** resultados medidos por un tercero sin interés en maquillarlos (una
  plataforma regulada, un auditor). Auditar formalmente es caro (~USD 50.000/año por
  estrategia según una fuente), inviable al inicio.

### Cómo construir historial siendo pequeños

1. **Operar el bot con dinero propio desde ya** y guardar todo el registro (ya lo hace el bot:
   `trades.jsonl`, `equity_log.jsonl`).
2. **Perfil público verificado en Binance** (portafolio de copy trading): Binance publica los
   resultados en vivo, así que funcionan como **historial verificado por un tercero** sin costo.
   *Ojo legal:* no usar esos resultados como publicidad del SaaS (ver
   [04 — Legal](04_legal.md)); sirven como prueba de funcionamiento.
3. Apuntar a **12 meses en vivo** antes de cobrar, e idealmente pasar por un período bajista.

## 6. Recomendación

| Escenario | Viabilidad | Comentario |
|---|---|---|
| SaaS solo para Chile | **Baja como empresa, posible como proyecto paralelo** | Mercado chico, competencia gratuita |
| SaaS para Latinoamérica hispana | **Media** | Nicho en español sin competidores locales visibles; hay que validarlo |
| Mundial | **Baja** al inicio | Mercado saturado con empresas consolidadas |

**Siguiente paso recomendado (bajo costo, alto aprendizaje):**
1. Seguir con el bot propio en real para acumular **historial de 12 meses**.
2. Hacer la **landing con lista de espera para toda Latinoamérica hispana** (fase 0) y medir
   inscritos por país antes de construir.
3. Definir la diferenciación: **riesgo + español + educación + pagos locales**.
4. Solo construir el SaaS si la lista de espera muestra demanda real (ej. ≥ 300 inscritos con
   capital suficiente).

## Fuentes

- [Koinly — Best crypto trading bots (oct. 2026)](https://koinly.io/blog/best-crypto-trading-bots/)
- [Turbinefi — Best crypto trading bots: features, costs & testing (2026)](https://www.turbinefi.com/blog/best-crypto-trading-bots)
- [altFINS — Best crypto trading bots in 2026](https://altfins.com/best/crypto-trading-and-investing/crypto-trading-bots/)
- [WunderTrading — Cryptohopper alternatives](https://wundertrading.com/journal/en/cryptohopper-alternatives)
- [Blockster — Crypto trading bots in 2026 ranked](https://blockster.com/crypto-trading-bots-in-2026-ranked-reviewed-compared-beginners-to-pros)
- [MEXC — Guía de bots de Binance](https://www.mexc.co/news/1106069)
- [Guru99 — Best Binance trading bots (2026)](https://www.guru99.com/best-binance-trading-bot.html)
- [Chainalysis — 2025 Global Crypto Adoption Index](https://www.chainalysis.com/blog/2025-global-crypto-adoption-index/)
- [Chainalysis — 2025 LATAM crypto adoption](https://www.chainalysis.com/blog/latin-america-crypto-adoption-2025/)
- [Chainalysis — 2026 LATAM crypto adoption](https://www.chainalysis.com/blog/latin-america-crypto-adoption-2026/)
- [Diario Financiero — Encuesta usuarios cripto en Chile](https://www.df.cl/mercados/banca-fintech/encuesta-revela-que-solo-un-15-6-de-usuarios-cripto-en-chile-son-mujeres)
- [CriptoNoticias — Encuesta sudamericanos y criptomonedas](https://www.criptonoticias.com/comunidad/adopcion/criptomonedas-ahorrar-dinero-encuesta-sudamerica-bitcoin/)
- [La Tercera — Perfil de quienes invierten en cripto en Chile](https://www.latercera.com/pulso-pm/noticia/criptomonedas-cual-es-el-perfil-de-quienes-invierten-en-ellas-en-chile-y-la-region/MZJH575LTVC2TEJTMKDIEVIR2U/)
- [Intel Market Research — Crypto trading bot market](https://www.intelmarketresearch.com/crypto-trading-bot-market-59769)
- [Dataintelo — AI crypto trading bot market](https://dataintelo.com/report/ai-crypto-trading-bot-market)
- [Verified Market Reports — Crypto bot trading tool market](https://www.verifiedmarketreports.com/product/crypto-bot-trading-tool-market/)
- [Cointelegraph — 3Commas CEO confirma filtración de API keys](https://cointelegraph.com/news/3commas-ceo-confirms-api-key-leak-following-warning-from-cz)
- [Blockworks — 3Commas API leak](https://blockworks.com/news/crypto-trading-bot-platform-3commas-rocked-by-critical-api-leak)
- [HAPI Labs — Análisis de la filtración de 3Commas](https://medium.com/hapi-labs/api-leaked-3commas-api-keys-leak-detailed-analysis-b8b4076ecf11)
- [Brave New Coin — Shrimpy cierra](https://bravenewcoin.com/insights/crypto-trading-bots-in-trouble)
- [Cryptohopper — Shrimpy deja el producto de consumo](https://www.cryptohopper.com/blog/shrimpy-stops-consumer-offering-introducing-cryptohopper-as-the-perfect-alternative-9465)
- [Altrady — Are AI crypto trading bots profitable in 2026](https://www.altrady.com/blog/crypto-bots/are-ai-crypto-trading-bots-profitable-2026)
- [BotVerdict — Are crypto trading bots profitable in 2026](https://botverdict.com/articles/are-crypto-trading-bots-profitable-in-2026-an-honest-analysis-of-real-returns/)
- [Kieran Duff — Raising capital (historial mínimo)](https://kieranduff.substack.com/p/raising-capital)
- [Private.ch — Track record and due diligence](https://private.ch/media/docs/private/2014/02/de/24_Track_Record_and.pdf)
- [TradingView — Métricas de copy trading](https://id.tradingview.com/chart/EURUSD/B3nsqgLv-Important-Copy-Trading-Metrics-to-Analyze)
- [Rankia — Análisis de Binance en Chile](https://www.rankia.cl/blog/como-operar-invertir-criptomonedas/4945836-analisis-binance-criptomonedas-comisiones-como-abrir-cuenta)
- [CriptoNoticias — CMF: Binance no autorizada para operar en Chile](https://www.criptonoticias.com/regulacion/binance-no-autorizado-operar-chile-dice-cmf)
- [Guía de Trader — Crypto en Chile: panorama 2026](https://www.guiadetrader.com/blog/mejores-exchanges-crypto-chile-2026)
