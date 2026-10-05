"""跨模块共用的截面工具：行业映射、最近 N 个季度的宽表、行业统计。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..pipeline.dataset import BigFishDataset
from ..utils import rolling_last


def stock_industry_map(dataset: BigFishDataset) -> pd.DataFrame:
    """ts_code → 申万一级行业（缺失时回退 stock_basic.industry）。"""
    if dataset.membership is not None and not dataset.membership.empty:
        mapping = dataset.membership[["ts_code", "industry_code", "industry_name"]].copy()
    else:
        mapping = pd.DataFrame(columns=["ts_code", "industry_code", "industry_name"])
    if dataset.universe is not None and not dataset.universe.empty:
        fallback = dataset.universe[["ts_code", "industry"]].rename(
            columns={"industry": "basic_industry"}
        )
        mapping = mapping.merge(fallback, on="ts_code", how="outer")
    else:
        mapping["basic_industry"] = np.nan
    mapping["industry_name"] = mapping["industry_name"].fillna(mapping["basic_industry"])
    mapping["industry_code"] = mapping["industry_code"].fillna(mapping["industry_name"])
    return mapping[["ts_code", "industry_code", "industry_name"]]


def tail_wide(panel: pd.DataFrame, cols: list[str], n: int = 6) -> pd.DataFrame:
    """把长表面板转成"最近 n 个季度"宽表：``<col>_t0`` 为最新一季度。"""
    if panel is None or panel.empty:
        return pd.DataFrame()
    use_cols = [c for c in cols if c in panel.columns]
    if not use_cols:
        return pd.DataFrame()
    p = panel.sort_values(["ts_code", "end_date"]).copy()
    p["_k"] = p.groupby("ts_code", sort=False).cumcount(ascending=False)
    p = p.loc[p["_k"] < n]
    wide = p.pivot(index="ts_code", columns="_k", values=use_cols)
    wide.columns = [f"{col}_t{k}" for col, k in wide.columns]
    wide = wide.sort_index()
    wide.index.name = "ts_code"
    return wide


def latest_snapshot(panel: pd.DataFrame) -> pd.DataFrame:
    """每只股票在 as_of 之前可见的最新一期报告。"""
    if panel is None or panel.empty:
        return pd.DataFrame()
    p = panel.sort_values(["ts_code", "end_date", "ann_date"])
    return p.drop_duplicates(subset=["ts_code"], keep="last").set_index("ts_code")


def industry_aggregate(panel: pd.DataFrame, industry_map: pd.DataFrame,
                       value_cols: list[str]) -> pd.DataFrame:
    """按行业聚合最新报告期的中位数（行业景气趋势的基础）。"""
    latest = latest_snapshot(panel)
    if latest.empty:
        return pd.DataFrame()
    merged = latest.reset_index().merge(industry_map, on="ts_code", how="left")
    merged = merged.loc[merged["industry_name"].notna()]
    cols = [c for c in value_cols if c in merged.columns]
    agg = merged.groupby("industry_name")[cols].median(numeric_only=True)
    agg["members"] = merged.groupby("industry_name")["ts_code"].count()
    return agg


def industry_breadth(panel: pd.DataFrame, industry_map: pd.DataFrame,
                     condition_col: str, positive: bool = True) -> pd.Series:
    """行业内满足条件的公司占比（用于 Revision Breadth 类指标）。"""
    latest = latest_snapshot(panel)
    if latest.empty or condition_col not in latest.columns:
        return pd.Series(dtype=float)
    merged = latest.reset_index().merge(industry_map, on="ts_code", how="left")
    values = merged[condition_col]
    flags = (values > 0) if positive else (values < 0)
    flags = flags.where(values.notna())
    merged = merged.assign(_flag=flags.astype(float))
    return merged.groupby("industry_name")["_flag"].mean()


def cross_section_rank(series: pd.Series) -> pd.Series:
    """0~100 分位（值越大分越高）。"""
    return series.rank(pct=True, na_option="keep") * 100.0


def forecast_snapshot(dataset: BigFishDataset, window_days: int | None = None) -> pd.DataFrame:
    """每只股票最近一次业绩预告（point-in-time：只看 as_of 之前公告的）。"""
    fc = dataset.forecast
    if fc is None or fc.empty:
        return pd.DataFrame(columns=["ts_code", "fc_ann_date", "fc_end_date", "fc_type",
                                     "fc_growth_mid", "fc_positive"])
    fc = fc.copy()
    fc["ann_date"] = fc["ann_date"].astype(str)
    if window_days is not None:
        cutoff = (pd.to_datetime(dataset.as_of) - pd.Timedelta(days=window_days)).strftime("%Y%m%d")
        fc = fc.loc[fc["ann_date"] >= cutoff]
    if fc.empty:
        return pd.DataFrame(columns=["ts_code", "fc_ann_date", "fc_end_date", "fc_type",
                                     "fc_growth_mid", "fc_positive"])
    fc["fc_growth_mid"] = (pd.to_numeric(fc["p_change_min"], errors="coerce") +
                           pd.to_numeric(fc["p_change_max"], errors="coerce")) / 2.0
    fc = fc.sort_values(["ts_code", "ann_date"]).drop_duplicates(subset=["ts_code"], keep="last")
    fc = fc.rename(columns={"ann_date": "fc_ann_date", "end_date": "fc_end_date", "type": "fc_type"})
    fc["fc_positive"] = (fc["fc_growth_mid"] > 0).astype(float)
    keep = ["ts_code", "fc_ann_date", "fc_end_date", "fc_type", "fc_growth_mid", "fc_positive"]
    return fc[keep].set_index("ts_code")


def moneyflow_snapshot(dataset: BigFishDataset, window: int = 20) -> pd.Series:
    """最近 window 日大单+超大单净流入合计（万元）。"""
    mf = dataset.moneyflow
    if mf is None or mf.empty:
        return pd.Series(dtype=float)
    mf = mf.copy()
    for col in ("buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount"):
        if col not in mf.columns:
            return pd.Series(dtype=float)
        mf[col] = pd.to_numeric(mf[col], errors="coerce")
    mf["_net"] = (mf["buy_lg_amount"] + mf["buy_elg_amount"]
                  - mf["sell_lg_amount"] - mf["sell_elg_amount"])
    dates = sorted(mf["trade_date"].astype(str).unique())[-window:]
    mf = mf.loc[mf["trade_date"].astype(str).isin(dates)]
    return mf.groupby("ts_code")["_net"].sum()


def price_matrices(dataset: BigFishDataset, lookback: int) -> dict[str, pd.DataFrame]:
    """价格矩阵缓存：同一 as-of 下 close/high/low/open/amount/pct_chg 只透视一次。

    回测里每个调仓日会被 market_regime / PCS / price_features / 事件研究各调用一次，
    重复 pivot 是最大的性能开销，这里做进程内缓存（挂在 dataset.cache 上，随对象释放）。
    """
    key = ("price_matrices", int(lookback))
    cached = dataset.cache.get(key)
    if cached is not None:
        return cached
    prices = dataset.prices
    if prices is None or prices.empty:
        dataset.cache[key] = {}
        return {}
    dates = sorted(prices["trade_date"].astype(str).unique())[-int(lookback):]
    sub = prices.loc[prices["trade_date"].astype(str).isin(dates)]
    mats: dict[str, pd.DataFrame] = {}
    for field in ("close", "high", "low", "open", "amount", "pct_chg"):
        if field in sub.columns:
            mats[field] = sub.pivot(index="ts_code", columns="trade_date", values=field).reindex(
                sorted(dates), axis=1
            )
    dataset.cache[key] = mats
    return mats


def price_features(dataset: BigFishDataset, lookback: int = 280) -> pd.DataFrame:
    """每只股票最新交易日的价格特征（收盘、均线、ATR14、波动、成交额）。"""
    mats = price_matrices(dataset, lookback)
    if not mats or "close" not in mats:
        return pd.DataFrame()
    close = mats["close"].astype(float)
    high = mats.get("high", close).astype(float)
    low = mats.get("low", close).astype(float)
    amount = mats.get("amount", pd.DataFrame(index=close.index)).astype(float)
    pct = mats.get("pct_chg", pd.DataFrame(index=close.index)).astype(float)

    prev_close = close.shift(1, axis=1)
    tr = pd.concat([(high - low).stack(), (high - prev_close).abs().stack(),
                    (low - prev_close).abs().stack()], axis=1).max(axis=1).unstack()
    atr14 = rolling_last(tr, 14)

    out = pd.DataFrame(index=close.index)
    out["last_date"] = close.columns[-1]
    out["close"] = close.iloc[:, -1]
    out["ma20"] = rolling_last(close, 20)
    out["ma60"] = rolling_last(close, 60)
    out["ma120"] = rolling_last(close, 120)
    out["atr14"] = atr14
    out["atr_pct"] = atr14 / out["close"]
    out["ret20"] = close.iloc[:, -1] / close.iloc[:, -21] - 1
    out["ret60"] = close.iloc[:, -1] / close.iloc[:, -61] - 1
    span = min(251, close.shape[1])
    out["ret250"] = close.iloc[:, -1] / close.iloc[:, -span] - 1
    out["amount20"] = amount.iloc[:, -20:].mean(axis=1)
    out["vol20"] = pct.iloc[:, -20:].std(axis=1)
    out["dist_ma20"] = out["close"] / out["ma20"]
    return out.reset_index()
