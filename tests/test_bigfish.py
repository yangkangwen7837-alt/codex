"""单元测试：口径正确性、权重守恒、point-in-time、输出不变量。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bigfish.config import load_settings  # noqa: E402
from bigfish.core.fundamental_reversal import classify_reversal_type  # noqa: E402
from bigfish.periods import quarter_list, shift_period, single_quarterize  # noqa: E402
from bigfish.pipeline.fundamentals import build_fundamental_panel, _dedupe_pit  # noqa: E402
from bigfish.utils import nan_weighted_mean, pct_change, scale  # noqa: E402


def test_quarter_helpers():
    quarters = quarter_list("20230331", "20240630")
    assert quarters == ["20230331", "20230630", "20230930", "20231231", "20240331", "20240630"]
    assert shift_period("20260630", -4) == "20250630"
    assert shift_period("20230331", -1) == "20221231"


def test_single_quarterize_cumulative_to_single():
    df = pd.DataFrame(
        {
            "ts_code": ["A"] * 4,
            "end_date": ["20250331", "20250630", "20250930", "20251231"],
            "revenue": [10.0, 25.0, 45.0, 70.0],
        }
    )
    out = single_quarterize(df, ["revenue"]).sort_values("end_date")
    assert out["revenue"].tolist() == [10.0, 15.0, 20.0, 25.0]


def test_single_quarterize_missing_previous_quarter_is_nan():
    df = pd.DataFrame(
        {
            "ts_code": ["A", "A"],
            "end_date": ["20250331", "20250930"],
            "revenue": [10.0, 45.0],
        }
    )
    out = single_quarterize(df, ["revenue"]).sort_values("end_date")
    assert out["revenue"].tolist()[0] == 10.0
    assert np.isnan(out["revenue"].tolist()[1])  # 缺 Q2 累计 → 不猜


def test_pct_change_sign_convention_for_negative_base():
    # 基期为亏损 −100，本期 +50 → 改善幅度 +150%
    out = pct_change(pd.Series([50.0]), pd.Series([-100.0]))
    assert out.iloc[0] == pytest.approx(1.5)


def test_dedupe_pit_keeps_earliest_announcement():
    df = pd.DataFrame(
        {
            "ts_code": ["A", "A"],
            "end_date": ["20260630", "20260630"],
            "ann_date": ["20260828", "20260920"],  # 更正公告晚于首次公告
            "revenue": [100.0, 90.0],
        }
    )
    out = _dedupe_pit(df)
    assert len(out) == 1
    assert out["ann_date"].iloc[0] == "20260828"
    assert out["revenue"].iloc[0] == 100.0


def test_fundamental_panel_is_point_in_time():
    income = pd.DataFrame(
        {
            "ts_code": ["A"] * 7,
            "ann_date": ["20250425", "20250825", "20251025", "20260410",
                         "20260425", "20260825", "20261020"],
            "end_date": ["20250331", "20250630", "20250930", "20251231",
                         "20260331", "20260630", "20260930"],
            "revenue": [120.0, 260.0, 390.0, 520.0, 300.0, 460.0, 700.0],
            "oper_cost": [70.0, 150.0, 220.0, 300.0, 170.0, 250.0, 400.0],
            "n_income_attr_p": [12.0, 26.0, 39.0, 52.0, 30.0, 46.0, 70.0],
        }
    )
    panel = build_fundamental_panel(income, pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
    assert not panel.empty
    # 2026Q3（2026-10-20 公告）尚未可见：过滤后最新报告期必须是 2026Q2
    visible = panel.loc[panel["ann_date"].astype(str) <= "20260930"]
    latest = visible.sort_values("end_date").iloc[-1]
    assert latest["end_date"] == "20260630"
    # 2026Q2 单季 = 460 − 300 = 160；去年同期单季 = 260 − 120 = 140
    assert latest["sq_revenue"] == pytest.approx(160.0)
    assert latest["revenue_yoy"] == pytest.approx(160.0 / 140.0 - 1, rel=1e-6)
    # 每行都带公告日（point-in-time 的必要条件）
    assert panel["ann_date"].notna().all()


def test_weights_are_conserved():
    settings = load_settings()
    bfs = settings.path("bfs.weights")
    assert sum(bfs.values()) == pytest.approx(1.0, abs=1e-9)
    frs = settings.path("frs.weights")
    assert sum(frs.values()) == pytest.approx(100.0, abs=1e-9)
    ods = settings.path("ods.weights")
    assert sum(ods.values()) == pytest.approx(1.0, abs=1e-9)
    pcs = settings.path("pcs.weights")
    assert sum(pcs.values()) == pytest.approx(100.0, abs=1e-9)


def test_nan_weighted_mean_renormalizes():
    weights = {"a": 0.5, "b": 0.3, "c": 0.2}
    score, coverage = nan_weighted_mean({"a": 80.0, "b": None, "c": 60.0}, weights)
    assert score == pytest.approx((80 * 0.5 + 60 * 0.2) / 0.7)
    assert coverage == pytest.approx(0.7)
    score, coverage = nan_weighted_mean({"a": None, "b": None, "c": None}, weights)
    assert np.isnan(score) and coverage == 0.0


def test_scale_clips_and_supports_series():
    assert scale(0.0, 0.0, 1.0) == 0.0
    assert scale(2.0, 0.0, 1.0) == 100.0
    out = scale(pd.Series([-1.0, 0.5, 3.0]), 0.0, 1.0)
    assert out.tolist() == [0.0, 50.0, 100.0]


def test_reversal_type_requires_evidence():
    base = {"f_revenue_yoy": -0.1, "f_gross_margin": -0.02, "f_contract_liab_yoy": np.nan}
    assert classify_reversal_type(base, {"price_vs_ma60": 0.1, "inventory_down": True}) == "SUPPLY_CONTRACTION"
    assert classify_reversal_type({**base, "f_revenue_yoy": 0.2, "f_contract_liab_yoy": 0.3},
                                  {"price_vs_ma60": 0.0, "inventory_down": False}) == "DEMAND_REVERSAL"
    assert classify_reversal_type({**base, "f_revenue_yoy": -0.2, "f_gross_margin": 0.05},
                                  {}) == "COST_REVERSAL"


SNAPSHOT = Path(__file__).resolve().parents[1] / "data" / "processed" / "bigfish_score_latest.parquet"


@pytest.mark.skipif(not SNAPSHOT.exists(), reason="需要先运行 scripts/run_daily.py")
def test_output_invariants():
    df = pd.read_parquet(SNAPSHOT)
    # 1) 可交易标的一律有名称与行业
    tradeable = df.loc[df["tradeable"].fillna(False)]
    assert tradeable["name"].notna().all()
    assert tradeable["industry"].notna().all()
    # 2) Risk Block 不得出现在可入池集合
    pool = tradeable.loc[(tradeable["grade"] != "Reject") & (~tradeable["risk_block"].fillna(False).astype(bool))]
    assert not pool.empty
    # 3) S 级硬门槛
    s = df.loc[df["grade"] == "S"]
    if not s.empty:
        assert (s["frs"] >= 75).all() and (s["lis"] >= 70).all() and (s["pcs"] >= 70).all()
        assert (s["kill_count"] == 0).all()
    # 4) 分数范围
    for col in ("frs", "lis", "pcs", "rps", "catalyst", "bfs"):
        values = df[col].dropna()
        assert values.between(0, 100).all(), col
    # 5) point-in-time：最新报告公告日不晚于基准日
    dated = df.loc[df["latest_ann_date"].notna()]
    assert (dated["latest_ann_date"].astype(str) <= dated["as_of"].astype(str)).all()
    # 6) 动作集合受控
    allowed = {"RESEARCH", "WATCH", "WAIT_TRIGGER", "STARTER_POSITION", "ADD_ON_CONFIRMATION",
               "HOLD", "REDUCE", "EXIT", "REJECT", "WAIT_PULLBACK"}
    assert set(df["action"].dropna().unique()) <= allowed
