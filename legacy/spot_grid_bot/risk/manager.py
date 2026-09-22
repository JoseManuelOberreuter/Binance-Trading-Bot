"""Risk management: stop loss, position sizing."""

from config import CAPITAL_USDT, STOP_LOSS_PCT


def check_stop_loss(
    initial_value_usdt: float,
    current_value_usdt: float,
    stop_pct: float | None = None,
) -> bool:
    """
    Check if global stop loss is triggered.
    Returns True if we should STOP (loss exceeded threshold).
    """
    threshold = stop_pct if stop_pct is not None else STOP_LOSS_PCT
    if initial_value_usdt <= 0 or threshold <= 0:
        return False

    loss_pct = (initial_value_usdt - current_value_usdt) / initial_value_usdt
    return loss_pct >= threshold
