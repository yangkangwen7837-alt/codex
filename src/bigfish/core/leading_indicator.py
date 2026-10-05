"""LEVEL 4 · Leading Indicator Score（规格书第 15~18 节）。

财报是滞后指标，本模块寻找**领先 1~2 个季度**的改善信号。
当前数据源下可直接验证的领先指标：
    订单/需求  → 收入加速度 + 合同负债增速（领先收入）
    产品价格   → 毛利率同比变化 + 行业指数价格动量（周期行业）
    库存周期   → 存货/收入比变化（去库 = 领先信号）
    合同负债   → 合同负债同比（预收 = 在手订单代理）
    管理层指引 → 业绩预告区间中枢
不可得的指标（产能利用率 / 行业高频价格）**不参与打分**，只记入 data_gap。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import load_indicator_templates
from ..pipeline.dataset import BigFishDataset
from ..utils import (clip_score, data_flag, scale, weighted_available, weighted_frame)
from .common import forecast_snapshot, tail_wide


def _template_for(templates, industry: str) -> dict:
    if industry and industry in templates.as_dict():
        return templates[industry].as_dict()
    return templates["default"].as_dict()


def compute_lis(dataset: BigFishDataset) -> pd.DataFrame:
    settings = dataset.settings
    weights = dict(settings.path("lis.weights", {}))
    panel = dataset.fundamentals
    if panel is None or panel.empty:
        return pd.DataFrame()
    wide = tail_wide(panel, ["revenue_yoy", "revenue_accel", "gross_margin_chg_yoy",
                             "inventory_to_revenue_chg", "contract_liab_yoy", "contract_liab_to_revenue",
                             "np_yoy", "gross_margin"], n=5)
    if wide.empty:
        return pd.DataFrame()

    def col(name: str, t: int = 0) -> pd.Series:
        key = f"{name}_t{t}"
        return wide[key] if key in wide.columns else pd.Series(np.nan, index=wide.index)

    fc = forecast_snapshot(dataset)
    fc_mid = fc["fc_growth_mid"].reindex(wide.index) if not fc.empty else pd.Series(np.nan, index=wide.index)

    industry_price = _industry_price_momentum(dataset)
    industry_inventory = _industry_inventory_trend(dataset)

    from .common import stock_industry_map

    industry_map = stock_industry_map(dataset).drop_duplicates("ts_code").set_index("ts_code")
    templates = load_indicator_templates(settings)
    industry_name = industry_map["industry_name"].reindex(wide.index)
    ind_price = industry_map["industry_code"].map(industry_price).reindex(wide.index)
    ind_inv = industry_map["industry_name"].map(industry_inventory).reindex(wide.index)

    components: dict[str, pd.Series] = {}

    # 订单 / 需求（25）：回测暴露 LIS 与 FRS 重复计分（收入加速度本就是同步指标），
    # 因此把权重改为"合同负债（真正的领先项）70% + 收入加速度（同步，仅作辅助）30%"。
    rev_accel = col("revenue_accel")
    contract = col("contract_liab_yoy")
    orders = weighted_available([(scale(contract, -0.10, 0.40), 0.7),
                                 (scale(rev_accel, -0.10, 0.30), 0.3)], index=wide.index)
    # 领先一致性加成：合同负债增速领先收入增速 → 说明订单确实走在收入前面
    lead_ok = (contract > col("revenue_yoy")) & (contract > 0)
    orders = orders.where(~lead_ok, (orders + 15).clip(0, 100))
    components["orders_demand"] = orders.clip(0, 100)

    # 产品价格（20）
    gm_chg = col("gross_margin_chg_yoy")
    components["product_price"] = weighted_available(
        [(scale(gm_chg, -0.03, 0.05), 0.6), (scale(ind_price, -0.05, 0.15), 0.4)], index=wide.index
    ).clip(0, 100)

    # 库存周期（15）：去库存为正向
    components["inventory_cycle"] = scale(-col("inventory_to_revenue_chg"), -0.05, 0.05).clip(0, 100)

    # 合同负债（10）
    components["contract_liability"] = scale(contract, -0.10, 0.50).clip(0, 100)

    # 管理层指引（5）
    components["management_guidance"] = scale(fc_mid, -20, 80).clip(0, 100)

    # 不可得的分项：显式置 NaN（不参与打分，只记录缺口）
    components["capacity_utilization"] = pd.Series(np.nan, index=wide.index)
    components["industry_highfreq"] = pd.Series(np.nan, index=wide.index)
    components["other"] = pd.Series(np.nan, index=wide.index)

    frame = pd.DataFrame(index=wide.index)
    for key, series in components.items():
        frame[f"lis_{key}"] = series
    score, coverage = weighted_frame(
        frame[[f"lis_{k}" for k in components]], {f"lis_{k}": v for k, v in weights.items()}
    )
    frame["lis"] = score
    frame["lis_coverage"] = coverage
    frame["lis_template"] = [str(_template_for(templates, ind).get("template", "DEFAULT")) for ind in industry_name]
    frame["lis_data_gap"] = [str(_template_for(templates, ind).get("data_gap_note", "")) for ind in industry_name]
    min_coverage = float(settings.path("lis.min_coverage", 0.45))
    incomplete = frame["lis_coverage"] < min_coverage
    frame.loc[incomplete, "lis"] = np.nan      # 覆盖率不足 → 视为缺失，不参与 BFS
    frame["lis_flag"] = [data_flag(c, 0.9, min_coverage) for c in frame["lis_coverage"]]
    frame["lis_stage"] = pd.cut(
        frame["lis"], bins=[-1, 60, 70, 80, 101],
        labels=["NOT CONFIRMED", "POSITIVE", "STRONG", "VERY STRONG"],
    ).astype(str)
    frame.loc[frame["lis"].isna(), "lis_stage"] = "DATA INCOMPLETE"
    # 领先一致性：合同负债增速领先收入增速（规格书第 17 节）
    frame["lis_lead_consistency"] = (contract > 0) & (contract > col("revenue_yoy"))
    return frame.reset_index()


def _industry_price_momentum(dataset: BigFishDataset) -> pd.Series:
    """行业价格动量（周期行业的产品价格代理）。"""
    ind = dataset.industry_daily
    if ind is None or ind.empty:
        return pd.Series(dtype=float)
    out = {}
    for code, sub in ind.groupby("ts_code"):
        close = sub.sort_values("trade_date")["close"].astype(float).reset_index(drop=True)
        if len(close) < 121:
            continue
        out[code] = float(close.iloc[-1] / close.iloc[-61] - 1)
    return pd.Series(out, dtype=float)


def _industry_inventory_trend(dataset: BigFishDataset) -> pd.Series:
    """行业库存趋势（成员公司存货/收入比变化中位数，负值 = 去库）。"""
    from .common import industry_aggregate, stock_industry_map

    if dataset.fundamentals is None or dataset.fundamentals.empty:
        return pd.Series(dtype=float)
    agg = industry_aggregate(dataset.fundamentals, stock_industry_map(dataset), ["inventory_to_revenue_chg"])
    if "med_inventory_to_revenue_chg" not in agg.columns:
        return pd.Series(dtype=float)
    return -agg["med_inventory_to_revenue_chg"]
