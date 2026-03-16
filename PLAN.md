# Binance Grid Trading Bot — Plan de Desarrollo

## Objetivo
Construir un bot de Grid Trading para el par **ETH/USDT** en Binance, con retorno objetivo de **10-30% mensual**, pasando por una etapa de testnet antes de operar con capital real.

---

## Stack Tecnológico

| Herramienta | Uso |
|---|---|
| `python-binance` | Cliente oficial Binance API |
| `pandas` / `numpy` | Procesamiento de datos y cálculo de indicadores |
| `pandas-ta` | Indicadores técnicos (Bollinger Bands, ADX, RSI, MACD) |
| `python-dotenv` | Manejo de variables de entorno |
| `schedule` | Ejecución periódica del loop del bot |
| `rich` | Dashboard visual en consola |
| `sqlite3` | Registro local de trades y P&L |

---

## Estructura del Proyecto

```
binance-trading/
│
├── .env                    ← API keys (testnet + producción)
├── .gitignore              ← excluye .env del repositorio
├── requirements.txt        ← dependencias Python
├── PLAN.md                 ← este archivo
├── config.py               ← parámetros del grid y del bot
│
├── exchange/
│   ├── __init__.py
│   └── client.py           ← wrapper Binance API (testnet / producción)
│
├── strategy/
│   ├── __init__.py
│   └── grid.py             ← lógica del grid trading
│
├── risk/
│   ├── __init__.py
│   └── manager.py          ← stop loss global, position sizing
│
├── bot.py                  ← loop principal del bot
└── dashboard.py            ← visualización en consola (opcional)
```

---

## Variables de Entorno (.env)

```bash
# Credenciales Testnet (obtener en https://testnet.binance.vision)
TESTNET_API_KEY=
TESTNET_SECRET=

# Credenciales Producción (obtener en https://www.binance.com/es/my/settings/api-management)
BINANCE_API_KEY=
BINANCE_SECRET=

# Entorno activo: "testnet" o "production"
ENVIRONMENT=testnet

# Par a operar
SYMBOL=ETHUSDT

# Configuración del Grid
GRID_UPPER=3500         # precio máximo del rango
GRID_LOWER=2500         # precio mínimo del rango
GRID_LEVELS=20          # número de niveles del grid
CAPITAL_USDT=500        # capital total a asignar al grid

# Risk Management
STOP_LOSS_PCT=0.05      # apagar bot si pérdida total supera 5%
MAX_ORDER_SIZE=50       # máximo USDT por orden individual
```

> **Seguridad:** Los permisos de la API Key en Binance deben ser solo "Spot & Margin Trading". **Nunca habilitar retiros.** Activar restricción por IP.

---

## Cómo Obtener API Keys del Testnet

