import json
from pathlib import Path

from xrp_futures.reports.report import build_comparison_rows, save_comparison_json


def _fake_metrics(sharpe):
    return dict(
        cagr=0.1, sharpe=sharpe, sortino=0.2, calmar=0.3, max_drawdown=-0.1,
        win_rate=0.5, profit_factor=1.2, total_trades=10, net_pnl_usdt=500.0,
        fees_usdt=20.0, funding_usdt=-5.0, per_year_returns={}, long_vs_short={},
    )


def test_build_comparison_rows_preserves_order_and_values():
    data = {"Buy & Hold": _fake_metrics(0.5), "Strategy D": _fake_metrics(1.5)}
    rows = build_comparison_rows(data)
    assert [r["name"] for r in rows] == ["Buy & Hold", "Strategy D"]
    assert rows[0]["sharpe"] == 0.5
    assert rows[1]["sharpe"] == 1.5


def test_build_comparison_rows_handles_missing_metric_gracefully():
    incomplete = {"cagr": 0.1}  # missing everything else
    rows = build_comparison_rows({"X": incomplete})
    assert rows[0]["sharpe"] is None


def test_save_comparison_json_round_trips(tmp_path):
    data = {"A": _fake_metrics(1.0)}
    out_path = save_comparison_json(data, out_path=Path(tmp_path) / "test_compare.json")
    assert out_path.exists()
    loaded = json.loads(out_path.read_text())
    assert loaded["A"]["sharpe"] == 1.0
