"""Load grid and bot parameters from environment variables."""

import os
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()


def get_str(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def get_float(key: str, default: float = 0.0) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


# Trading
SYMBOL = get_str("SYMBOL", "ETHUSDT")
BASE_ASSET = SYMBOL.replace("USDT", "")
QUOTE_ASSET = "USDT"

# Grid (dynamic: based on current price ± GRID_SPREAD_PCT)
GRID_SPREAD_PCT = get_float("GRID_SPREAD_PCT", 15)
GRID_LEVELS = get_int("GRID_LEVELS", 20)
CAPITAL_USDT = get_float("CAPITAL_USDT", 500)

# Legacy fixed bounds (used only if GRID_SPREAD_PCT=0)
GRID_UPPER = get_float("GRID_UPPER", 3500)
GRID_LOWER = get_float("GRID_LOWER", 2500)


def get_grid_bounds(current_price: float) -> tuple[float, float]:
    """Return (lower, upper) for grid. Uses GRID_SPREAD_PCT if > 0, else fixed bounds."""
    if GRID_SPREAD_PCT > 0 and current_price > 0:
        spread = GRID_SPREAD_PCT / 100
        return (current_price * (1 - spread), current_price * (1 + spread))
    return (GRID_LOWER, GRID_UPPER)

# Risk
STOP_LOSS_PCT = get_float("STOP_LOSS_PCT", 0.05)
MAX_ORDER_SIZE = get_float("MAX_ORDER_SIZE", 50)

# Optional reserve (% of ETH to never sell, 0 = use all in grid)
RESERVE_ETH_PCT = get_float("RESERVE_ETH_PCT", 0)


def grid_step(lower: float | None = None, upper: float | None = None) -> float:
    """Price step between grid levels."""
    if GRID_LEVELS < 1:
        return 0.0
    low = lower if lower is not None else GRID_LOWER
    up = upper if upper is not None else GRID_UPPER
    return (up - low) / GRID_LEVELS


def capital_per_level() -> float:
    """USDT per grid level."""
    if GRID_LEVELS < 1:
        return 0.0
    return min(CAPITAL_USDT / GRID_LEVELS, MAX_ORDER_SIZE)
