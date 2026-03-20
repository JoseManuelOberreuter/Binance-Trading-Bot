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


def get_bool(key: str, default: bool = False) -> bool:
    v = os.getenv(key, "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


# Trading
SYMBOL = get_str("SYMBOL", "ETHUSDT")
BASE_ASSET = SYMBOL.replace("USDT", "")
QUOTE_ASSET = "USDT"

# Grid (dynamic: based on current price ± spread_pct; see GRID_ADAPTIVE)
GRID_SPREAD_PCT = get_float("GRID_SPREAD_PCT", 15)
GRID_LEVELS = get_int("GRID_LEVELS", 20)
CAPITAL_USDT = get_float("CAPITAL_USDT", 500)

# Adaptive grid: ATR-based half-range %, regime scaling, geometric levels
GRID_ADAPTIVE = get_bool("GRID_ADAPTIVE", True)
GRID_SPREAD_MIN_PCT = get_float("GRID_SPREAD_MIN_PCT", 5)
GRID_SPREAD_MAX_PCT = get_float("GRID_SPREAD_MAX_PCT", 38)
GRID_ATR_SPREAD_MULT = get_float("GRID_ATR_SPREAD_MULT", 5.0)
GRID_ADX_SIDEWAYS_MAX = get_float("GRID_ADX_SIDEWAYS_MAX", 28)
GRID_ADX_STRONG_TREND = get_float("GRID_ADX_STRONG_TREND", 32)
GRID_SIDEWAYS_SPREAD_SCALE = get_float("GRID_SIDEWAYS_SPREAD_SCALE", 1.06)
GRID_STRONG_TREND_SPREAD_SCALE = get_float("GRID_STRONG_TREND_SPREAD_SCALE", 0.82)
GRID_GEOMETRIC = get_bool("GRID_GEOMETRIC", True)
# Max share of portfolio in base asset (0–100) before skipping new BUY ladder placement
MAX_BASE_INVENTORY_PCT = get_float("MAX_BASE_INVENTORY_PCT", 88)
# Recenter grid when price drifts this far from anchor (percent); not every tick
RELOCATE_THRESHOLD_PCT = get_float("RELOCATE_THRESHOLD_PCT", 8)
RELOCATE_COOLDOWN_SEC = get_float("RELOCATE_COOLDOWN_SEC", 300)

# Legacy fixed bounds (used only if GRID_SPREAD_PCT=0)
GRID_UPPER = get_float("GRID_UPPER", 3500)
GRID_LOWER = get_float("GRID_LOWER", 2500)


def get_grid_bounds(
    current_price: float,
    spread_pct: float | None = None,
) -> tuple[float, float]:
    """
    Return (lower, upper) for grid around current_price.
    If GRID_SPREAD_PCT > 0: symmetric band ± spread_pct% (half-range = spread_pct).
    spread_pct overrides GRID_SPREAD_PCT when provided (e.g. adaptive engine).
    """
    if GRID_SPREAD_PCT > 0 and current_price > 0:
        sp = spread_pct if spread_pct is not None else GRID_SPREAD_PCT
        sp = sp / 100.0
        return (current_price * (1 - sp), current_price * (1 + sp))
    return (GRID_LOWER, GRID_UPPER)

# Risk
STOP_LOSS_PCT = get_float("STOP_LOSS_PCT", 0.05)
MAX_ORDER_SIZE = get_float("MAX_ORDER_SIZE", 50)

# ADX: pause grid only when trend is BEARISH and ADX > threshold (operate in bullish trends)
ADX_PAUSE_THRESHOLD = get_float("ADX_PAUSE_THRESHOLD", 35)

# Binance spot fee (0.1% default, 0.075% with BNB)
FEE_RATE = get_float("FEE_RATE", 0.001)

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
