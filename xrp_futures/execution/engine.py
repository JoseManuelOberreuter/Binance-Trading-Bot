"""
Live engine (Phase 4, docs/execution_plan.md): one `cycle()` per poll (~5 min).

  1. snapshot Binance -> reconcile -> apply actions (Binance is the source of truth)
  2. apply dashboard commands (execution/commands.py)
  3. if a new 12h candle closed: per symbol, the backtest's rules in the backtest's order
     - trail the Chandelier stop (only toward the market) for positions kept
     - exit when the signal no longer matches the position (close, then cancel stop)
     - enter when flat and signal != 0 (also re-entry after a stop-out, like the backtest)
     exits before entries, so freed exposure is available to new entries
  4. equity (simulated $250 + bot PnL), kill-switch, equity/heartbeat logs, save

Safety rules: a position is never left without a stop (new stop placed before the old
is cancelled; if a stop can't be placed after an entry, the position is closed and the
symbol paused); a stop level the market has already crossed is executed at market
(the backtest's gap-through), since Binance rejects stops that would trigger at once.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import pandas as pd

from xrp_futures.config.live import LiveConfig
from xrp_futures.execution import commands as cmdq
from xrp_futures.execution import reconcile as rc
from xrp_futures.execution.live_signal import LiveSignal
from xrp_futures.execution.order_manager import Fill, Snapshot
from xrp_futures.execution.sizing import Skip, plan_entry
from xrp_futures.execution.state import BotState, Position, StateStore
from xrp_futures.execution.symbol_filters import SymbolFilters
from xrp_futures.risk import stops as stp

log = logging.getLogger("xfb")


def last_closed_bar_open(now: pd.Timestamp, timeframe: str = "12h") -> pd.Timestamp:
    """Open time of the most recent fully closed candle (12h candles open 00:00/12:00 UTC)."""
    return now.floor(timeframe) - pd.Timedelta(timeframe)


def _iso(ts) -> str:
    return pd.Timestamp(ts).isoformat()


def _ms(iso: str) -> int:
    return int(pd.Timestamp(iso).value // 10**6)


class LiveEngine:
    def __init__(self, config: LiveConfig, exchange, signal_fn: Callable[[str], LiveSignal],
                 filters: dict[str, SymbolFilters], store: StateStore, dry_run: bool = False):
        self.config = config
        self.exchange = exchange
        self.signal_fn = signal_fn
        self.filters = filters
        self.store = store
        self.commands = cmdq.CommandQueue(store.dir)
        self.dry_run = dry_run

    # ------------------------------------------------------------------ cycle ----
    def cycle(self, now: pd.Timestamp | None = None) -> BotState:
        now = now if now is not None else pd.Timestamp.now(tz="UTC")
        state = self.store.load(self.config)
        snap = self.exchange.snapshot()

        for action in rc.reconcile(state, snap.positions, snap.stops, self.config.universe):
            self._apply_reconcile(state, action)

        self._apply_commands(state, snap)

        target = last_closed_bar_open(now, self.config.strategy.timeframe)
        if state.last_processed_bar != _iso(target):
            signals = {s: self.signal_fn(s) for s in self.config.universe}
            stale = [s for s, sig in signals.items() if sig.bar_open_time != target]
            if stale:
                log.info("candle %s not published yet for %s; retry next cycle", target, stale)
            else:
                self._process_candle(state, signals, snap)
                state.last_processed_bar = _iso(target)

        self._update_equity(state, snap, now)
        self._save(state)
        return state

    # ------------------------------------------------------------- candle rules ----
    def _process_candle(self, state: BotState, signals: dict[str, LiveSignal], snap: Snapshot) -> None:
        log.info("processing candle %s", next(iter(signals.values())).bar_open_time)
        for symbol in self.config.universe:          # 1) exits / trailing
            pos = state.positions.get(symbol)
            if pos is None or symbol in state.paused:
                continue
            sig = signals[symbol]
            if sig.signal != pos.side:
                self._close(state, symbol, reason="signal")
            else:
                self._trail(state, symbol, sig)

        for symbol in self.config.universe:          # 2) entries
            sig = signals[symbol]
            if symbol in state.positions or symbol in state.paused or sig.signal == 0:
                continue
            if state.bot_paused:
                log.info("%s: %s signal, bot paused -> no entry", symbol, "LONG" if sig.signal == 1 else "SHORT")
                continue
            self._enter(state, symbol, sig, snap)

    def _enter(self, state: BotState, symbol: str, sig: LiveSignal, snap: Snapshot) -> None:
        price = self.exchange.mark_price(symbol)
        prices = {s: self.exchange.mark_price(s) for s in state.positions}
        equity = state.sizing_equity(snap.wallet_equity, self._unrealized(state, snap))
        plan = plan_entry(symbol, sig.signal, equity, price, sig.atr, self.filters[symbol],
                          state.open_notional(prices), self.config, state.kill_switch.tripped)
        if isinstance(plan, Skip):
            log.info("%s: entry skipped (%s) %s", symbol, plan.reason, plan.detail)
            return

        fill = self.exchange.market_order(symbol, plan.side, plan.qty, reduce_only=False)
        stop = self.filters[symbol].round_price(
            stp.atr_stop_price(fill.price, sig.atr, self.config.initial_stop_atr_multiplier, plan.side))
        state.positions[symbol] = Position(
            symbol=symbol, side=plan.side, qty=str(fill.qty), entry_price=fill.price,
            entry_time=_iso(pd.Timestamp(fill.time_ms, unit="ms", tz="UTC")), stop_price=str(stop),
            entry_order_id=fill.order_id, entry_commission=fill.commission)
        self._save(state)   # the position exists on Binance now — persist before anything else can fail
        log.info("%s: OPEN %s %s @ %.6g, stop %s (equity %.2f)", symbol,
                 "LONG" if plan.side == 1 else "SHORT", fill.qty, fill.price, stop, equity)
        try:
            state.positions[symbol].stop_client_order_id = self.exchange.place_stop(symbol, plan.side, fill.qty, stop)
        except Exception as e:  # never leave a position unprotected
            self._alert(state, symbol, f"stop placement failed after entry ({e}); closing position")
            self._close(state, symbol, reason="stop_failed")
            state.paused[symbol] = "stop placement failed after entry"
        self._save(state)

    def _trail(self, state: BotState, symbol: str, sig: LiveSignal) -> None:
        pos = state.positions[symbol]
        current = Decimal(pos.stop_price)
        candidate = sig.chandelier_long if pos.side == 1 else sig.chandelier_short
        new = self.filters[symbol].round_price(stp.trailing_stop_update(float(current), candidate, pos.side))
        if (new - current) * pos.side <= 0:
            return                                   # not tighter: keep the current stop
        mark = self.exchange.mark_price(symbol)
        if (mark - float(new)) * pos.side <= 0:     # market already through the new level
            log.info("%s: trailed stop %s already crossed (mark %s) -> exit at market", symbol, new, mark)
            self._close(state, symbol, reason="stop")
            return
        new_id = self.exchange.place_stop(symbol, pos.side, Decimal(pos.qty), new)   # new first...
        if pos.stop_client_order_id:
            self.exchange.cancel_stop(symbol, pos.stop_client_order_id)             # ...then the old
        log.info("%s: stop trailed %s -> %s", symbol, current, new)
        pos.stop_price, pos.stop_client_order_id = str(new), new_id
        self._save(state)

    def _close(self, state: BotState, symbol: str, reason: str) -> None:
        pos = state.positions[symbol]
        fill = self.exchange.market_order(symbol, -pos.side, Decimal(pos.qty), reduce_only=True)
        if pos.stop_client_order_id:     # close first, then cancel: never unprotected
            self.exchange.cancel_stop(symbol, pos.stop_client_order_id)
        self._record_trade(state, pos, fill, reason)

    def _record_trade(self, state: BotState, pos: Position, fill: Fill | None, reason: str) -> None:
        funding = self.exchange.funding_since(pos.symbol, _ms(pos.entry_time)) if fill else 0.0
        net = None
        if fill is not None:
            net = fill.realized_pnl - fill.commission - pos.entry_commission + funding
            state.realized_pnl += net
        record = dict(symbol=pos.symbol, side="LONG" if pos.side == 1 else "SHORT", qty=pos.qty,
                      entry_time=pos.entry_time, entry_price=pos.entry_price,
                      exit_time=_iso(pd.Timestamp(fill.time_ms, unit="ms", tz="UTC")) if fill else None,
                      exit_price=fill.price if fill else None, reason=reason,
                      realized_pnl=fill.realized_pnl if fill else None,
                      commission=(fill.commission + pos.entry_commission) if fill else None,
                      funding=funding, net_pnl=net, dry_run=self.dry_run)
        del state.positions[pos.symbol]
        if not self.dry_run:
            self.store.append_trade(record)
        log.info("%s: CLOSED (%s) net %s", pos.symbol, reason, f"{net:+.4f}" if net is not None else "unknown")
        self._save(state)

    # ------------------------------------------------------------ reconcile -----
    def _apply_reconcile(self, state: BotState, a: rc.Action) -> None:
        pos = state.positions.get(a.symbol)
        if a.kind == rc.PLACE_STOP:
            mark = self.exchange.mark_price(a.symbol)
            if (mark - float(pos.stop_price)) * pos.side <= 0:
                self._alert(state, a.symbol, f"stop {pos.stop_price} missing and already crossed; closing")
                self._close(state, a.symbol, reason="stop")
                return
            pos.stop_client_order_id = self.exchange.place_stop(a.symbol, pos.side, Decimal(pos.qty),
                                                                Decimal(pos.stop_price))
            self._alert(state, a.symbol, "position had no stop on Binance; stop re-placed")
        elif a.kind == rc.RECORD_CLOSED:
            fill = self.exchange.stop_fill(a.symbol, pos.stop_client_order_id) if pos.stop_client_order_id else None
            if fill is None:
                self._alert(state, a.symbol, "position closed on Binance but not by the bot's stop; PnL unknown")
            self._record_trade(state, pos, fill, reason="stop")
        elif a.kind in (rc.CANCEL_ORPHAN_STOP, rc.CANCEL_DUPLICATE_STOP):
            self.exchange.cancel_stop(a.symbol, a.client_order_id)
            log.info("%s: %s %s", a.symbol, a.kind, a.client_order_id)
        elif a.kind == rc.ADOPT_STOP:
            pos.stop_client_order_id = a.client_order_id
        elif a.kind == rc.PAUSE_SYMBOL:
            state.paused[a.symbol] = a.detail
            self._alert(state, a.symbol, f"paused: {a.detail}")
        self._save(state)

    # ------------------------------------------------------------- commands -----
    def _apply_commands(self, state: BotState, snap: Snapshot) -> None:
        if self.dry_run:
            for c in self.commands.pending():
                log.info("[DRY-RUN] pending command %s %s (not applied)", c["kind"], c.get("symbol") or "")
            return
        taken = self.commands.take()
        if not taken:
            return
        results = []
        for c in taken:
            ok, msg = self._apply_command(state, c, snap)
            results.append({**c, "ok": ok, "message": msg, "applied_at": datetime.now(timezone.utc).isoformat()})
            log.info("command %s %s -> %s", c["kind"], c.get("symbol") or "", msg)
        self._save(state)
        self.commands.finish(results)

    def _apply_command(self, state: BotState, c: dict, snap: Snapshot) -> tuple[bool, str]:
        kind, symbol = c["kind"], c.get("symbol")
        if kind == cmdq.PAUSE_BOT:
            state.bot_paused = True
            return True, "bot paused: no new entries; open positions keep their stops"
        if kind == cmdq.RESUME_BOT:
            state.bot_paused = False
            return True, "bot resumed"
        if kind == cmdq.PAUSE_SYMBOL:
            state.paused[symbol] = "manual pause"
            return True, f"{symbol} paused (its current stop stays active on Binance)"
        if kind == cmdq.RESUME_SYMBOL:
            if state.paused.pop(symbol, None) is None:
                return False, f"{symbol} was not paused"
            return True, f"{symbol} resumed"
        if kind == cmdq.CLOSE_POSITION:
            if symbol not in state.positions:
                return False, f"no bot position in {symbol}"
            self._close(state, symbol, reason="manual")
            return True, f"{symbol} closed at market (the bot may re-enter next candle; pause the coin to avoid it)"
        if kind == cmdq.RESET_KILL_SWITCH:
            equity = state.sizing_equity(snap.wallet_equity, self._unrealized(state, snap))
            state.kill_switch.reset(equity)
            return True, f"kill-switch reset; new peak {equity:.2f}"
        return False, f"unknown command {kind}"

    # ---------------------------------------------------------------- equity ----
    def _unrealized(self, state: BotState, snap: Snapshot) -> float:
        return sum(p.unrealized_pnl for s, p in snap.positions.items() if s in state.positions)

    def _update_equity(self, state: BotState, snap: Snapshot, now: pd.Timestamp) -> None:
        equity = state.sizing_equity(snap.wallet_equity, self._unrealized(state, snap))
        was_tripped = state.kill_switch.tripped
        state.kill_switch.update(equity)
        if state.kill_switch.tripped and not was_tripped:
            self._alert(state, None, f"KILL-SWITCH tripped at equity {equity:.2f} "
                                     f"({state.kill_switch.drawdown(equity):.1%} from peak): new entries blocked")
        state.last_cycle_at = _iso(now)
        if not self.dry_run:
            self.store.append_equity(dict(
                time=_iso(now), equity=equity, wallet_equity=snap.wallet_equity, realized_pnl=state.realized_pnl,
                unrealized_pnl=self._unrealized(state, snap), drawdown=state.kill_switch.drawdown(equity),
                kill_switch=state.kill_switch.tripped, bot_paused=state.bot_paused,
                open_positions=len(state.positions)))

    # --------------------------------------------------------------- helpers ----
    def _alert(self, state: BotState, symbol: str | None, message: str) -> None:
        log.warning("ALERT %s%s", f"{symbol}: " if symbol else "", message)
        if not self.dry_run:
            self.store.append_alert(dict(time=datetime.now(timezone.utc).isoformat(), symbol=symbol, message=message))

    def _save(self, state: BotState) -> None:
        if not self.dry_run:
            self.store.save(state)
