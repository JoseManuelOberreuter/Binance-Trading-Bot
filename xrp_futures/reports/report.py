"""
Comparison report: strategies vs benchmarks (§23/§24), rendered as a Rich console
table and saved as JSON. `build_comparison_rows()` is the pure/testable part;
`print_comparison_table()` and `save_comparison_json()` are thin rendering/IO
wrappers around it.
"""

from __future__ import annotations

import json
from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table

RESULTS_DIR = Path(__file__).parent.parent / "backtest" / "results"

REPORT_METRICS = [
    ("cagr", "CAGR", "pct"),
    ("sharpe", "Sharpe", "num"),
    ("sortino", "Sortino", "num"),
    ("calmar", "Calmar", "num"),
    ("max_drawdown", "MaxDD", "pct"),
    ("win_rate", "Win%", "pct"),
    ("profit_factor", "PF", "num"),
    ("total_trades", "Trades", "int"),
    ("net_pnl_usdt", "Net PnL", "usd"),
    ("fees_usdt", "Fees", "usd"),
    ("funding_usdt", "Funding", "usd"),
]


def build_comparison_rows(name_to_metrics: dict[str, dict]) -> list[dict]:
    """Flatten {strategy_name: metrics_dict} into a list of plain dict rows, one per strategy."""
    rows = []
    for name, metrics in name_to_metrics.items():
        row = {"name": name}
        for key, _, _ in REPORT_METRICS:
            row[key] = metrics.get(key)
        rows.append(row)
    return rows


def _fmt(value, kind: str) -> str:
    if value is None:
        return "-"
    if kind == "pct":
        return f"{value * 100:+.2f}%"
    if kind == "usd":
        return f"${value:,.0f}"
    if kind == "int":
        return f"{value:,.0f}"
    if value == float("inf"):
        return "inf"
    return f"{value:.2f}"


def print_comparison_table(name_to_metrics: dict[str, dict], title: str = "Strategy Comparison") -> None:
    console = Console()
    table = Table(title=title, box=box.SIMPLE_HEAVY)
    table.add_column("Strategy", style="bold")
    for _, label, _ in REPORT_METRICS:
        table.add_column(label, justify="right")

    for row in build_comparison_rows(name_to_metrics):
        cells = [row["name"]] + [_fmt(row[key], kind) for key, _, kind in REPORT_METRICS]
        table.add_row(*cells)

    console.print(table)


def save_comparison_json(name_to_metrics: dict[str, dict], out_path: Path | None = None) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = out_path or (RESULTS_DIR / "compare_latest.json")

    serializable = {}
    for name, metrics in name_to_metrics.items():
        clean = {}
        for k, v in metrics.items():
            if k == "per_year_returns":
                clean[k] = {str(ts): float(val) for ts, val in v.items()}
            elif k == "long_vs_short":
                clean[k] = v
            elif hasattr(v, "item"):  # numpy scalar
                clean[k] = v.item()
            else:
                clean[k] = v
        serializable[name] = clean

    out_path.write_text(json.dumps(serializable, indent=2, default=str))
    return out_path
