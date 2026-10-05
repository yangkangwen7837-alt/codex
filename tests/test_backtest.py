"""Phase 6 回测模块测试：调仓日、数据卫生、分层统计、组合曲线。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bigfish.backtest.analysis import (ROUND_TRIP_COST, _summary, group_stats,  # noqa: E402
                                       portfolio_stats)
from bigfish.backtest.engine import apply_hygiene, rebalance_dates  # noqa: E402


def test_rebalance_dates_monthly_and_quarterly():
    dates = ["20240102", "20240131", "20240201", "20240229", "20240329", "20240401"]
    monthly = rebalance_dates(dates, "20240101", "20240430", "M")
    # 每期取区间内最后一个交易日；4 月只有 04-01 落在区间内，也构成一期
    assert monthly == ["20240131", "20240229", "20240329", "20240401"]
    quarterly = rebalance_dates(dates, "20240101", "20240430", "Q")
    assert quarterly == ["20240329", "20240401"]


def test_rebalance_dates_respects_range():
    dates = ["20240131", "20240229", "20240329"]
    assert rebalance_dates(dates, "20240201", "20240301", "M") == ["20240229"]


def _panel(rows: int = 200, dates: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    records = []
    for d in range(dates):
        for i in range(rows):
            records.append({
                "as_of": f"2024{d + 1:02d}28",
                "ts_code": f"{i:06d}.SZ",
                "tradeable": True,
                "risk_block": False,
                "trail_ret20": 0.02,
                "trail_ret60": 0.05,
                "entry_gap": 0.001,
                "max_abs_move": 0.05,
                "obs_days": 250,
                "expected_days": 251,
                "frs": rng.normal(50, 15),
                "pcs": rng.normal(50, 15),
                "ods": rng.normal(50, 15),
                "bfs": None,
                "ret20": None,
                "ret60": None,
            })
    df = pd.DataFrame(records)
    # 收益与该股在截面内的 FRS 排名同向（用于验证分层统计方向）
    df["bfs"] = df.groupby("as_of")["frs"].transform(lambda s: s.rank(pct=True) * 100)
    df["ret20"] = (df["bfs"] - 50) / 100.0 + rng.normal(0, 0.02, len(df))
    df["ret60"] = (df["bfs"] - 50) / 100.0 * 1.5 + rng.normal(0, 0.03, len(df))
    return df


def test_group_stats_direction():
    panel = _panel()
    stats = group_stats(panel, "frs", [20, 60])
    top = stats.loc[(stats["group"] == "TOP 10%") & (stats["horizon"] == 20), "mean"].iloc[0]
    bottom = stats.loc[(stats["group"] == "BOTTOM 20%") & (stats["horizon"] == 20), "mean"].iloc[0]
    assert top > bottom


def test_cost_is_applied():
    values = pd.Series([0.0])
    assert _summary(values - ROUND_TRIP_COST)["mean"] == pytest.approx(-ROUND_TRIP_COST)


def test_portfolio_stats_curve_and_drawdown():
    panel = _panel(dates=6)
    res = portfolio_stats(panel, "bfs", "ret20", 0.8)
    assert res["cohorts"] == 6
    assert np.isfinite(res["total_return"])
    assert res["max_drawdown"] <= 0
    assert 0 <= res["win_rate"] <= 1


def test_apply_hygiene_records_reasons():
    panel = _panel(rows=10, dates=1)
    panel.loc[0, "tradeable"] = False
    panel.loc[1, "trail_ret60"] = np.nan
    panel.loc[2, "risk_block"] = True
    panel.loc[3, "entry_gap"] = 0.2
    clean = apply_hygiene(panel)
    assert len(clean) == 6
    reasons = clean.attrs["dropped_reasons"]
    assert sum(reasons.values()) == 4
    assert "无名称/无行业/当日未交易" in reasons
    assert "RISK_BLOCK" in reasons
