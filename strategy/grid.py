"""Grid trading logic: level computation and simulation."""

from dataclasses import dataclass, field

from config import (
    CAPITAL_USDT,
    FEE_RATE,
    GRID_ADAPTIVE,
    GRID_ADX_SIDEWAYS_MAX,
    GRID_ADX_STRONG_TREND,
    GRID_ATR_SPREAD_MULT,
    GRID_GEOMETRIC,
    GRID_LEVELS,
    GRID_LOWER,
    GRID_SIDEWAYS_SPREAD_SCALE,
    GRID_SPREAD_MAX_PCT,
    GRID_SPREAD_MIN_PCT,
    GRID_SPREAD_PCT,
    GRID_STRONG_TREND_SPREAD_SCALE,
    GRID_UPPER,
    MAX_ORDER_SIZE,
    get_grid_bounds,
)


@dataclass
class GridLevel:
    """Single grid level: buy price and corresponding sell price."""

    index: int
    buy_price: float
    sell_price: float


@dataclass
class SimulatedTrade:
    """One completed buy->sell cycle."""

    buy_price: float
    sell_price: float
    qty: float
    profit_usdt: float
    commission_usdt: float
    timestamp_ms: int


@dataclass
class SimulationResult:
    """Result of a grid simulation run."""

    total_trades: int
    winning_trades: int
    pnl_usdt: float
    pnl_pct: float
    total_commission_usdt: float = 0.0
    trades: list[SimulatedTrade] = field(default_factory=list)
    initial_capital: float = 0.0
    final_capital: float = 0.0


def compute_adaptive_spread_pct(atr_pct: float, adx: float, is_bearish: bool) -> float:
    """
    Half-range (±) spread percent around spot for dynamic grid bounds.
    Couples width to ATR (volatility) and scales slightly for sideways vs strong trend.
    """
    if not GRID_ADAPTIVE:
        return GRID_SPREAD_PCT

    raw = max(
        GRID_SPREAD_MIN_PCT,
        min(GRID_SPREAD_MAX_PCT, atr_pct * GRID_ATR_SPREAD_MULT),
    )
    if adx < GRID_ADX_SIDEWAYS_MAX:
        raw *= GRID_SIDEWAYS_SPREAD_SCALE
    elif adx > GRID_ADX_STRONG_TREND:
        raw *= GRID_STRONG_TREND_SPREAD_SCALE
        if is_bearish:
            raw *= 0.96
    return max(GRID_SPREAD_MIN_PCT, min(GRID_SPREAD_MAX_PCT, raw))


def _levels_linear(low: float, up: float, n: int) -> list[GridLevel]:
    step = (up - low) / n if n > 0 else 0
    if step <= 0:
        return []
    levels = []
    for i in range(n):
        buy_price = low + i * step
        sell_price = buy_price + step
        if sell_price > up:
            sell_price = up
        levels.append(GridLevel(index=i, buy_price=buy_price, sell_price=sell_price))
    return levels


def _levels_geometric(low: float, up: float, n: int) -> list[GridLevel]:
    """Equal multiplicative spacing between adjacent buy prices (constant % step in price)."""
    if n < 1 or low <= 0 or up <= low:
        return []
    ratio = (up / low) ** (1.0 / n)
    levels = []
    for i in range(n):
        buy_price = low * (ratio**i)
        sell_price = min(low * (ratio ** (i + 1)), up)
        levels.append(GridLevel(index=i, buy_price=buy_price, sell_price=sell_price))
    return levels


def compute_grid_levels(
    current_price: float | None = None,
    lower: float | None = None,
    upper: float | None = None,
    n_levels: int | None = None,
    spread_pct: float | None = None,
    *,
    geometric: bool | None = None,
) -> list[GridLevel]:
    """
    Compute all grid levels (buy/sell prices).
    If current_price is given and GRID_SPREAD_PCT > 0, uses dynamic range (price ± spread).
    spread_pct overrides default half-range when set (adaptive engine).
    Explicit lower/upper (e.g. simulation) skips spread_pct.
    geometric: if True, equal % steps between levels; else equal $ width (legacy).
    """
    use_geo = GRID_GEOMETRIC if geometric is None else geometric

    if lower is not None and upper is not None:
        low, up = lower, upper
    elif current_price is not None and current_price > 0:
        low, up = get_grid_bounds(current_price, spread_pct=spread_pct)
    else:
        low, up = GRID_LOWER, GRID_UPPER
    n = n_levels if n_levels is not None else GRID_LEVELS
    if use_geo:
        return _levels_geometric(low, up, n)
    return _levels_linear(low, up, n)


def run_grid_simulation(
    klines: list,
    capital: float | None = None,
    levels: list[GridLevel] | None = None,
    max_order_size: float | None = None,
) -> SimulationResult:
    """
    Simulate grid trading over historical klines.

    Each kline: [open_time, open, high, low, close, volume, ...]
    """
    if levels is None:
        levels = compute_grid_levels()
    if not levels:
        return SimulationResult(0, 0, 0.0, 0.0)

    cap = capital if capital is not None else CAPITAL_USDT
    max_order = max_order_size if max_order_size is not None else MAX_ORDER_SIZE
    usdt_per_order = min(cap / len(levels), max_order) if cap else 0

    # Track open positions: level_index -> {qty, buy_price}
    positions: dict[int, dict] = {}
    completed_trades: list[SimulatedTrade] = []

    for kline in klines:
        open_time = int(kline[0])
        low = float(kline[3])
        high = float(kline[2])

        # First: process sells (close positions)
        to_remove = []
        for level_idx, pos in positions.items():
            sell_price = levels[level_idx].sell_price
            if low <= sell_price <= high:
                qty = pos["qty"]
                buy_price = pos["buy_price"]
                gross_profit = qty * (sell_price - buy_price)
                commission = FEE_RATE * qty * (buy_price + sell_price)
                net_profit = gross_profit - commission
                completed_trades.append(
                    SimulatedTrade(
                        buy_price=buy_price,
                        sell_price=sell_price,
                        qty=qty,
                        profit_usdt=net_profit,
                        commission_usdt=commission,
                        timestamp_ms=open_time,
                    )
                )
                to_remove.append(level_idx)
        for idx in to_remove:
            del positions[idx]

        # Second: process buys (open new positions)
        for level_idx, level in enumerate(levels):
            if level_idx in positions:
                continue
            buy_price = level.buy_price
            if low <= buy_price <= high:
                qty = usdt_per_order / buy_price
                positions[level_idx] = {"qty": qty, "buy_price": buy_price}

    total_pnl = sum(t.profit_usdt for t in completed_trades)
    total_commission = sum(t.commission_usdt for t in completed_trades)
    winning = sum(1 for t in completed_trades if t.profit_usdt > 0)
    pnl_pct = (total_pnl / cap * 100) if cap > 0 else 0

    return SimulationResult(
        total_trades=len(completed_trades),
        winning_trades=winning,
        pnl_usdt=total_pnl,
        pnl_pct=pnl_pct,
        total_commission_usdt=total_commission,
        trades=completed_trades,
        initial_capital=cap,
        final_capital=cap + total_pnl,
    )
