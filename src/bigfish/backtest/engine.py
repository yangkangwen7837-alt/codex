"""Phase 6 · 回测引擎：按调仓日重算信号 + 前视收益 + 数据卫生过滤。"""
from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd

from .. import PROCESSED_DIR
from ..config import Settings, load_settings
from ..pipeline.history import HistoryData
from ..pipeline.run import score_dataset

log = logging.getLogger(__name__)

#: 回测面板保留的信号字段（其余评分细节留在 data/processed/bigfish_score_<date>.parquet）
KEEP_COLUMNS = [
    "as_of", "ts_code", "name", "industry", "industry_code",
    "frs", "lis", "pcs", "ods", "rps", "catalyst", "bfs",
    "frs_revenue", "frs_profit", "frs_margin", "frs_cashflow", "frs_quality", "frs_special",
    "pcs_price_structure", "pcs_moving_average", "pcs_relative_strength", "pcs_volume",
    "pcs_smart_money", "pcs_news_asymmetry",
    "lis_orders_demand", "lis_product_price", "lis_inventory_cycle", "lis_contract_liability",
    "grade", "stage", "action", "quadrant", "reversal_type",
    "kill_count", "kill_level", "kill_fundamental", "kill_technical", "kill_thesis",
    "is_value_trap", "is_fake_turnaround", "note_low_base", "risk_block", "tradeable",
    "expensive_entry", "severe_expensive", "low_base_penalty",
    "frs_coverage", "lis_coverage", "pcs_coverage", "data_confidence", "data_flag",
    "close", "amount20", "atr14", "atr_pct", "trail_ret20", "trail_ret60", "p_dist_ma20",
    "f_revenue_yoy", "f_np_yoy", "f_gross_margin", "f_net_margin", "f_inventory_to_revenue",
    "f_receivable_yoy", "f_contract_liab_yoy", "f_debt_to_assets", "f_one_off_ratio",
    "f_low_base_ratio", "f_ttm_np", "f_ttm_revenue",
    "current_pe", "industry_pe", "upside_base", "upside_bull", "rps_basis",
    "cfo_to_np", "latest_period", "latest_ann_date", "one_off_ratio", "dedt_gap_ratio",
]


def rebalance_dates(daily_dates: list[str], start: str, end: str, freq: str = "M") -> list[str]:
    """调仓日 = 每期最后一个交易日（信号在收盘后生成，次日开盘入场）。"""
    dates = [d for d in daily_dates if start <= d <= end]
    if not dates:
        return []
    frame = pd.DataFrame({"date": dates})
    if freq == "M":
        frame["bucket"] = frame["date"].str[:6]
    elif freq == "Q":
        frame["bucket"] = frame["date"].str[:4] + "Q" + ((frame["date"].str[4:6].astype(int) - 1) // 3 + 1).astype(str)
    elif freq == "W":
        frame["bucket"] = pd.to_datetime(frame["date"]).dt.strftime("%G%V")
    else:
        frame["bucket"] = frame["date"]
    return frame.groupby("bucket")["date"].max().tolist()


def build_panel(settings: Settings | None = None, start: str | None = None, end: str | None = None,
                freq: str = "M", horizons: tuple[int, ...] = (20, 60, 120, 250),
                limit: int | None = None, progress=None) -> tuple[pd.DataFrame, dict]:
    """生成回测面板：每个调仓日 × 每只股票 × 评分 + 前视收益。"""
    settings = settings or load_settings()
    start = start or str(settings.path("backtest.start", "20190101"))
    end = end or str(settings.path("backtest.end", settings.path("data.end_date")))

    t0 = time.time()
    history = HistoryData(settings)
    dates = rebalance_dates(history.daily_dates, start, end, freq)
    if limit:
        dates = dates[:limit]
    if progress:
        progress(f"调仓日 {len(dates)} 个（{dates[0]} ~ {dates[-1]}），行情分片 {len(history.daily_dates)} 个")

    rows = []
    for i, as_of in enumerate(dates, start=1):
        dataset = history.dataset_at(as_of)
        scored = score_dataset(dataset, narratives=False)
        full = scored["full"]
        full["as_of"] = as_of
        # 价格特征里的 ret20/ret60 是"过去收益"，与前视收益 ret{h} 区分开，避免列名冲突
        full = full.rename(columns={"ret20": "trail_ret20", "ret60": "trail_ret60"})
        keep = [c for c in KEEP_COLUMNS if c in full.columns]
        rows.append(full[keep].copy())
        if progress:
            progress(f"[{i}/{len(dates)}] {as_of} 信号 {len(full)} 只，"
                     f"市场 {scored['regime']['regime']}，用时 {time.time() - t0:.0f}s")
    panel = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if panel.empty:
        return panel, {"rebalances": 0}

    if progress:
        progress("计算前视收益（次日开盘入场 / T+h 收盘出场）")
    fwd = history.forward_returns(panel[["as_of", "ts_code"]], list(horizons))
    panel = panel.merge(fwd, on=["as_of", "ts_code"], how="left")

    raw_rows = len(panel)
    panel = apply_hygiene(panel)
    stats = {
        "rebalances": len(dates),
        "start": dates[0],
        "end": dates[-1],
        "rows_raw": int(raw_rows),
        "rows_clean": int(len(panel)),
        "dropped": int(raw_rows - len(panel)),
        "elapsed_seconds": round(time.time() - t0, 1),
        "data": {
            "daily_shards": len(history.daily_dates),
            "daily_reads": history.daily_cache.reads,
            "basic_reads": history.basic_cache.reads,
            "moneyflow_reads": history.moneyflow_cache.reads,
            "fundamental_rows": int(len(history.fundamentals)),
        },
    }
    return panel, stats


def apply_hygiene(panel: pd.DataFrame) -> pd.DataFrame:
    """数据卫生与可交易性过滤（逐条记录剔除原因，便于如实汇报）。"""
    df = panel.copy()
    reason = pd.Series("", index=df.index, dtype=object)

    def flag(mask, label: str) -> None:
        mask = pd.Series(mask, index=df.index).fillna(False).astype(bool)
        reason.loc[mask & (reason == "")] = label

    nan = pd.Series(np.nan, index=df.index)
    flag(~df["tradeable"].fillna(False).astype(bool), "无名称/无行业/当日未交易")
    flag(df.get("trail_ret60", nan).isna(), "上市不足 60 个交易日")
    flag(df.get("trail_ret20", nan).isna(), "缺少 20 日价格")
    flag(df.get("entry_gap", nan) > 0.095, "次日开盘接近涨停（买不到）")
    flag(df.get("max_abs_move", nan) > 0.30, "持有窗口内 >30% 单日异动（数据异常/退市整理）")
    expected = df.get("expected_days", nan)
    obs = df.get("obs_days", nan)
    flag((obs < 0.8 * expected).fillna(False), "持有窗口内长期停牌/退市")
    flag(df.get("risk_block", pd.Series(False, index=df.index)).fillna(False).astype(bool), "RISK_BLOCK")

    keep = reason == ""
    out = df.loc[keep].reset_index(drop=True)
    out.attrs["dropped_reasons"] = reason.loc[~keep].value_counts().to_dict()
    return out


def save_panel(panel: pd.DataFrame, stats: dict, name: str = "backtest_panel") -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(PROCESSED_DIR / f"{name}.parquet", index=False)
    import json

    (PROCESSED_DIR / f"{name}_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
