"""LEVEL 5 · Price Confirmation Score（规格书第 19~26 节）。

回答一个问题：**市场是否已经开始相信基本面反转？**
价格结构 25 + 均线结构 15 + 相对强弱 20 + 量能 15 + 聪明钱 15 + 消息反应不对称 10。
"""
from __future__ import annotations

from bisect import bisect_left

import numpy as np
import pandas as pd

from ..pipeline.dataset import BigFishDataset
from ..utils import clip_score, data_flag, rolling_last, scale, weighted_frame
from .common import cross_section_rank, moneyflow_snapshot, price_matrices, stock_industry_map


def _price_matrices(dataset: BigFishDataset, lookback: int):
    return price_matrices(dataset, lookback)


def compute_pcs(dataset: BigFishDataset) -> pd.DataFrame:
    settings = dataset.settings
    weights = dict(settings.path("pcs.weights", {}))
    lookback = int(settings.path("pcs.lookback.structure", 250)) + 30
    mats = _price_matrices(dataset, lookback)
    if not mats or "close" not in mats:
        return pd.DataFrame()

    close = mats["close"].astype(float)
    high = mats.get("high", close).astype(float)
    amount = mats.get("amount", pd.DataFrame(index=close.index)).astype(float)
    n_days = close.shape[1]

    components: dict[str, pd.Series] = {}
    detail: dict[str, pd.Series] = {}

    # ---------------- 20. 价格结构（25） ----------------
    window = int(settings.path("pcs.lookback.structure", 250))
    recent_low = close.iloc[:, -20:].min(axis=1)
    long_low = close.iloc[:, -min(window, n_days):].min(axis=1)
    not_new_low = recent_low > long_low * 1.001
    detail["not_new_low"] = not_new_low.astype(float)

    mid = n_days // 2
    second_half_low = close.iloc[:, -60:].min(axis=1) if n_days >= 60 else pd.Series(np.nan, index=close.index)
    first_half_low = close.iloc[:, -120:-60].min(axis=1) if n_days >= 120 else pd.Series(np.nan, index=close.index)
    higher_low = second_half_low > first_half_low * 1.001
    detail["higher_low"] = higher_low.astype(float)

    base_window = int(settings.path("pcs.lookback.base", 60))
    if n_days >= base_window:
        base_high = close.iloc[:, -base_window:].max(axis=1)
        base_low = close.iloc[:, -base_window:].min(axis=1)
        base_width = (base_high - base_low) / close.iloc[:, -base_window:].mean(axis=1)
        long_base = base_width < 0.35
    else:
        long_base = pd.Series(np.nan, index=close.index)
    detail["long_base"] = long_base.astype(float)

    if n_days > base_window:
        box_high = high.iloc[:, -base_window - 1:-1].max(axis=1)
        breakout = close.iloc[:, -1] > box_high
        near_breakout = close.iloc[:, -1] > box_high * 0.97
    else:
        breakout = pd.Series(np.nan, index=close.index)
        near_breakout = pd.Series(np.nan, index=close.index)

    # ---- 突破确认 / 突破失败（回测暴露：「价格确认后仍下跌」平均亏 12.3%）----
    bcfg = settings.path("pcs.breakout", {}) or {}
    breakout_lookback = int(bcfg.get("lookback", 20))
    hold_tolerance = float(bcfg.get("hold_tolerance", 0.98))
    confirmed_breakout = pd.Series(np.nan, index=close.index)
    failed_breakout = pd.Series(False, index=close.index)
    if n_days > base_window + breakout_lookback:
        box_hist = high.T.rolling(base_window, min_periods=base_window // 2).max().T.shift(1, axis=1)
        recent = high.iloc[:, -breakout_lookback:] > box_hist.iloc[:, -breakout_lookback:]
        level = box_hist.iloc[:, -breakout_lookback:].where(recent)
        level_last = level.ffill(axis=1).iloc[:, -1]
        has_break = level_last.notna()
        last_close = close.iloc[:, -1]
        confirmed_breakout = (has_break & (last_close >= level_last * hold_tolerance)).astype(float)
        failed_breakout = has_break & (last_close < level_last * hold_tolerance)
    elif n_days > base_window:
        confirmed_breakout = breakout.astype(float)
    detail["box_breakout"] = breakout.astype(float)
    detail["breakout_confirmed"] = confirmed_breakout
    detail["breakout_failed"] = failed_breakout.astype(float)

    structure = (not_new_low.fillna(False).astype(float) * 6
                 + higher_low.fillna(False).astype(float) * 6
                 + long_base.fillna(False).astype(float) * 6
                 + breakout.fillna(False).astype(float) * 7
                 + (confirmed_breakout.fillna(0).astype(float) * 5)
                 - (failed_breakout.fillna(False).astype(float) * 5))
    structure = structure.clip(lower=0)
    structure_avail = (not_new_low.notna().astype(float) * 6 + higher_low.notna().astype(float) * 6
                       + long_base.notna().astype(float) * 6 + breakout.notna().astype(float) * 12)
    components["price_structure"] = np.where(structure_avail > 0, structure / structure_avail * 100, np.nan)
    detail["near_breakout"] = near_breakout.astype(float)

    # ---------------- 21. 均线结构（15） ----------------
    ma20 = rolling_last(close, 20)
    ma60 = rolling_last(close, 60)
    ma120 = rolling_last(close, 120) if n_days >= 121 else pd.Series(np.nan, index=close.index)
    last = close.iloc[:, -1]
    ma60_prev = rolling_last(close, 60, offset=20) if n_days >= 81 else pd.Series(np.nan, index=close.index)
    ma_score = ((last > ma20).fillna(False).astype(float) * 5
                + (ma20 > ma60).fillna(False).astype(float) * 5
                + ((last > ma120) & (ma60 > ma60_prev)).fillna(False).astype(float) * 5)
    ma_avail = ((last > ma20).notna().astype(float) * 5 + (ma20 > ma60).notna().astype(float) * 5
                + ((last > ma120) & (ma60 > ma60_prev)).notna().astype(float) * 5)
    components["moving_average"] = np.where(ma_avail > 0, ma_score / ma_avail * 100, np.nan)
    detail["dist_ma20"] = last / ma20

    # ---------------- 22. 相对强弱（20） ----------------
    rs_window_short = int(settings.path("pcs.lookback.rs", 60))
    ind = dataset.industry_daily
    industry_returns: dict[str, dict[str, float]] = {}
    if ind is not None and not ind.empty:
        for code, sub in ind.groupby("ts_code"):
            sub = sub.sort_values("trade_date")
            c = sub["close"].astype(float).reset_index(drop=True)
            if len(c) < 61:
                continue
            industry_returns[code] = {
                "ret20": float(c.iloc[-1] / c.iloc[-21] - 1),
                "ret60": float(c.iloc[-1] / c.iloc[-61] - 1),
            }
    industry_map = stock_industry_map(dataset).drop_duplicates("ts_code").set_index("ts_code")
    ind_ret20 = industry_map["industry_code"].map({k: v["ret20"] for k, v in industry_returns.items()})
    ind_ret60 = industry_map["industry_code"].map({k: v["ret60"] for k, v in industry_returns.items()})

    stock_ret20 = (close.iloc[:, -1] / close.iloc[:, -21] - 1) if n_days > 21 else pd.Series(np.nan, index=close.index)
    stock_ret60 = (close.iloc[:, -1] / close.iloc[:, -61] - 1) if n_days > 61 else pd.Series(np.nan, index=close.index)
    bench = dataset.indices.loc[dataset.indices["ts_code"] == "000300.SH"].sort_values("trade_date")
    bench_ret60 = np.nan
    if not bench.empty and len(bench) > 61:
        bc = bench["close"].astype(float).reset_index(drop=True)
        bench_ret60 = float(bc.iloc[-1] / bc.iloc[-61] - 1)

    rs20 = stock_ret20 - ind_ret20.reindex(close.index)
    rs60 = stock_ret60 - ind_ret60.reindex(close.index)
    rs_bench = stock_ret60 - bench_ret60
    rs_parts, rs_w = [], []
    for series, w in ((rs20, 0.4), (rs60, 0.35), (rs_bench, 0.25)):
        rs_parts.append(cross_section_rank(series) * w)
        rs_w.append(series.notna().astype(float) * w)
    rs_sum = sum(rs_parts)
    rs_den = sum(rs_w).replace(0, np.nan)
    components["relative_strength"] = (rs_sum / rs_den).clip(0, 100)
    detail["rs20_vs_industry"] = rs20
    detail["rs60_vs_industry"] = rs60

    # ---------------- 23. 量能确认（15） ----------------
    if not amount.empty and n_days > 120:
        vol20 = amount.iloc[:, -20:].mean(axis=1)
        vol120 = amount.iloc[:, -120:].mean(axis=1)
        ratio = vol20 / vol120.replace(0, np.nan)
        target = float(settings.path("pcs.volume_ratio_target", 1.4))
        vol_score = (100 - (ratio - target).abs() / target * 100).clip(0, 100)
        last_amount = amount.iloc[:, -1]
        breakout_bonus = np.where(
            breakout.fillna(False) & (last_amount > vol120 * 1.5), 20, 0
        )
        components["volume"] = (vol_score * 0.8 + pd.Series(breakout_bonus, index=ratio.index) * 0.2).clip(0, 100)
        detail["volume_ratio_20_120"] = ratio

    # ---------------- 24. 聪明钱（15） ----------------
    money = moneyflow_snapshot(dataset, window=20)
    if not money.empty:
        money = money.reindex(close.index)
        components["smart_money"] = cross_section_rank(money)
        detail["net_large_inflow_20d"] = money

    # ---------------- 25. 消息反应不对称（10） ----------------
    asym = _reaction_asymmetry(dataset, close)
    if not asym.empty:
        components["news_asymmetry"] = asym.reindex(close.index)
        detail["reaction_good"] = asym.reindex(close.index)

    # ---------------- 汇总 ----------------
    frame = pd.DataFrame({"ts_code": close.index})
    scores, coverages = [], []
    comp_frame = pd.DataFrame({k: pd.Series(v, index=close.index) for k, v in components.items()})
    score, coverage = weighted_frame(comp_frame, weights)
    frame["pcs"] = score.to_numpy()
    frame["pcs_coverage"] = coverage.to_numpy()
    min_cov = float(settings.path("pcs.min_coverage", 0.5))
    frame.loc[frame["pcs_coverage"] < min_cov, "pcs"] = np.nan
    for key in components:
        frame[f"pcs_{key}"] = pd.Series(components[key], index=close.index).values
    for key, series in detail.items():
        frame[f"p_{key}"] = pd.Series(series, index=close.index).values
    frame["pcs_flag"] = [data_flag(c, 0.85, min_cov) for c in frame["pcs_coverage"]]
    frame["pcs_stage"] = pd.cut(
        frame["pcs"], bins=[-1, 60, 70, 80, 101],
        labels=["NO PRICE CONFIRMATION", "EARLY CONFIRMATION", "CONFIRMED", "STRONG CONFIRMATION"],
    ).astype(str)
    frame.loc[frame["pcs"].isna(), "pcs_stage"] = "DATA INCOMPLETE"
    return frame


def _reaction_asymmetry(dataset: BigFishDataset, close_matrix: pd.DataFrame,
                        window_days: int = 400) -> pd.Series:
    """消息反应不对称（规格书第 25 节）。

    事件 = 最近一次财报公告日（全市场都有），以及业绩预告公告日。
    好消息的定义：财报净利同比 > 0 或预告增速 > 0。指标 = 事件后 3 日累计超额收益（相对沪深300）。
    """
    if close_matrix is None or close_matrix.empty or dataset.fundamentals is None or dataset.fundamentals.empty:
        return pd.Series(dtype=float)
    dates = [str(c) for c in sorted(close_matrix.columns)]
    date_pos = {d: i for i, d in enumerate(dates)}

    bench = dataset.indices.loc[dataset.indices["ts_code"] == "000300.SH"].sort_values("trade_date")
    bench_close = dict(zip(bench["trade_date"].astype(str), bench["close"].astype(float))) if not bench.empty else {}

    latest = dataset.fundamentals.sort_values(["ts_code", "end_date"]).drop_duplicates("ts_code", keep="last")
    events = latest[["ts_code", "ann_date", "np_yoy"]].copy()
    events["good"] = events["np_yoy"] > 0
    fc = dataset.forecast
    if fc is not None and not fc.empty:
        fc = fc.copy()
        mid = (pd.to_numeric(fc["p_change_min"], errors="coerce") + pd.to_numeric(fc["p_change_max"], errors="coerce")) / 2
        fc = fc.assign(good=mid > 0, np_yoy=mid)[["ts_code", "ann_date", "np_yoy", "good"]]
        events = pd.concat([events, fc], ignore_index=True)

    cutoff = (pd.to_datetime(dataset.as_of) - pd.Timedelta(days=window_days)).strftime("%Y%m%d")
    events = events.loc[events["ann_date"].astype(str) >= cutoff]
    if events.empty:
        return pd.Series(dtype=float)

    close_wide = close_matrix.reindex(dates, axis=1)
    col_pos = {d: i for i, d in enumerate(close_wide.columns)}
    row_pos = {c: i for i, c in enumerate(close_wide.index)}
    values = close_wide.to_numpy(dtype=float)
    good_scores: dict[str, list[float]] = {}
    bad_scores: dict[str, list[float]] = {}
    for row in events.itertuples(index=False):
        ann = str(row.ann_date)
        pos = bisect_left(dates, ann)
        if pos >= len(dates) or pos + 3 >= len(dates) or row.ts_code not in row_pos:
            continue
        entry_date, exit_date = dates[pos], dates[pos + 3]
        ci = row_pos[row.ts_code]
        if entry_date not in col_pos or exit_date not in col_pos:
            continue
        entry = values[ci, col_pos[entry_date]]
        exit_ = values[ci, col_pos[exit_date]]
        if not (np.isfinite(entry) and np.isfinite(exit_) and entry > 0):
            continue
        stock_ret = exit_ / entry - 1
        bench_ret = 0.0
        if bench_close.get(entry_date) and bench_close.get(exit_date):
            bench_ret = bench_close[exit_date] / bench_close[entry_date] - 1
        abn = stock_ret - bench_ret
        (good_scores if bool(row.good) else bad_scores).setdefault(row.ts_code, []).append(abn)

    codes = sorted(set(good_scores) | set(bad_scores))
    out = {}
    for code in codes:
        parts = []
        if code in good_scores:
            parts.append(scale(float(np.mean(good_scores[code])), -0.02, 0.06))
        if code in bad_scores:
            parts.append(scale(float(-np.mean(bad_scores[code])), -0.02, 0.06))
        parts = [p for p in parts if np.isfinite(p)]
        if parts:
            out[code] = float(np.mean(parts))
    return pd.Series(out, dtype=float)
