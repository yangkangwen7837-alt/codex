"""Catalyst Score（规格书第 31 节）：Probability × Impact × Timing。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..pipeline.dataset import BigFishDataset
from ..utils import clip_score, data_flag, scale, weighted_frame
from .common import forecast_snapshot, latest_snapshot, stock_industry_map


def compute_catalyst(dataset: BigFishDataset) -> pd.DataFrame:
    settings = dataset.settings
    weights = dict(settings.path("catalyst.weights", {}))
    panel = dataset.fundamentals
    if panel is None or panel.empty:
        return pd.DataFrame()
    latest = latest_snapshot(panel)
    index = latest.index

    fc = forecast_snapshot(dataset, window_days=200)
    fc_mid = fc["fc_growth_mid"].reindex(index) if not fc.empty else pd.Series(np.nan, index=index)
    fc_date = fc["fc_ann_date"].reindex(index) if not fc.empty else pd.Series(np.nan, index=index)

    as_of = pd.to_datetime(dataset.as_of)
    fc_recency = pd.to_datetime(fc_date, format="%Y%m%d", errors="coerce")
    days_since_fc = (as_of - fc_recency).dt.days

    # 1) 财报/预告事件（35%）：已发预告为正 + 事件新鲜度
    earnings_event = pd.Series(np.nan, index=index)
    has_fc = fc_mid.notna()
    earnings_event[has_fc] = (scale(fc_mid[has_fc], -30, 100) * 0.6
                              + scale(-days_since_fc[has_fc], -180, 0) * 0.4)
    # 没有预告的：临近下一期财报（距上次公告越久 → 越接近新财报窗口）
    last_ann = pd.to_datetime(latest["ann_date"], format="%Y%m%d", errors="coerce")
    days_since_ann = (as_of - last_ann).dt.days.reindex(index)
    fallback = scale(days_since_ann, 0, 120) * 0.6 + 35
    earnings_event = earnings_event.where(has_fc, fallback)

    # 2) 资本动作（25%）：回购 / 股东增持
    capital = pd.Series(0.0, index=index)
    if dataset.repurchase is not None and not dataset.repurchase.empty:
        rp = dataset.repurchase.copy()
        rp["ann_date"] = rp["ann_date"].astype(str)
        cutoff = (as_of - pd.Timedelta(days=365)).strftime("%Y%m%d")
        rp = rp.loc[rp["ann_date"] >= cutoff]
        recent = rp.groupby("ts_code")["ann_date"].max()
        capital = capital.add(recent.reindex(index).notna().astype(float) * 70, fill_value=0)
    if dataset.holder_trades is not None and not dataset.holder_trades.empty:
        ht = dataset.holder_trades.copy()
        ht["ann_date"] = ht["ann_date"].astype(str)
        cutoff = (as_of - pd.Timedelta(days=180)).strftime("%Y%m%d")
        ht = ht.loc[ht["ann_date"] >= cutoff]
        ht["_inc"] = ht["in_de"].astype(str).str.upper().str[0] == "I"
        net = ht.groupby("ts_code")["_inc"].sum()
        capital = capital.add((net.reindex(index).fillna(0) > 0).astype(float) * 30, fill_value=0)
    capital = capital.clip(0, 100)

    # 3) 行业供给收缩 / 价格（20%+20%）
    industry_map = stock_industry_map(dataset).drop_duplicates("ts_code").set_index("ts_code")
    ind_mom = _industry_momentum(dataset)
    ind_code = industry_map["industry_code"].reindex(index)
    supply = ind_code.map(ind_mom).map(lambda v: scale(v, -0.08, 0.15) if pd.notna(v) else np.nan)
    price_action = ind_code.map(ind_mom).map(lambda v: scale(v, -0.05, 0.10) if pd.notna(v) else np.nan)

    components = {
        "earnings_event": earnings_event,
        "capital_action": capital,
        "industry_supply": supply,
        "price_action": price_action,
    }
    frame = pd.DataFrame(index=index)
    for key, series in components.items():
        frame[f"cs_{key}"] = series
    score, coverage = weighted_frame(
        frame[[f"cs_{k}" for k in components]], {f"cs_{k}": v for k, v in weights.items()}
    )
    frame["catalyst"] = score
    frame["catalyst_coverage"] = coverage
    min_cov = float(settings.path("catalyst.min_coverage", 0.5))
    frame.loc[frame["catalyst_coverage"] < min_cov, "catalyst"] = np.nan
    frame["catalyst_flag"] = [data_flag(c, 0.8, min_cov) for c in frame["catalyst_coverage"]]
    return frame.reset_index()


def _industry_momentum(dataset: BigFishDataset) -> pd.Series:
    ind = dataset.industry_daily
    if ind is None or ind.empty:
        return pd.Series(dtype=float)
    out = {}
    for code, sub in ind.groupby("ts_code"):
        close = sub.sort_values("trade_date")["close"].astype(float).reset_index(drop=True)
        if len(close) < 61:
            continue
        out[code] = float(close.iloc[-1] / close.iloc[-61] - 1)
    return pd.Series(out, dtype=float)
