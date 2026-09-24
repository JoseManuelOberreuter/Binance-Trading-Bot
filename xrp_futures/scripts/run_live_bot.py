"""
Run the live bot (Phase 4, docs/execution_plan.md).

  python -m xrp_futures.scripts.run_live_bot --dry-run --once   # show decisions, send nothing
  python -m xrp_futures.scripts.run_live_bot --once             # one real cycle (testnet)
  python -m xrp_futures.scripts.run_live_bot                    # loop every 5 min (testnet)

Environment comes from FUTURES_ENVIRONMENT (default testnet). Production additionally
requires --allow-production, so real money can't be used by accident.
Signals use production market data (public, keyless) even on testnet (decision 16).
Logs go to the console and live_state/<env>/bot.log.
"""

from __future__ import annotations

import argparse
import logging
import time

from binance.client import Client

from xrp_futures.config.live import DEFAULT
from xrp_futures.execution.client import PRODUCTION, futures_environment, get_futures_client
from xrp_futures.execution import events as ev
from xrp_futures.execution.engine import LiveEngine
from xrp_futures.execution.live_signal import compute_live_signal, fetch_closed_bars
from xrp_futures.execution.order_manager import BinanceFuturesExchange, DryRunExchange
from xrp_futures.execution.state import StateStore
from xrp_futures.execution.symbol_filters import load_filters

log = logging.getLogger("xfb")


def setup_logging(store: StateStore) -> None:
    store.dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    log.setLevel(logging.INFO)
    for handler in (logging.StreamHandler(), logging.FileHandler(store.dir / "bot.log", encoding="utf-8")):
        handler.setFormatter(fmt)
        log.addHandler(handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="decide and log, send no orders, save no state")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    parser.add_argument("--interval", type=int, default=300, help="seconds between cycles (default 300)")
    parser.add_argument("--allow-production", action="store_true", help="required to trade real money")
    args = parser.parse_args()

    environment = futures_environment()
    if environment == PRODUCTION and not args.allow_production:
        raise SystemExit("FUTURES_ENVIRONMENT=production requires --allow-production (real money).")

    config = DEFAULT
    store = StateStore(environment)
    setup_logging(store)
    client = get_futures_client(environment)
    filters = load_filters(client, config.universe, config.use_production_filters)
    exchange = BinanceFuturesExchange(client, filters)
    if args.dry_run:
        exchange = DryRunExchange(exchange)

    market = Client()   # production market data for signals
    engine = LiveEngine(config, exchange,
                        lambda s: compute_live_signal(s, fetch_closed_bars(market, s, config), config),
                        filters, store, dry_run=args.dry_run)

    log.info("start: env=%s dry_run=%s universe=%s risk=%.2f%% leverage=%dx simulated_capital=%s",
             environment, args.dry_run, ",".join(config.universe), config.risk_pct * 100, config.leverage,
             config.equity_override_usdt)
    for symbol in config.universe:
        exchange.setup_symbol(symbol, config.leverage)
    engine.events.emit(ev.BOT_STARTED, environment=environment, simulated_capital=config.equity_override_usdt,
                       universe=list(config.universe), risk_pct=config.risk_pct, interval_s=args.interval)

    while True:
        try:
            state = engine.cycle()
            log.info("cycle ok: positions=%s paused=%s bot_paused=%s kill_switch=%s realized_pnl=%+.4f",
                     list(state.positions) or "none", list(state.paused) or "none", state.bot_paused,
                     state.kill_switch.tripped, state.realized_pnl)
        except Exception as e:   # never let one bad cycle kill the loop (legacy bot.py pattern)
            log.exception("cycle failed; retrying next interval")
            engine.events.emit(ev.CYCLE_ERROR, error=f"{type(e).__name__}: {e}"[:300], retry_s=args.interval)
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
