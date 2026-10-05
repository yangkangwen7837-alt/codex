"""Phase 6 · 回测分析：分层收益、二维/三维矩阵、分位组合、S 级测试、False Positive Library。"""
from __future__ import annotations

import numpy as np
import pandas as pd

#: 双边成本 = 2 ×（佣金 3bp + 滑点 5bp）
ROUND_TRIP_COST = 0.0016


def _net(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce") - ROUND_TRIP_COST


def _summary(values: pd.Series) -> dict:
    v = pd.to_numeric(values, errors="coerce").dropna()
    if v.empty:
        return {"n": 0, "mean": np.nan, "median": np.nan, "win": np.nan, "pf": np.nan,
                "std": np.nan, "ir": np.nan, "p10": np.nan, "p90": np.nan}
    gains = v[v > 0].sum()
    losses = -v[v < 0].sum()
    return {
        "n": int(len(v)),
        "mean": float(v.mean()),
        "median": float(v.median()),
        "win": float((v > 0).mean()),
        "pf": float(gains / losses) if losses > 0 else np.inf,
        "std": float(v.std()),
        "ir": float(v.mean() / v.std() * np.sqrt(12)) if v.std() > 0 else np.nan,
        "p10": float(v.quantile(0.10)),
        "p90": float(v.quantile(0.90)),
    }


def _quantile_group(panel: pd.DataFrame, col: str, q: float) -> pd.Series:
    return panel.groupby("as_of")[col].transform(lambda s: s.rank(pct=True, na_option="keep")) >= q


def _bucket(series: pd.Series, bins: int, prefix: str) -> pd.Series:
    """按截面分位分箱；样本不足或取值重复时返回 NaN（不强行分箱）。"""
    labels = [f"{prefix} Q{i + 1}" for i in range(bins)]
    try:
        valid = series.dropna()
        if valid.nunique() < bins:
            return pd.Series(np.nan, index=series.index)
        return pd.qcut(series.rank(method="first"), bins, labels=labels, duplicates="drop")
    except (ValueError, TypeError):
        return pd.Series(np.nan, index=series.index)


def group_stats(panel: pd.DataFrame, signal_col: str, horizons: list[int],
                buckets: dict[str, tuple[float, float]] | None = None) -> pd.DataFrame:
    """规格书第 70 节：信号分组 → 未来 20/60/120/250 日收益。"""
    buckets = buckets or {
        "TOP 10%": (0.90, 1.01),
        "TOP 10-20%": (0.80, 0.90),
        "MIDDLE 20-80%": (0.20, 0.80),
        "BOTTOM 20%": (-0.01, 0.20),
    }
    rows = []
    rank = panel.groupby("as_of")[signal_col].transform(lambda s: s.rank(pct=True, na_option="keep"))
    for label, (low, high) in buckets.items():
        mask = (rank > low) & (rank <= high)
        sub = panel.loc[mask]
        for h in horizons:
            col = f"ret{h}"
            if col not in panel.columns:
                continue
            stats = _summary(_net(sub[col]))
            rows.append({"group": label, "horizon": h, **stats})
    return pd.DataFrame(rows)


def matrix_stats(panel: pd.DataFrame, row_col: str, col_col: str, horizon: int,
                 row_bins: int = 3, col_bins: int = 3) -> pd.DataFrame:
    """规格书第 71/72 节：二维（FRS×PCS）/ 三维（再加 ODS）矩阵。"""
    ret = f"ret{horizon}"
    if ret not in panel.columns:
        return pd.DataFrame()
    df = panel.copy()
    df["_row"] = df.groupby("as_of")[row_col].transform(
        lambda s: _bucket(s, row_bins, row_col)
    )
    df["_col"] = df.groupby("as_of")[col_col].transform(
        lambda s: _bucket(s, col_bins, col_col)
    )
    df = df.dropna(subset=["_row", "_col"])
    if df.empty:
        return pd.DataFrame()
    out = (df.groupby(["_row", "_col"], observed=True)[ret]
           .apply(lambda s: pd.Series(_summary(_net(s)))).unstack().reset_index())
    return out


def portfolio_stats(panel: pd.DataFrame, signal_col: str, ret_col: str, quantile: float,
                    top: bool = True) -> dict:
    """分位组合：每个调仓日等权持有，逐期复利，给出累计收益 / 最大回撤 / 胜率。"""
    df = panel.copy()
    rank = df.groupby("as_of")[signal_col].transform(lambda s: s.rank(pct=True, na_option="keep"))
    mask = rank >= quantile if top else rank <= quantile
    sub = df.loc[mask].copy()
    cohort = sub.groupby("as_of")[ret_col].apply(lambda s: _net(s).mean()).dropna()
    if cohort.empty:
        return {"cohorts": 0}
    curve = (1.0 + cohort).cumprod()
    peak = curve.cummax()
    drawdown = curve / peak - 1.0
    return {
        "cohorts": int(len(cohort)),
        "avg_cohort_return": float(cohort.mean()),
        "median_cohort_return": float(cohort.median()),
        "win_rate": float((cohort > 0).mean()),
        "total_return": float(curve.iloc[-1] - 1.0),
        "max_drawdown": float(drawdown.min()),
        "sharpe": float(cohort.mean() / cohort.std() * np.sqrt(12)) if cohort.std() > 0 else np.nan,
        "avg_holdings": float(sub.groupby("as_of").size().mean()),
        "_curve": curve,
        "_cohort": cohort,
    }


def false_positive_library(panel: pd.DataFrame, signal_col: str = "bfs",
                           horizon: int = 60, quantile: float = 0.8) -> pd.DataFrame:
    """规格书第 75 节：把失败案例归类，形成 False Positive Library。"""
    ret = f"ret{horizon}"
    if ret not in panel.columns:
        return pd.DataFrame()
    df = panel.copy()
    rank = df.groupby("as_of")[signal_col].transform(lambda s: s.rank(pct=True, na_option="keep"))
    top = df.loc[rank >= quantile].copy()
    fails = top.loc[_net(top[ret]) < 0].copy()
    if fails.empty:
        return fails

    def classify(row: pd.Series) -> str:
        if bool(row.get("is_fake_turnaround", False)):
            return "Fake Earnings（一次性收益/扣非背离）"
        if bool(row.get("is_value_trap", False)):
            return "Value Trap"
        if np.isfinite(row.get("f_one_off_ratio", np.nan)) and row.get("f_one_off_ratio", 0) > 0.4:
            return "One-off Profit"
        if np.isfinite(row.get("cfo_to_np", np.nan)) and row.get("cfo_to_np", 0) < 0.3:
            return "Cash Flow Warning"
        if np.isfinite(row.get("current_pe", np.nan)) and row.get("current_pe", 0) > 60:
            return "Valuation Too Expensive"
        if bool(row.get("note_low_base", False)):
            return "Low Base（低基数）"
        if np.isfinite(row.get("pcs", np.nan)) and row.get("pcs", 0) >= 65:
            return "Price Confirmation Failure（价格确认后仍下跌）"
        if np.isfinite(row.get("frs", np.nan)) and row.get("frs", 0) >= 70 and (
            not np.isfinite(row.get("pcs", np.nan)) or row.get("pcs", 0) < 50
        ):
            return "Valuation/Price Timing Failure（基本面好但市场不认）"
        return "Unclassified（需人工复盘）"

    fails["failure_type"] = fails.apply(classify, axis=1)
    fails["failure_ret"] = _net(fails[ret])
    return fails
