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

Fatal-error review (2026-10-11, docs decisions 38-42):
  - an entry is recorded as *pending* before it's sent; if the response is lost (timeout,
    -1007, network) the order is looked up by its client id and, if it executed, adopted
    with its stop — never left as an unprotected "unknown position"
  - entries require ISOLATED margin at the configured leverage (checked on Binance)
  - each symbol fails on its own: one coin's error never blocks the others; a failed
    exit leaves the candle unprocessed so it's retried next cycle (the stop still protects)
  - signed-request timestamps are re-aligned with Binance's clock every cycle
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import pandas as pd

from xrp_futures.config.live import LiveConfig
from xrp_futures.execution import commands as cmdq
from xrp_futures.execution import events as ev
from xrp_futures.execution import reconcile as rc
from xrp_futures.execution.live_signal import LiveSignal
from xrp_futures.execution.order_manager import Fill, Snapshot, new_client_id, outcome_unknown
from xrp_futures.execution.sizing import Skip, plan_entry
from xrp_futures.execution.state import BotState, Position, StateStore
from xrp_futures.execution.symbol_filters import SymbolFilters
from xrp_futures.risk import stops as stp

log = logging.getLogger("xfb")

PENDING_GIVE_UP = pd.Timedelta(minutes=2)   # an order Binance still doesn't know after this never executed


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
        self.events = ev.EventLog(store.dir, enabled=not dry_run)
        self.dry_run = dry_run
        self._candle_actions: dict[str, int] = {}
        self._now = pd.Timestamp.now(tz="UTC")

    # ------------------------------------------------------------------ cycle ----
    def cycle(self, now: pd.Timestamp | None = None) -> BotState:
        now = now if now is not None else pd.Timestamp.now(tz="UTC")
        self._now = now
        state = self.store.load(self.config)
        try:
            self.exchange.sync_clock()
        except Exception as e:     # not fatal by itself; signed calls will say if it matters
            log.warning("clock sync failed: %s", e)
        if state.pending_entries:
            self._resolve_pending(state, now)
        snap = self.exchange.snapshot()

        for action in rc.reconcile(state, snap.positions, snap.stops, self.config.universe):
            if action.symbol in state.pending_entries:
                continue           # outcome of its entry still unknown: don't treat as foreign
            try:
                self._apply_reconcile(state, action)
            except Exception as e:
                self._symbol_error(state, action.symbol, f"reconcile {action.kind}", e)

        self._apply_commands(state, snap)

        target = last_closed_bar_open(now, self.config.strategy.timeframe)
        if state.last_processed_bar != _iso(target):
            signals = {s: self.signal_fn(s) for s in self.config.universe}
            stale = [s for s, sig in signals.items() if sig.bar_open_time != target]
            if stale:
                log.info("candle %s not published yet for %s; retry next cycle", target, stale)
            elif self._process_candle(state, signals, snap):
                state.last_processed_bar = _iso(target)
            else:
                log.warning("an exit failed on candle %s; retrying next cycle (stops still active)", target)

        self._update_equity(state, snap, now)
        self._save(state)
        return state

    # ------------------------------------------------------------- candle rules ----
    def _process_candle(self, state: BotState, signals: dict[str, LiveSignal], snap: Snapshot) -> bool:
        """Apply the candle's rules. False if an exit failed (the candle must be retried)."""
        bar_time = next(iter(signals.values())).bar_open_time
        log.info("processing candle %s", bar_time)
        self._candle_actions = {"opened": 0, "closed": 0, "stops_moved": 0, "skipped": 0}
        exits_ok = self._rules(state, signals, snap)
        state.last_signals = {s: sig.signal for s, sig in signals.items()}
        self.events.emit(ev.CANDLE_PROCESSED, bar_open_time=_iso(bar_time),
                         bar_close_time=_iso(bar_time + pd.Timedelta(self.config.strategy.timeframe)),
                         signals={s: sig.signal for s, sig in signals.items()},
                         closes={s: sig.close for s, sig in signals.items()},
                         actions=self._candle_actions, open_positions=len(state.positions))
        return exits_ok

    def _count(self, action: str) -> None:
        if action in self._candle_actions:
            self._candle_actions[action] += 1

    def _rules(self, state: BotState, signals: dict[str, LiveSignal], snap: Snapshot) -> bool:
        exits_ok = True
        for symbol in self.config.universe:          # 1) exits / trailing
            pos = state.positions.get(symbol)
            if pos is None or symbol in state.paused:
                continue
            sig = signals[symbol]
            try:
                if sig.signal != pos.side:
                    self._close(state, symbol, reason="signal")
                else:
                    self._trail(state, symbol, sig)
            except Exception as e:                   # one coin's failure never blocks the others
                self._symbol_error(state, symbol, "exit/trail", e)
                exits_ok = exits_ok and sig.signal == pos.side   # a failed trail is retried next candle

        for symbol in self.config.universe:          # 2) entries
            sig = signals[symbol]
            if (symbol in state.positions or symbol in state.paused or symbol in state.pending_entries
                    or sig.signal == 0):
                continue
            if state.bot_paused:
                log.info("%s: %s signal, bot paused -> no entry", symbol, "LONG" if sig.signal == 1 else "SHORT")
                self._count("skipped")
                self.events.emit(ev.ENTRY_SKIPPED, symbol=symbol, side=sig.signal, reason="bot_paused")
                continue
            try:
                self._enter(state, symbol, sig, snap)
            except Exception as e:
                self._symbol_error(state, symbol, "entry", e)
        return exits_ok

    def _enter(self, state: BotState, symbol: str, sig: LiveSignal, snap: Snapshot) -> None:
        price = self.exchange.mark_price(symbol)
        prices = {s: self.exchange.mark_price(s) for s in state.positions}
        equity = state.sizing_equity(snap.wallet_equity, self._unrealized(state, snap))
        plan = plan_entry(symbol, sig.signal, equity, price, sig.atr, self.filters[symbol],
                          state.open_notional(prices), self.config, state.kill_switch.tripped)
        if isinstance(plan, Skip):
            log.info("%s: entry skipped (%s) %s", symbol, plan.reason, plan.detail)
            self._count("skipped")
            self.events.emit(ev.ENTRY_SKIPPED, symbol=symbol, side=sig.signal, reason=plan.reason,
                             target_usdt=plan.target_usdt, limit_usdt=plan.limit_usdt)
            return

        problem = self.exchange.ensure_margin(symbol, self.config.leverage)
        if problem:
            log.warning("%s: entry skipped, margin mode is %s", symbol, problem)
            self._count("skipped")
            self.events.emit(ev.ENTRY_SKIPPED, symbol=symbol, side=sig.signal, reason="margin_mode")
            self._alert(state, symbol, f"margin is {problem}; no entries until it's fixed", code="MARGIN_MODE")
            return

        cid = new_client_id("e", symbol)
        state.pending_entries[symbol] = dict(client_id=cid, side=plan.side, atr=sig.atr, equity=equity,
                                             created=_iso(self._now))
        self._save(state)      # persisted BEFORE sending, so a crash right after can be recovered
        try:
            fill = self.exchange.market_order(symbol, plan.side, plan.qty, reduce_only=False, client_id=cid)
        except Exception as e:
            if not outcome_unknown(e):           # definite rejection (e.g. insufficient margin)
                del state.pending_entries[symbol]
                self._save(state)
                raise
            log.warning("%s: entry response lost (%s); checking whether it executed", symbol, e)
            try:
                fill = self.exchange.entry_status(symbol, cid)
            except Exception:
                fill = None
            if fill is None:                     # not visible yet: decide next cycle
                self._alert(state, symbol, f"entry order outcome unknown ({e}); checking again next cycle",
                            code="ENTRY_UNKNOWN")
                return
        del state.pending_entries[symbol]
        self._open_position(state, symbol, plan.side, fill, sig.atr, equity)

    def _resolve_pending(self, state: BotState, now: pd.Timestamp) -> bool:
        """Settle entries whose response was lost. True if any position was adopted."""
        adopted = False
        for symbol, p in list(state.pending_entries.items()):
            try:
                fill = self.exchange.entry_status(symbol, p["client_id"])
            except Exception as e:
                log.warning("%s: pending entry %s still unknown: %s", symbol, p["client_id"], e)
                continue
            if fill is None:
                if now - pd.Timestamp(p["created"]) < PENDING_GIVE_UP:
                    continue                     # Binance may not show it yet
                del state.pending_entries[symbol]
                log.info("%s: pending entry %s never executed", symbol, p["client_id"])
                self._save(state)
                continue
            del state.pending_entries[symbol]
            self._alert(state, symbol, "entry executed despite the lost response; adopted with its stop",
                        code="ENTRY_RECOVERED")
            try:
                self._open_position(state, symbol, p["side"], fill, p["atr"], p["equity"])
                adopted = True
            except Exception as e:
                self._symbol_error(state, symbol, "adopt pending entry", e)
        return adopted

    def _open_position(self, state: BotState, symbol: str, plan_side: int, fill: Fill, atr: float,
                       equity: float) -> None:
        """Record a filled entry and protect it with its initial stop (closes it if that fails)."""
        stop = self.filters[symbol].round_price(
            stp.atr_stop_price(fill.price, atr, self.config.initial_stop_atr_multiplier, plan_side))
        state.positions[symbol] = Position(
            symbol=symbol, side=plan_side, qty=str(fill.qty), entry_price=fill.price,
            entry_time=_iso(pd.Timestamp(fill.time_ms, unit="ms", tz="UTC")), stop_price=str(stop),
            entry_order_id=fill.order_id, entry_commission=fill.commission)
        self._save(state)   # the position exists on Binance now — persist before anything else can fail
        log.info("%s: OPEN %s %s @ %.6g, stop %s (equity %.2f)", symbol,
                 "LONG" if plan_side == 1 else "SHORT", fill.qty, fill.price, stop, equity)
        self._count("opened")
        self.events.emit(ev.POSITION_OPENED, symbol=symbol, side=plan_side, qty=str(fill.qty), price=fill.price,
                         notional=float(fill.qty) * fill.price, stop=float(stop),
                         stop_pct=abs(fill.price - float(stop)) / fill.price,
                         risk_usdt=float(fill.qty) * abs(fill.price - float(stop)), equity=equity)
        try:
            state.positions[symbol].stop_client_order_id = self.exchange.place_stop(symbol, plan_side, fill.qty, stop)
        except Exception as e:  # never leave a position unprotected
            self._alert(state, symbol, f"stop placement failed after entry ({e}); closing position",
                        code="STOP_FAILED")
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
        self._count("stops_moved")
        self.events.emit(ev.STOP_MOVED, symbol=symbol, side=pos.side, old_stop=float(current), new_stop=float(new),
                         locked_pnl=pos.side * (float(new) - pos.entry_price) * float(Decimal(pos.qty)))
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
        self._count("closed")
        notional = float(Decimal(pos.qty)) * pos.entry_price
        hours = ((pd.Timestamp(record["exit_time"]) - pd.Timestamp(pos.entry_time)).total_seconds() / 3600
                 if record["exit_time"] else None)
        self.events.emit(ev.POSITION_CLOSED, symbol=pos.symbol, side=pos.side, reason=reason,
                         entry_price=pos.entry_price, exit_price=record["exit_price"], net_pnl=net,
                         pnl_pct=(net / notional) if net is not None and notional else None, hours=hours)
        log.info("%s: CLOSED (%s) net %s", pos.symbol, reason, f"{net:+.4f}" if net is not None else "unknown")
        self._save(state)

    # ------------------------------------------------------------ reconcile -----
    def _apply_reconcile(self, state: BotState, a: rc.Action) -> None:
        pos = state.positions.get(a.symbol)
        if a.kind == rc.PLACE_STOP:
            mark = self.exchange.mark_price(a.symbol)
            if (mark - float(pos.stop_price)) * pos.side <= 0:
                self._alert(state, a.symbol, f"stop {pos.stop_price} missing and already crossed; closing",
                            code="STOP_CROSSED")
                self._close(state, a.symbol, reason="stop")
                return
            pos.stop_client_order_id = self.exchange.place_stop(a.symbol, pos.side, Decimal(pos.qty),
                                                                Decimal(pos.stop_price))
            self._alert(state, a.symbol, "position had no stop on Binance; stop re-placed", code="STOP_REPLACED")
        elif a.kind == rc.RECORD_CLOSED:
            fill = self.exchange.stop_fill(a.symbol, pos.stop_client_order_id) if pos.stop_client_order_id else None
            if fill is None:
                self._alert(state, a.symbol, "position closed on Binance but not by the bot's stop; PnL unknown",
                            code="CLOSED_EXTERNALLY")
            self._record_trade(state, pos, fill, reason="stop")
        elif a.kind in (rc.CANCEL_ORPHAN_STOP, rc.CANCEL_DUPLICATE_STOP):
            self.exchange.cancel_stop(a.symbol, a.client_order_id)
            log.info("%s: %s %s", a.symbol, a.kind, a.client_order_id)
        elif a.kind == rc.ADOPT_STOP:
            pos.stop_client_order_id = a.client_order_id
        elif a.kind == rc.PAUSE_SYMBOL:
            state.paused[a.symbol] = a.detail
            self._alert(state, a.symbol, f"paused: {a.detail}",
                        code="UNKNOWN_POSITION" if "not opened by the bot" in a.detail else "QTY_MISMATCH")
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
            self.events.emit(ev.COMMAND_APPLIED, command=c["kind"], symbol=c.get("symbol"), ok=ok,
                             value=c.get("value"))
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
        if kind == cmdq.SET_CAPITAL:
            value = c.get("value")
            if value is not None and not float(value) > 0:
                return False, f"invalid capital {value!r}"
            amount = float(value) if value is not None else None
            equity = state.set_capital(amount, snap.wallet_equity, self._unrealized(state, snap))
            if amount is None:
                return True, f"capital: whole futures wallet ({equity:.2f})"
            note = f" (wallet only has {snap.wallet_equity:.2f}: using that)" if amount > snap.wallet_equity else ""
            return True, f"capital set to {amount:.2f}{note}"
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
                                     f"({state.kill_switch.drawdown(equity):.1%} from peak): new entries blocked",
                        emit=False)
            self.events.emit(ev.KILL_SWITCH, equity=equity, drawdown=state.kill_switch.drawdown(equity))
        state.last_cycle_at = _iso(now)
        self._maybe_status(state, snap, now, equity)
        if not self.dry_run:
            self.store.append_equity(dict(
                time=_iso(now), equity=equity, wallet_equity=snap.wallet_equity, realized_pnl=state.realized_pnl,
                unrealized_pnl=self._unrealized(state, snap), drawdown=state.kill_switch.drawdown(equity),
                kill_switch=state.kill_switch.tripped, bot_paused=state.bot_paused,
                open_positions=len(state.positions), positions=self._position_marks(state, snap)))

    def _maybe_status(self, state: BotState, snap: Snapshot, now: pd.Timestamp, equity: float) -> None:
        """Periodic console summary so the user can see the bot is alive between candles."""
        every = pd.Timedelta(minutes=self.config.status_every_min)
        if state.last_status_at and now - pd.Timestamp(state.last_status_at) < every:
            return
        sigs = list(state.last_signals.values())
        self.events.emit(ev.STATUS, equity=equity, realized_pnl=state.realized_pnl,
                         unrealized_pnl=self._unrealized(state, snap), open_positions=len(state.positions),
                         market_up=sigs.count(1), market_down=sigs.count(-1), market_total=len(sigs),
                         bot_paused=state.bot_paused, kill_switch=state.kill_switch.tripped)
        state.last_status_at = _iso(now)

    @staticmethod
    def _position_marks(state: BotState, snap: Snapshot) -> dict[str, dict]:
        """Per bot position: current price and unrealized PnL as Binance reports them (for the dashboard).
        The price is derived from Binance's own unrealized PnL: entry + PnL / signed qty."""
        marks = {}
        for symbol, pos in state.positions.items():
            remote = snap.positions.get(symbol)
            if remote is None or remote.amt == 0:
                continue
            marks[symbol] = dict(price=remote.entry_price + remote.unrealized_pnl / float(remote.amt),
                                 unrealized=remote.unrealized_pnl)
        return marks

    # --------------------------------------------------------------- helpers ----
    def _symbol_error(self, state: BotState, symbol: str | None, what: str, e: Exception) -> None:
        log.exception("%s: %s failed", symbol, what)
        self._alert(state, symbol, f"{what} failed: {type(e).__name__}: {e}"[:300], code="SYMBOL_ERROR")

    def _alert(self, state: BotState, symbol: str | None, message: str, emit: bool = True,
               code: str = "GENERIC") -> None:
        log.warning("ALERT %s%s", f"{symbol}: " if symbol else "", message)
        if not self.dry_run:
            self.store.append_alert(dict(time=datetime.now(timezone.utc).isoformat(), symbol=symbol, message=message))
        if emit:
            self.events.emit(ev.ALERT, symbol=symbol, code=code, message=message)

    def _save(self, state: BotState) -> None:
        if not self.dry_run:
            self.store.save(state)
