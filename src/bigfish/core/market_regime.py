"""LEVEL 1 · Market Regime（规格书第 5 节）。

本模块只输出市场状态与交易优先级，**不删除**任何基本面反转候选。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..pipeline.dataset import BigFishDataset
from ..utils import clip_score, rolling_last, scale
from .common import price_matrices


def _index_features(indices: pd.DataFrame, code: str, windows: list[int]) -> dict:
    sub = indices.loc[indices["ts_code"] == code].sort_values("trade_date")
    if sub.empty:
        return {}
    close = sub["close"].astype(float).reset_index(drop=True)
    out: dict[str, float] = {"last": float(close.iloc[-1])}
    for w in windows:
        out[f"ma{w}"] = float(close.rolling(w).mean().iloc[-1]) if len(close) >= w else np.nan
    for w in (20, 60):
        out[f"ret{w}"] = float(close.iloc[-1] / close.iloc[-1 - w] - 1) if len(close) > w else np.nan
    out["vol20"] = float(close.pct_change().tail(20).std() * np.sqrt(250)) if len(close) > 21 else np.nan
    return out


def compute_market_regime(dataset: BigFishDataset) -> dict:
    settings = dataset.settings
    windows = list(settings.path("regime.ma_windows", [20, 60]))
    high_low_window = int(settings.path("regime.high_low_window", 60))

    benchmark = str(settings.path("data.benchmark", "000300.SH"))
    bench = _index_features(dataset.indices, benchmark, windows)
    cyb = _index_features(dataset.indices, "399006.SZ", windows)
    zz1000 = _index_features(dataset.indices, "000852.SH", windows)

    components: dict[str, float] = {}
    reasons: list[str] = []

    # 1) 基准指数趋势（40%）
    if bench:
        score = 0.0
        if np.isfinite(bench.get("ma20", np.nan)) and bench["last"] > bench["ma20"]:
            score += 50
            reasons.append("沪深300 位于 MA20 上方")
        if np.isfinite(bench.get("ma60", np.nan)) and bench["last"] > bench["ma60"]:
            score += 30
            reasons.append("沪深300 位于 MA60 上方")
        ret60 = bench.get("ret60", np.nan)
        if np.isfinite(ret60):
            score += float(np.clip(ret60 * 100, -20, 20) / 20 * 20) if ret60 >= 0 else float(
                np.clip(ret60 * 100, -20, 0) / 20 * 20
            )
        components["index_trend"] = clip_score(score)

    # 2) 市场宽度（30%）：上涨家数占比 + 均线上方占比 + 新高占比
    prices = dataset.prices
    mats = price_matrices(dataset, max(high_low_window + 20, 260))
    if not prices.empty and "close" in mats:
        pivot = mats["close"].reindex(sorted(mats["close"].columns), axis=1)
        last = pivot.columns[-1]
        recent = pivot.iloc[:, -20:]
        ma20 = rolling_last(pivot, 20)
        above = float((pivot[last] > ma20).mean() * 100)
        up_ratio = float((recent[last] > recent.iloc[:, 0]).mean() * 100)
        rolling_max = rolling_last(pivot, high_low_window, how="max")
        new_high_ratio = float((pivot[last] >= rolling_max * 0.999).mean() * 100)
        breadth = 0.5 * above + 0.3 * up_ratio + 0.2 * scale(new_high_ratio, 0, 15)
        components["breadth"] = clip_score(breadth)
        reasons.append(f"MA20 上方个股 {above:.1f}%，20 日上涨占比 {up_ratio:.1f}%")

        # 3) 成交额（15%）
        amount = mats.get("amount")
        if amount is None:
            amount = pd.DataFrame(index=pivot.index)
        else:
            amount = amount.reindex(sorted(amount.columns), axis=1)
        total_amount = amount.sum(axis=0)
        if len(total_amount) > 25:
            ratio = float(total_amount.tail(5).mean() / total_amount.tail(20).mean())
            components["volume"] = clip_score(scale(ratio, 0.7, 1.4))
            reasons.append(f"近 5 日成交额 / 20 日均值 = {ratio:.2f}")

        # 4) 跌停/强势结构（15%）：当日涨跌停家数比（用近似阈值）
        pct = mats.get("pct_chg")
        last_ret = pct[last].dropna() if pct is not None and last in pct.columns else pd.Series(dtype=float)
        limit_up = float((last_ret >= 9.8).mean() * 100)
        limit_down = float((last_ret <= -9.8).mean() * 100)
        components["limit_structure"] = clip_score(scale(limit_up - limit_down, -1.0, 2.0))

    # 风格：小盘 vs 大盘
    if cyb and zz1000 and bench:
        cyb_rs = cyb.get("ret20", np.nan) - bench.get("ret20", np.nan)
        if np.isfinite(cyb_rs):
            components["style"] = clip_score(scale(cyb_rs, -0.08, 0.08))
            reasons.append(f"创业板 20 日相对沪深300 {cyb_rs * 100:+.1f}%")

    weights = {"index_trend": 0.40, "breadth": 0.30, "volume": 0.15, "limit_structure": 0.15}
    usable = {k: v for k, v in components.items() if k in weights and np.isfinite(v)}
    if usable:
        w_sum = sum(weights[k] for k in usable)
        regime_score = sum(usable[k] * weights[k] for k in usable) / w_sum
    else:
        regime_score = float("nan")

    risk_on = float(settings.path("regime.risk_on_threshold", 60))
    risk_off = float(settings.path("regime.risk_off_threshold", 35))
    if not np.isfinite(regime_score):
        label = "Neutral"
    elif regime_score >= risk_on:
        label = "Risk On"
    elif regime_score <= risk_off:
        label = "Risk Off"
    else:
        label = "Neutral"

    priority = (settings.path("regime.action_priority", {}) or {}).get(label, 0.85)
    return {
        "trade_date": dataset.as_of,
        "regime": label,
        "regime_score": float(regime_score) if np.isfinite(regime_score) else None,
        "components": components,
        "action_priority": float(priority),
        "benchmark": benchmark,
        "bench_features": bench,
        "reasons": reasons,
        "note": "Market Regime 只影响交易优先级，不删除任何基本面反转候选（规格书第 5 节）",
    }
