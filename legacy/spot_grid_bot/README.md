# Binance Grid Trading Bot (legacy — spot)

> **Legacy.** This is the original spot grid-trading bot, kept as-is and no longer under active development. Active development has moved to `xrp_futures/` at the repo root (Binance USDⓈ-M Futures, trend/momentum/volatility-targeting on XRPUSDT). This bot still runs unchanged from `legacy/spot_grid_bot/`.

Grid trading bot for ETH/USDT on Binance. Uses a dynamic grid that adapts to the current price (± percentage).

---

## Setup

Run all commands below from this directory (`legacy/spot_grid_bot/`):

```bash
cd legacy/spot_grid_bot
pip install -r ../../requirements.txt
```

Uses the `.env` file at the repo root for your Binance API keys and configuration (see Configuration section below) — `python-dotenv` finds it automatically by walking up from this directory.

---

## Basic Commands

### 1. Account stats
View balances, market data, technical indicators (RSI, ADX, Bollinger Bands), and recent trades.

```bash
python stats.py
```

---

### 2. Simulation (paper trading)
Run a backtest over historical data before using real capital.

```bash
# Single run, 30 days, 1h candles (default)
python simulate.py

# 60 days
python simulate.py --days 60

# Use 15-minute candles (more granular)
python simulate.py --interval 15m

# Compare multiple grid configurations
python simulate.py --compare

# Combine options
python simulate.py --compare --days 30 --interval 1h
```

**Simulation options**
| Option | Description | Default |
|--------|--------------|---------|
| `--days`, `-d` | Days to simulate | 30 |
| `--interval`, `-i` | Candle interval (`1h` or `15m`) | 1h |
| `--compare`, `-c` | Compare several grid configs | — |

Results are saved to `logs/simulation.json` (or `logs/simulation_compare.json`).

---

### 3. Run the bot
Start the grid trading bot. It runs in a loop every 60 seconds.

```bash
python bot.py
```

Press **Ctrl+C** to stop.

**Note:** Set `ENVIRONMENT=testnet` in `.env` to use Binance Testnet (fake funds). Use `ENVIRONMENT=production` for real trading.

---

## Configuration (.env)

| Variable | Description |
|----------|-------------|
| `ENVIRONMENT` | `testnet` or `production` |
| `SYMBOL` | Trading pair (default: ETHUSDT) |
| `GRID_SPREAD_PCT` | Grid range as ±% of current price (default: 15) |
| `GRID_LEVELS` | Number of grid levels |
| `CAPITAL_USDT` | Capital allocated to the grid |
| `STOP_LOSS_PCT` | Global stop loss (e.g. 0.1 = 10%) |
| `MAX_ORDER_SIZE` | Max USDT per order |
| `FEE_RATE` | Binance fee (0.001 = 0.1%, 0.00075 = 0.075% with BNB) |
| `ADX_PAUSE_THRESHOLD` | Pause grid only when bearish + ADX > this (default: 35) |

---

## Project structure

```
legacy/spot_grid_bot/
├── bot.py           # Main trading bot
├── stats.py         # Account statistics & indicators
├── simulate.py      # Backtest simulation
├── config.py        # Loads parameters from .env
├── exchange/        # Binance API client
├── strategy/        # Grid logic
├── risk/            # Stop loss
└── logs/            # simulation.json, trades.db
```
