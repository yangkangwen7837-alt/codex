"""LEVEL 6 · Revaluation Potential Score（规格书第 27~30 节）。

不是判断"便不便宜"，而是判断：**如果反转成立，未来合理估值与当前价格之间还有多大空间**。
所有情景都是估值情景，不是目标价承诺（规格书第 30 节）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..pipeline.dataset import BigFishDataset
from ..utils import clip_score, scale
from .common import latest_snapshot, stock_industry_map, tail_wide


def _bucket_label(upside: float) -> str:
    if not np.isfinite(upside):
        return "UNKNOWN"
    if upside > 0.8:
        return "Very Large"
    if upside > 0.5:
        return "Large"
    if upside > 0.3:
        return "Good"
    if upside > 0.15:
        return "Moderate"
    return "Low"


def _bucket_score(upside: float) -> float:
    """规格书第 30 节分档 → 0~100 分（分段线性）。"""
    if not np.isfinite(upside):
        return float("nan")
    if upside >= 0.8:
        return float(np.clip(90 + (upside - 0.8) / 0.7 * 10, 90, 100))
    if upside >= 0.5:
        return 75 + (upside - 0.5) / 0.3 * 15
    if upside >= 0.3:
        return 60 + (upside - 0.3) / 0.2 * 15
    if upside >= 0.15:
        return 45 + (upside - 0.15) / 0.15 * 15
    if upside >= 0:
        return 20 + upside / 0.15 * 25
    return float(np.clip(20 + upside * 100, 0, 20))


def compute_rps(dataset: BigFishDataset) -> pd.DataFrame:
    settings = dataset.settings
    scenario = settings.path("rps.scenarios", {}) or {}
    pe_low, pe_high = settings.path("rps.pe_bounds", [8, 60])
    mapping = settings.path("rps.mapping", {}) or {}

    panel = dataset.fundamentals
    if panel is None or panel.empty:
        return pd.DataFrame()

    latest = latest_snapshot(panel)
    if latest.empty:
        return pd.DataFrame()
    wide = tail_wide(panel, ["np_yoy", "np_accel", "net_margin", "gross_margin", "sq_np"], n=5)

    # 行业估值中枢（申万一级行业指数 PE 最新值）
    industry_map = stock_industry_map(dataset).drop_duplicates("ts_code").set_index("ts_code")
    industry_pe: dict[str, float] = {}
    ind = dataset.industry_daily
    if ind is not None and not ind.empty and "pe" in ind.columns:
        for code, sub in ind.groupby("ts_code"):
            pe = pd.to_numeric(sub.sort_values("trade_date")["pe"], errors="coerce").dropna()
            if len(pe):
                industry_pe[code] = float(pe.iloc[-1])
    market_pe_series = pd.Series(industry_pe, dtype=float)
    market_pe = float(market_pe_series.median()) if len(market_pe_series) else np.nan

    # 当前市值 / PE
    basic = dataset.basic
    mv = pd.Series(dtype=float)
    pe_ttm = pd.Series(dtype=float)
    pb = pd.Series(dtype=float)
    if basic is not None and not basic.empty:
        last_date = basic["trade_date"].max()
        snap = basic.loc[basic["trade_date"] == last_date].drop_duplicates("ts_code").set_index("ts_code")
        mv = pd.to_numeric(snap.get("total_mv"), errors="coerce") if "total_mv" in snap else mv
        pe_ttm = pd.to_numeric(snap.get("pe_ttm"), errors="coerce") if "pe_ttm" in snap else pe_ttm
        pb = pd.to_numeric(snap.get("pb"), errors="coerce") if "pb" in snap else pb

    ttm_np = pd.to_numeric(latest["ttm_np"], errors="coerce") / 1e4        # 元 → 万元
    ttm_rev = pd.to_numeric(latest["ttm_revenue"], errors="coerce") / 1e4
    # 归一化盈利：最近 4 个单季利润的中位数 × 4（年化，剔除季节性单季异常）
    quarterly_np = (panel.sort_values(["ts_code", "end_date"]).groupby("ts_code")["sq_np"].apply(
        lambda s: s.tail(4).median()
    ) * 4.0) / 1e4

    np_yoy = wide.get("np_yoy_t0", pd.Series(np.nan, index=wide.index))
    np_accel = wide.get("np_accel_t0", pd.Series(np.nan, index=wide.index))
    rev_yoy = pd.to_numeric(latest["revenue_yoy"], errors="coerce")

    current_pe = (mv / ttm_np.replace(0, np.nan))
    normalized_np = quarterly_np.reindex(ttm_np.index)
    normalized_np = normalized_np.where(normalized_np > 0, ttm_np.clip(lower=0))

    # 亏损/微利公司的"恢复性盈利"：TTM 收入 × 历史单季净利率中位数（规格书第 28 节 Normalized Earnings）
    hist_margin = panel.groupby("ts_code")["net_margin"].median()
    recovery_np = (ttm_rev * hist_margin.reindex(ttm_np.index).clip(lower=0)).where(lambda s: s > 0)
    basis = pd.Series("TTM", index=ttm_np.index)
    basis = basis.where(normalized_np.notna(), "RECOVERY")
    normalized_np = normalized_np.where(normalized_np > 0, recovery_np)
    normalized_np = normalized_np.where(normalized_np > 0)

    growth_bonus = 1 + np.clip(np_yoy.fillna(0), 0, 1.0) * 0.3 + np.clip(np_accel.fillna(0), 0, 0.5) * 0.2

    bear_np = normalized_np * float(scenario.get("bear_multiple", 0.7))
    base_np = normalized_np * float(scenario.get("base_multiple", 1.0)) * growth_bonus
    bull_np = normalized_np * float(scenario.get("bull_multiple", 1.6)) * growth_bonus

    industry_code = industry_map["industry_code"].reindex(latest.index)
    ind_pe = industry_code.map(industry_pe).fillna(market_pe)
    base_pe = ind_pe.clip(pe_low, pe_high)

    target_mv_bear = bear_np * (base_pe * float(scenario.get("bear_pe_factor", 0.8)))
    target_mv_base = base_np * (base_pe * float(scenario.get("base_pe_factor", 1.0)))
    target_mv_bull = bull_np * (base_pe * float(scenario.get("bull_pe_factor", 1.25)))

    mv_aligned = mv.reindex(latest.index).replace(0, np.nan)
    out = pd.DataFrame(index=latest.index)
    out["current_mv"] = mv_aligned
    out["ttm_np"] = ttm_np
    out["normalized_np"] = normalized_np
    out["rps_basis"] = basis
    out["current_pe"] = current_pe
    out["industry_pe"] = base_pe
    out["rps_bear_np"] = bear_np
    out["rps_base_np"] = base_np
    out["rps_bull_np"] = bull_np
    out["upside_bear"] = (target_mv_bear / mv_aligned - 1)
    out["upside_base"] = (target_mv_base / mv_aligned - 1)
    out["upside_bull"] = (target_mv_bull / mv_aligned - 1)
    out["rps_scenario"] = out["upside_base"].map(_bucket_label)
    out["rps"] = out["upside_base"].map(_bucket_score)

    # 已经是高估值的热门股：即使是反转，重估空间也要打折
    sane_pe = current_pe.where(current_pe.between(0, 500))
    expensive = (sane_pe > pe_high * 1.2) & (out["rps"] > 60)
    out.loc[expensive, "rps"] = out.loc[expensive, "rps"] * 0.7

    # ---------------- 估值硬约束（回测失败案例库：买入时估值已高平均亏 14.7%） ----------------
    gate = settings.path("rps.gate", {}) or {}
    pe_mult = float(gate.get("pe_vs_industry_multiple", 1.5))
    min_upside = float(gate.get("min_base_upside", 0.15))
    severe_mult = float(gate.get("severe_pe_multiple", 2.0))
    severe_upside = float(gate.get("severe_upside", 0.0))
    # out 的索引在逐列赋值时可能被并集扩展（不同来源的 ts_code 集合不同），
    # 这里统一按 out.index 对齐，避免 Series 比较时标签不一致。
    industry_pe_aligned = base_pe.reindex(out.index)
    sane_pe = current_pe.where(current_pe.between(0, 500)).reindex(out.index)
    pe_expensive = sane_pe > industry_pe_aligned * pe_mult
    pe_severe = sane_pe > industry_pe_aligned * severe_mult
    upside_expensive = out["upside_base"] < min_upside
    upside_severe = out["upside_base"] < severe_upside
    out["expensive_entry"] = (pe_expensive | upside_expensive).fillna(False)
    out["severe_expensive"] = (pe_severe | upside_severe).fillna(False)
    out["rps_flag"] = np.where(out["rps"].notna(), "OK", "DATA INCOMPLETE")
    out.loc[out["expensive_entry"], "rps_flag"] = "EXPENSIVE"
    out.loc[out["severe_expensive"], "rps_flag"] = "SEVERE_EXPENSIVE"
    out["rps_note"] = "估值情景（Bear/Base/Bull），非目标价；expensive/severe_expensive 为硬约束标记"
    return out.reset_index()
