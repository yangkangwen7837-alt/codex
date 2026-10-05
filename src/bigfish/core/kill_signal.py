"""Kill Signal System（规格书第 44~48 节）：独立风险通道，不被 BFS 高分掩盖。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..pipeline.dataset import BigFishDataset
from .common import latest_snapshot, tail_wide


def compute_kill(dataset: BigFishDataset, pcs: pd.DataFrame | None = None,
                 ods_stock: pd.DataFrame | None = None) -> pd.DataFrame:
    settings = dataset.settings
    panel = dataset.fundamentals
    if panel is None or panel.empty:
        return pd.DataFrame()

    wide = tail_wide(panel, ["revenue_yoy", "np_yoy", "gross_margin", "gross_margin_4q_avg",
                             "inventory_yoy", "inventory_to_revenue_chg", "receivable_yoy",
                             "cfo_to_np", "revenue_accel"], n=5)
    index = wide.index

    def col(name: str, t: int = 0) -> pd.Series:
        key = f"{name}_t{t}"
        return wide[key] if key in wide.columns else pd.Series(np.nan, index=index)

    fundamental_flags: dict[str, pd.Series] = {
        # 只有"增速连续下滑 且 已转负"才算基本面 Kill（否则在弱市中几乎全市场触发）
        "收入加速度连续下降且同比转负": (col("revenue_accel") < 0) & (col("revenue_accel", 1) < 0) & (col("revenue_yoy") < 0),
        "利润同比深度转负": col("np_yoy") < -0.3,
        "毛利率连续两季恶化且低于 4 季均值": (col("gross_margin") < col("gross_margin", 1))
        & (col("gross_margin", 1) < col("gross_margin", 2))
        & (col("gross_margin") < col("gross_margin_4q_avg")),
        "库存快速增加且库存占收入比上升": (col("inventory_yoy") > float(settings.path("kill.fundamental.inventory_surge_yoy", 0.5)))
        & (col("inventory_to_revenue_chg") > 0),
        "应收异常增加且快于收入": (col("receivable_yoy") > float(settings.path("kill.fundamental.receivable_surge_yoy", 0.5)))
        & (col("receivable_yoy") > col("revenue_yoy")),
        "利润改善但经营现金流恶化": (col("cfo_to_np") < 0) & (col("np_yoy") > 0),
    }
    fundamental_count = sum(flag.fillna(False).astype(int) for flag in fundamental_flags.values())

    # 技术 Kill
    technical_flags: dict[str, pd.Series] = {}
    if pcs is not None and not pcs.empty:
        p = pcs.set_index("ts_code")
        dist = p.get("p_dist_ma20")
        # 跌破 MA60：用 PCS 的均线结构分代理（低于 20 分说明价格结构已破坏）
        if "pcs_moving_average" in p.columns:
            technical_flags["均线结构破坏（无一条均线多头且价格低于 60 日线）"] = (
                (p["pcs_moving_average"] <= 0) & (p.get("p_dist_ma20", 1) < 0.93)
            )
        if "p_rs60_vs_industry" in p.columns:
            technical_flags["相对强弱明显转弱"] = p["p_rs60_vs_industry"] < float(
                settings.path("kill.technical.rs_weakening", -0.15)
            )
        if "pcs_price_structure" in p.columns:
            technical_flags["关键结构低点失守"] = p["pcs_price_structure"] <= 6
        if "p_breakout_failed" in p.columns and bool(settings.path("kill.technical.breakout_failure", True)):
            technical_flags["突破失败（跌回突破位 2% 以下）"] = p["p_breakout_failed"] > 0
    technical_count = sum(flag.reindex(index).fillna(False).astype(int) for flag in technical_flags.values()) \
        if technical_flags else pd.Series(0, index=index)

    # Thesis Kill
    thesis_flags: dict[str, pd.Series] = {}
    if ods_stock is not None and not ods_stock.empty:
        o = ods_stock.drop_duplicates("ts_code").set_index("ts_code")
        if "ods" in o.columns:
            thesis_flags["行业机会密度退坡"] = o["ods"].reindex(index) < 45
    if not dataset.forecast.empty:
        latest_fc = dataset.forecast.copy()
        mid = (pd.to_numeric(latest_fc["p_change_min"], errors="coerce")
               + pd.to_numeric(latest_fc["p_change_max"], errors="coerce")) / 2
        latest_fc = latest_fc.assign(_mid=mid).sort_values(["ts_code", "ann_date"]).drop_duplicates("ts_code", keep="last")
        thesis_flags["业绩预告转差"] = (latest_fc.set_index("ts_code")["_mid"].reindex(index) < -30)
    thesis_count = sum(flag.fillna(False).astype(int) for flag in thesis_flags.values()) if thesis_flags \
        else pd.Series(0, index=index)

    out = pd.DataFrame(index=index)
    out["kill_fundamental"] = fundamental_count
    out["kill_technical"] = technical_count
    out["kill_thesis"] = thesis_count
    out["kill_count"] = fundamental_count + technical_count + thesis_count
    reasons = []
    for ts_code in index:
        items = []
        for label, flag in list(fundamental_flags.items()) + list(technical_flags.items()) + list(thesis_flags.items()):
            if ts_code in flag.index and bool(flag.reindex(index).loc[ts_code]):
                items.append(label)
        reasons.append(" | ".join(items))
    out["kill_reasons"] = reasons
    warning = int(settings.path("kill.rules.warning", 1))
    reduce = int(settings.path("kill.rules.reduce", 2))
    exit_at = int(settings.path("kill.rules.exit", 3))
    out["kill_level"] = np.select(
        [out["kill_count"] >= exit_at, out["kill_count"] >= reduce, out["kill_count"] >= warning],
        ["EXIT", "REDUCE", "WARNING"], default="NORMAL",
    )
    return out.reset_index()