1. Ir a [testnet.binance.vision](https://testnet.binance.vision/)
2. Login con cuenta de GitHub
3. Generar API Key y Secret
4. El testnet otorga fondos ficticios (~1 BTC + 10,000 USDT)
5. Agregar las keys al `.env` bajo `TESTNET_API_KEY` y `TESTNET_SECRET`

---

## Estrategia: Grid Trading

### ¿Cómo funciona?

El bot divide un rango de precios en N niveles equidistantes y coloca órdenes alternas de compra y venta en cada nivel:

```
Precio ETH: $3,000
Rango: $2,500 — $3,500
Niveles: 20
Espaciado por nivel: ~$52.6 (1.75%)

Nivel 1:  Comprar en $2,500  → Vender en $2,552
Nivel 2:  Comprar en $2,552  → Vender en $2,607
...
Nivel 20: Comprar en $3,448  → Vender en $3,500

Cada oscilación captura ~1.75% de ganancia por nivel.
```

### Condiciones para activar el grid

| Indicador | Condición | Significado |
|---|---|---|
| ADX (14) | < 25 | Mercado lateral → activar grid |
| ADX (14) | > 35 | Tendencia fuerte → pausar grid |
| Bollinger Bands | Precio dentro de las bandas | Confirma lateralización |

---

## Etapas de Desarrollo

### Etapa 1 — Testnet (sin dinero real)

#### Fase 1.1 — Conexión y lectura de datos
- [ ] Instalar dependencias (`pip install -r requirements.txt`)
- [ ] Conectar a Binance Testnet con `client.py`
- [ ] Obtener y mostrar balance de la cuenta
- [ ] Obtener precio actual de ETH/USDT
- [ ] Obtener datos históricos (candlesticks 1h, últimas 200 velas)
- [ ] Calcular y mostrar: Bollinger Bands, ADX, RSI
- [ ] **No se ejecuta ninguna orden** — solo lectura

#### Fase 1.2 — Paper Trading (simulación en memoria)
- [ ] Calcular niveles del grid según precio actual
- [ ] Simular qué órdenes se habrían ejecutado en los últimos datos históricos
- [ ] Calcular ganancia/pérdida simulada
- [ ] Guardar registro en archivo `logs/simulation.json`
- [ ] Correr simulación por 48-72 horas y analizar resultados

#### Fase 1.3 — Órdenes reales en Testnet
- [ ] Colocar órdenes limit reales en el Testnet de Binance
- [ ] Loop cada 60 segundos: revisar estado de órdenes
- [ ] Si orden de compra ejecutada → colocar orden de venta +1.75%
- [ ] Si orden de venta ejecutada → colocar orden de compra -1.75%
- [ ] Verificar stop loss global cada ciclo
- [ ] Guardar historial de trades en `logs/trades.db` (SQLite)
- [ ] Correr 72 horas continuas sin intervención manual

---

### Etapa 2 — Producción (dinero real)

- [ ] Validar resultados positivos en Testnet (mínimo 1 semana)
- [ ] Cambiar `ENVIRONMENT=production` en `.env`
- [ ] Empezar con capital mínimo: **$100-200 USDT**
- [ ] Monitoreo manual las primeras 48 horas
- [ ] Escalar capital gradualmente según resultados

> **Regla:** No escalar capital si el bot tuvo drawdown > 10% en testnet.

---

## Flujo del Bot (Loop Principal)

```
Cada 60 segundos:
│
├── 1. Obtener precio actual ETH/USDT
├── 2. Calcular ADX y Bollinger Bands
│       ├── ADX < 25 → ✅ Grid activo
│       └── ADX > 35 → ⏸️  Grid pausado (mercado tendencial)
│
├── 3. Revisar órdenes ejecutadas
│       ├── Compra ejecutada → colocar venta en precio + 1.75%
│       └── Venta ejecutada  → colocar compra en precio - 1.75%
│
├── 4. Verificar stop loss global
│       └── Pérdida acumulada > 5% → cancelar todo y detener bot
│
└── 5. Mostrar estado en consola + guardar log
```

---

## Risk Management

| Parámetro | Valor | Descripción |
|---|---|---|
| Stop loss por trade | 2% | Máxima pérdida por operación individual |
| Stop loss global | 5% | Si se alcanza, el bot se apaga automáticamente |
| Capital por orden | máx. 10% del total | Diversificación dentro del grid |
| Ratio riesgo/recompensa | mínimo 1:2 | Configurar TP al doble del SL |
| Máximo capital en grid | 50% del portfolio | Nunca poner todo en un solo bot |

---

## Despliegue

### Etapa Testnet y primeras semanas de producción
Correr el bot **localmente en tu PC**:
```bash
python bot.py
```

### Producción estable (cuando el bot esté validado)
Migrar a un **VPS Linux** con proceso permanente:

| Proveedor | Costo | Specs recomendados |
|---|---|---|
| Hetzner CX11 | ~$4/mes | 1 vCPU, 2GB RAM |
| DigitalOcean Droplet | ~$6/mes | 1 vCPU, 1GB RAM |
| AWS EC2 t3.micro | Gratis 1 año | 2 vCPU, 1GB RAM |

```bash
# En el VPS, ejecutar el bot como proceso persistente:
nohup python bot.py > logs/bot.log 2>&1 &

# O con systemd para que se reinicie automáticamente si falla
```

> **¿Por qué no Vercel/Netlify?** Son plataformas serverless con límite de tiempo de ejecución (~30 segundos). Un bot de trading necesita un proceso continuo corriendo 24/7.

---

## Roadmap

```
Semana 1
├── Día 1-2  → Setup del proyecto + Fase 1.1 (conexión y lectura de datos)
├── Día 3-4  → Fase 1.2 (paper trading / simulación)
└── Día 5-7  → Observar resultados, ajustar parámetros del grid

Semana 2
├── Día 1-3  → Fase 1.3 (órdenes reales en Testnet)
└── Día 4-7  → Correr 72h continuas, medir P&L real

Semana 3
├── Si resultados positivos  → Producción con $100-200
└── Si resultados negativos  → Ajustar parámetros y repetir semana 2
```

---

## Métricas a Monitorear

| Métrica | Objetivo |
|---|---|
| Win rate | > 55% |
| Ratio riesgo/recompensa | > 1.5 |
| Drawdown máximo | < 10% |
| Trades por día | 5-15 (según volatilidad) |
| Retorno mensual objetivo | 10-30% |

---

## Referencias

- [Binance Testnet](https://testnet.binance.vision/) — Entorno de pruebas oficial
- [Binance API Docs](https://binance-docs.github.io/apidocs/spot/en/) — Documentación oficial
- [python-binance Docs](https://python-binance.readthedocs.io/) — Librería Python
- [Grid Trading Strategy Guide 2026](https://www.livevolatile.com/blog/grid-trading-crypto-volatility-strategy-2026) — Guía de estrategia
