"""报告期工具：季度序列、单季度化（累计 → 单季）。"""
from __future__ import annotations

import pandas as pd

_QUARTER_MONTH = {1: 3, 2: 6, 3: 9, 4: 12}
_QUARTER_MONTH_REV = {3: 1, 6: 2, 9: 3, 12: 4}
_MONTH_DAY = {3: "31", 6: "30", 9: "30", 12: "31"}


def quarter_list(start_period: str, end_period: str) -> list[str]:
    """生成 [start, end] 之间的报告期列表，如 20230331..20260630。"""
    start_year, start_q = int(start_period[:4]), int(start_period[4:6])
    end_year, end_q = int(end_period[:4]), int(end_period[4:6])
    out: list[str] = []
    year, quarter = start_year, _QUARTER_MONTH_REV[start_q]
    while (year, quarter) <= (end_year, _QUARTER_MONTH_REV[end_q]):
        month = _QUARTER_MONTH[quarter]
        out.append(f"{year}{month:02d}{_MONTH_DAY[month]}")
        quarter += 1
        if quarter > 4:
            quarter = 1
            year += 1
    return out


def quarter_index(period: str) -> int:
    """把报告期映射成连续季度序号（用于滞后计算）。"""
    year, month = int(period[:4]), int(period[4:6])
    return year * 4 + {3: 0, 6: 1, 9: 2, 12: 3}[month]


def shift_period(period: str, quarters: int) -> str:
    idx = quarter_index(period) + quarters
    year, q = divmod(idx, 4)
    month = (q + 1) * 3
    day = {3: "31", 6: "30", 9: "30", 12: "31"}[month]
    return f"{year}{month:02d}{day}"


def single_quarterize(df: pd.DataFrame, value_cols: list[str], code_col: str = "ts_code",
                     period_col: str = "end_date") -> pd.DataFrame:
    """累计口径 → 单季度口径（利润表 / 现金流量表）。

    Q1 单季 = Q1 累计；Q2 单季 = Q2 累计 − Q1 累计；以此类推。
    缺少上一季累计时返回 NaN（不猜测、不填充）。
    """
    out = df.copy()
    out = out.sort_values([code_col, period_col])
    out["_qidx"] = out[period_col].map(quarter_index)
    prev = out[[code_col, "_qidx", period_col] + value_cols].copy()
    prev["_qidx"] = prev["_qidx"] + 1
    prev = prev.rename(columns={period_col: "_prev_period"})
    merged = out.merge(prev, on=[code_col, "_qidx"], how="left", suffixes=("", "_prev"))
    for col in value_cols:
        prev_col = f"{col}_prev"
        if prev_col not in merged.columns:
            continue
        is_q1 = merged[period_col].str[4:6] == "03"
        single = merged[col] - merged[prev_col]
        merged[col] = single.where(~is_q1, merged[col])
    drop_cols = [c for c in merged.columns if c.endswith("_prev")] + ["_prev_period"]
    return merged.drop(columns=[c for c in drop_cols + ["_qidx"] if c in merged.columns])
