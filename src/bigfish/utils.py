"""通用工具：安全运算、分位、缺失值加权、评分映射。"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np
import pandas as pd


def safe_div(a, b, default=np.nan) -> pd.Series:
    """逐元素安全除法：分母为 0 / 缺失 / 无穷时返回 default。"""
    a = a if isinstance(a, pd.Series) else pd.Series(a)
    b = b if isinstance(b, pd.Series) else pd.Series(b)
    out = a / b.replace(0, np.nan)
    out = out.replace([np.inf, -np.inf], np.nan)
    return out if default is np.nan else out.fillna(default)


def pct_change(new, old) -> pd.Series:
    """(new - old) / |old|；old 为 0 时返回 NaN（不做主观填充）。"""
    new = new if isinstance(new, pd.Series) else pd.Series(new)
    old = old if isinstance(old, pd.Series) else pd.Series(old)
    denom = old.abs().replace(0, np.nan)
    out = (new - old) / denom
    return out.replace([np.inf, -np.inf], np.nan)


def nan_weighted_mean(values: dict[str, float], weights: dict[str, float]) -> tuple[float, float]:
    """按可用分项重新归一化权重。返回 (得分, 覆盖率)。"""
    total_w = sum(weights.values())
    if total_w <= 0:
        return float("nan"), 0.0
    usable: dict[str, float] = {}
    for key, weight in weights.items():
        if key in values and values[key] is not None and np.isfinite(float(values[key])):
            usable[key] = float(weight)
    if not usable:
        return float("nan"), 0.0
    weight_sum = sum(usable.values())
    score = sum(values[k] * w for k, w in usable.items()) / weight_sum
    return float(score), float(weight_sum / total_w)


def points_to_score(points: float, max_points: float) -> float:
    """加分制 → 0~100 分（规格书第 8~12 节以加分项定义，这里换算成百分制）。"""
    if max_points <= 0 or points is None or not np.isfinite(points):
        return float("nan")
    return float(np.clip(points / max_points * 100.0, 0.0, 100.0))


def scale(value, low: float, high: float):
    """线性映射到 0~100 并截断。支持标量与 Series。"""
    if value is None or high == low:
        return float("nan")
    if isinstance(value, pd.Series):
        out = (value.astype(float) - low) / (high - low) * 100.0
        return out.clip(0.0, 100.0)
    if not np.isfinite(value):
        return float("nan")
    return float(np.clip((value - low) / (high - low) * 100.0, 0.0, 100.0))


def cross_section_percentile(series: pd.Series, ascending: bool = True) -> pd.Series:
    """截面分位（0~100），ascending=True 表示值越大分位越高。"""
    return series.rank(pct=True, ascending=ascending, na_option="keep") * 100.0


def consecutive_run(flags: Sequence[bool]) -> int:
    """从末尾往前数连续 True 的个数。"""
    run = 0
    for flag in reversed(list(flags)):
        if bool(flag):
            run += 1
        else:
            break
    return run


def last_valid(series: pd.Series, n: int = 1) -> float:
    s = series.dropna()
    if len(s) < n:
        return float("nan")
    return float(s.iloc[-n])


def data_flag(coverage: float, confidence: float, min_coverage: float = 0.5) -> str:
    if not np.isfinite(coverage) or not np.isfinite(confidence):
        return "DATA INCOMPLETE"
    if coverage < min_coverage or confidence < 0.5:
        return "DATA INCOMPLETE"
    if coverage < 0.85 or confidence < 0.8:
        return "DATA PARTIAL"
    return "OK"


def join_list(items: Iterable[str]) -> str:
    return " | ".join(str(i) for i in items if i)


def to_num(value, default=np.nan) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if np.isfinite(out) else default


def clip_score(value: float) -> float:
    if value is None or not np.isfinite(value):
        return float("nan")
    return float(np.clip(value, 0.0, 100.0))


def rolling_last(df: pd.DataFrame, window: int, how: str = "mean", offset: int = 0) -> pd.Series:
    """按时间方向（列）滚动，取倒数第 (1+offset) 个位置。

    pandas 3.0 已移除 DataFrame.rolling(axis=1)，统一用转置后滚动实现。
    df: index=标的, columns=按时间升序排列的交易日
    """
    rolled = getattr(df.T.rolling(window, min_periods=max(2, window // 2)), how)()
    return rolled.iloc[-1 - offset]


def weighted_available(pairs: list[tuple], index=None) -> pd.Series:
    """按可用分项加权平均（缺一项不影响另一项），用于子分项内部合成。

    pairs: [(Series|None, weight), ...]
    """
    if index is None:
        for series, _ in pairs:
            if isinstance(series, pd.Series):
                index = series.index
                break
    num = pd.Series(0.0, index=index)
    den = pd.Series(0.0, index=index)
    for series, weight in pairs:
        if series is None or not isinstance(series, pd.Series) or weight <= 0:
            continue
        mask = series.notna()
        num = num.add((series * weight).where(mask, 0.0), fill_value=0.0)
        den = den.add(pd.Series(np.where(mask, weight, 0.0), index=series.index), fill_value=0.0)
    out = num / den.replace(0, np.nan)
    return out.reindex(index) if index is not None else out


def weighted_frame(frame: pd.DataFrame, weights: dict[str, float]) -> tuple[pd.Series, pd.Series]:
    """整表加权合成（等价于逐行 nan_weighted_mean，但快上百倍）。

    返回 (score, coverage)；覆盖率 = 可用权重 / 全部权重。
    """
    cols = [c for c in weights if c in frame.columns]
    total = sum(weights.values())
    if not cols or total <= 0:
        idx = frame.index
        return pd.Series(np.nan, index=idx), pd.Series(0.0, index=idx)
    weight = pd.Series({c: float(weights[c]) for c in cols})
    values = frame[cols].astype(float)
    mask = values.notna()
    num = values.fillna(0.0).mul(weight, axis=1).sum(axis=1)
    den = mask.mul(weight, axis=1).sum(axis=1)
    score = (num / den.replace(0, np.nan)).clip(0, 100)
    coverage = den / total
    return score, coverage
