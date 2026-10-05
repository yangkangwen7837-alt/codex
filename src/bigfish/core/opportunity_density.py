"""LEVEL 2 · Opportunity Density Score（规格书第 6 节）。

ODS = 30% 行业基本面趋势 + 20% 行业资金趋势 + 20% 盈利预期变化
    + 15% 价格/库存周期 + 10% 行业估值位置 + 5% 政策/产业催化
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..pipeline.dataset import BigFishDataset
from ..utils import clip_score, data_flag, nan_weighted_mean
from .common import (cross_section_rank, industry_aggregate, industry_breadth,
                     latest_snapshot, stock_industry_map)


def _industry_index_features(industry_daily: pd.DataFrame, benchmark_ret: dict[str, float]) -> pd.DataFrame:
    rows = []
    if industry_daily is None or industry_daily.empty:
        return pd.DataFrame()
    for code, sub in industry_daily.groupby("ts_code"):
        sub = sub.sort_values("trade_date")
        close = sub["close"].astype(float).reset_index(drop=True)
        amount = sub["amount"].astype(float).reset_index(drop=True)
        if len(close) < 61:
            continue
        row = {
            "industry_code": code,
            "name": sub["name"].iloc[-1] if "name" in sub.columns else code,
            "last": float(close.iloc[-1]),
            "ret20": float(close.iloc[-1] / close.iloc[-21] - 1),
            "ret60": float(close.iloc[-1] / close.iloc[-61] - 1),
            "ma60": float(close.rolling(60).mean().iloc[-1]),
            "amount20": float(amount.tail(20).mean()),
            "amount60": float(amount.tail(60).mean()),
            "vol20": float(close.pct_change().tail(20).std()),
        }
        row["rel20"] = row["ret20"] - benchmark_ret.get("ret20", np.nan)
        row["rel60"] = row["ret60"] - benchmark_ret.get("ret60", np.nan)
        row["price_vs_ma60"] = row["last"] / row["ma60"] - 1 if row["ma60"] else np.nan
        row["amount_ratio"] = row["amount20"] / row["amount60"] if row["amount60"] else np.nan
        if "pe" in sub.columns:
            pe_series = pd.to_numeric(sub["pe"], errors="coerce").dropna()
            row["pe"] = float(pe_series.iloc[-1]) if len(pe_series) else np.nan
            row["pe_pct"] = float((pe_series <= row["pe"]).mean() * 100) if len(pe_series) > 30 else np.nan
        if "pb" in sub.columns:
            pb_series = pd.to_numeric(sub["pb"], errors="coerce").dropna()
            row["pb"] = float(pb_series.iloc[-1]) if len(pb_series) else np.nan
            row["pb_pct"] = float((pb_series <= row["pb"]).mean() * 100) if len(pb_series) > 30 else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def compute_ods(dataset: BigFishDataset) -> tuple[pd.DataFrame, pd.DataFrame]:
    """返回 (行业 ODS 明细, 个股 → 行业 ODS 映射)。"""
    settings = dataset.settings
    weights = dict(settings.path("ods.weights", {}))
    industry_map = stock_industry_map(dataset)

    # 基准指数动量
    bench_code = str(settings.path("ods.relative_benchmark", "000300.SH"))
    bench = dataset.indices.loc[dataset.indices["ts_code"] == bench_code].sort_values("trade_date")
    bench_ret: dict[str, float] = {}
    if not bench.empty and len(bench) > 61:
        close = bench["close"].astype(float).reset_index(drop=True)
        bench_ret["ret20"] = float(close.iloc[-1] / close.iloc[-21] - 1)
        bench_ret["ret60"] = float(close.iloc[-1] / close.iloc[-61] - 1)

    index_feat = _industry_index_features(dataset.industry_daily, bench_ret)
    if index_feat.empty:
        return pd.DataFrame(), industry_map.assign(ods=np.nan)

    index_feat = index_feat.rename(columns={"name": "industry_name"})

    # ---- 6.1 行业景气改善（来自成员公司财报中位数）----
    fund_cols = ["revenue_yoy", "np_yoy", "revenue_accel", "np_accel", "gross_margin_chg_yoy",
                 "inventory_to_revenue_chg", "contract_liab_yoy", "ttm_revenue"]
    fund = industry_aggregate(dataset.fundamentals, industry_map, fund_cols)
    if not fund.empty:
        fund = fund.rename(columns={c: f"med_{c}" for c in fund.columns if c not in ("members",)})
        index_feat = index_feat.merge(fund, left_on="industry_name", right_index=True, how="left")

    # ---- 6.3 盈利预期变化（业绩预告：中位增速 + Revision Breadth）----
    rev = _revision_features(dataset, industry_map)
    if not rev.empty:
        index_feat = index_feat.merge(rev, left_on="industry_name", right_index=True, how="left")

    # ---- 6.5 政策 / 产业催化代理（股东增持 + 回购）----
    cat = _catalyst_proxy(dataset, industry_map)
    if not cat.empty:
        index_feat = index_feat.merge(cat, left_on="industry_name", right_index=True, how="left")

    # ---- 逐项打分（截面分位）----
    components: dict[str, pd.Series] = {}

    trend_parts, trend_weights = [], []
    for col, w in (("med_revenue_yoy", 0.35), ("med_np_yoy", 0.35), ("med_np_accel", 0.30)):
        if col in index_feat.columns:
            trend_parts.append(cross_section_rank(index_feat[col]) * w)
            trend_weights.append(pd.Series(index_feat[col]).notna().astype(float) * w)
    if trend_parts:
        num = sum(trend_parts)
        den = sum(trend_weights).replace(0, np.nan)
        components["fundamental_trend"] = (num / den).clip(0, 100)

    cap = pd.Series(np.nan, index=index_feat.index)
    cap_parts, cap_w = [], []
    for col, w in (("ret20", 0.35), ("ret60", 0.25), ("rel20", 0.25), ("amount_ratio", 0.15)):
        if col in index_feat.columns:
            cap_parts.append(cross_section_rank(index_feat[col]) * w)
            cap_w.append(index_feat[col].notna().astype(float) * w)
    if cap_parts:
        cap = (sum(cap_parts) / sum(cap_w).replace(0, np.nan)).clip(0, 100)
        components["capital_trend"] = cap

    revision = pd.Series(np.nan, index=index_feat.index)
    if "med_forecast_growth" in index_feat.columns or "revision_breadth" in index_feat.columns:
        parts, ws = [], []
        if "med_forecast_growth" in index_feat.columns:
            parts.append(cross_section_rank(index_feat["med_forecast_growth"]) * 0.6)
            ws.append(index_feat["med_forecast_growth"].notna().astype(float) * 0.6)
        if "revision_breadth" in index_feat.columns:
            parts.append(cross_section_rank(index_feat["revision_breadth"]) * 0.4)
            ws.append(index_feat["revision_breadth"].notna().astype(float) * 0.4)
        revision = (sum(parts) / sum(ws).replace(0, np.nan)).clip(0, 100)
        components["earnings_revision"] = revision

    cycle_parts, cycle_w = [], []
    if "price_vs_ma60" in index_feat.columns:
        cycle_parts.append(cross_section_rank(index_feat["price_vs_ma60"]) * 0.6)
        cycle_w.append(index_feat["price_vs_ma60"].notna().astype(float) * 0.6)
    if "med_inventory_to_revenue_chg" in index_feat.columns:
        cycle_parts.append(cross_section_rank(-index_feat["med_inventory_to_revenue_chg"]) * 0.4)
        cycle_w.append(index_feat["med_inventory_to_revenue_chg"].notna().astype(float) * 0.4)
    if cycle_parts:
        components["price_inventory_cycle"] = (sum(cycle_parts) / sum(cycle_w).replace(0, np.nan)).clip(0, 100)

    if "pe_pct" in index_feat.columns:
        components["valuation_position"] = (100 - index_feat["pe_pct"]).clip(0, 100)

    if "catalyst_proxy" in index_feat.columns:
        components["policy_catalyst"] = cross_section_rank(index_feat["catalyst_proxy"])

    # ---- 加权（缺失项重新归一化）----
    scores, coverages = [], []
    for idx in index_feat.index:
        values = {k: float(v.loc[idx]) if pd.notna(v.loc[idx]) else None for k, v in components.items()}
        score, coverage = nan_weighted_mean(values, weights)
        scores.append(score)
        coverages.append(coverage)
    index_feat["ods"] = scores
    index_feat["coverage"] = coverages
    for key, series in components.items():
        index_feat[f"ods_{key}"] = series
    index_feat["data_flag"] = [
        data_flag(c, 0.75, float(settings.path("ods.min_factor_coverage", 0.5)))
        for c in index_feat["coverage"]
    ]
    index_feat = index_feat.sort_values("ods", ascending=False).reset_index(drop=True)
    index_feat["ods_rank"] = index_feat["ods"].rank(ascending=False, method="min")
    high = float(settings.path("ods.thresholds.high_density", 80))
    good = float(settings.path("ods.thresholds.good", 70))
    watch = float(settings.path("ods.thresholds.watch", 60))
    index_feat["zone"] = pd.cut(
        index_feat["ods"], bins=[-1, watch, good, high, 101],
        labels=["LOW PRIORITY", "WATCH", "GOOD", "HIGH DENSITY"],
    ).astype(str)
    index_feat["trade_date"] = dataset.as_of

    stock_ods = industry_map.merge(
        index_feat[["industry_code", "industry_name", "ods", "ods_rank", "zone", "data_flag"]],
        on=["industry_code", "industry_name"], how="left",
    )
    return index_feat, stock_ods


def _revision_features(dataset: BigFishDataset, industry_map: pd.DataFrame, window_days: int = 90) -> pd.DataFrame:
    """业绩预告 → 行业盈利预期变化（规格书第 6.3 节，替代一致预期数据）。"""
    fc = dataset.forecast
    if fc is None or fc.empty:
        return pd.DataFrame()
    fc = fc.copy()
    fc["ann_date"] = fc["ann_date"].astype(str)
    cutoff = (pd.to_datetime(dataset.as_of) - pd.Timedelta(days=window_days)).strftime("%Y%m%d")
    recent = fc.loc[fc["ann_date"] >= cutoff]
    if recent.empty:
        recent = fc
    recent = recent.merge(industry_map, on="ts_code", how="left")
    mid = (pd.to_numeric(recent["p_change_min"], errors="coerce") +
           pd.to_numeric(recent["p_change_max"], errors="coerce")) / 2.0
    recent = recent.assign(_mid=mid)
    grouped = recent.groupby("industry_name").agg(
        med_forecast_growth=("_mid", "median"),
        forecast_count=("_mid", "count"),
    )
    breadth = recent.assign(_pos=(recent["_mid"] > 0).astype(float)).groupby("industry_name")["_pos"].mean()
    grouped["revision_breadth"] = breadth
    return grouped


def _catalyst_proxy(dataset: BigFishDataset, industry_map: pd.DataFrame) -> pd.DataFrame:
    """政策/产业催化代理：近 180 天股东增持与回购事件密度。"""
    frames = []
    if dataset.holder_trades is not None and not dataset.holder_trades.empty:
        ht = dataset.holder_trades.copy()
        ht["ann_date"] = ht["ann_date"].astype(str)
        cutoff = (pd.to_datetime(dataset.as_of) - pd.Timedelta(days=180)).strftime("%Y%m%d")
        ht = ht.loc[ht["ann_date"] >= cutoff]
        ht["_inc"] = (ht["in_de"].astype(str).str.upper().str[0] == "I").astype(float)
        frames.append(ht.merge(industry_map, on="ts_code", how="left")[["industry_name", "_inc"]])
    if dataset.repurchase is not None and not dataset.repurchase.empty:
        rp = dataset.repurchase.copy()
        rp["ann_date"] = rp["ann_date"].astype(str)
        cutoff = (pd.to_datetime(dataset.as_of) - pd.Timedelta(days=180)).strftime("%Y%m%d")
        rp = rp.loc[rp["ann_date"] >= cutoff]
        rp["_inc"] = 1.0
        frames.append(rp.merge(industry_map, on="ts_code", how="left")[["industry_name", "_inc"]])
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True).dropna(subset=["industry_name"])
    counts = merged.groupby("industry_name")["_inc"].sum()
    members = industry_map.dropna(subset=["industry_name"]).groupby("industry_name")["ts_code"].nunique()
    out = pd.DataFrame({"catalyst_proxy": counts / members.replace(0, np.nan)})
    return out
