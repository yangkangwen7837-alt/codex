"""过滤器：Value Trap / Fake Turnaround / Risk Block（规格书第 49~50、82 节）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..pipeline.dataset import BigFishDataset
from .common import latest_snapshot, tail_wide


def compute_filters(dataset: BigFishDataset, frs: pd.DataFrame, rps: pd.DataFrame,
                    ods_stock: pd.DataFrame | None = None) -> pd.DataFrame:
    settings = dataset.settings
    panel = dataset.fundamentals
    if panel is None or panel.empty:
        return pd.DataFrame()
    latest = latest_snapshot(panel)
    wide = tail_wide(panel, ["revenue_yoy", "np_yoy", "cfo_to_np", "one_off_ratio", "low_base_ratio"], n=5)
    index = latest.index

    rev_yoy = pd.to_numeric(latest["revenue_yoy"], errors="coerce").reindex(index)
    np_yoy = pd.to_numeric(latest["np_yoy"], errors="coerce").reindex(index)
    cfo_to_np = pd.to_numeric(latest["cfo_to_np"], errors="coerce").reindex(index)
    pe_ttm = pd.Series(np.nan, index=index)
    pb = pd.Series(np.nan, index=index)
    amount = pd.Series(np.nan, index=index)
    if dataset.basic is not None and not dataset.basic.empty:
        last = dataset.basic["trade_date"].max()
        snap = dataset.basic.loc[dataset.basic["trade_date"] == last].drop_duplicates("ts_code").set_index("ts_code")
        pe_ttm = pd.to_numeric(snap.get("pe_ttm"), errors="coerce").reindex(index)
        pb = pd.to_numeric(snap.get("pb"), errors="coerce").reindex(index)
    if dataset.prices is not None and not dataset.prices.empty:
        last = dataset.prices["trade_date"].max()
        snap = dataset.prices.loc[dataset.prices["trade_date"] == last].drop_duplicates("ts_code").set_index("ts_code")
        amount = pd.to_numeric(snap.get("amount"), errors="coerce").reindex(index)

    industry_ods = pd.Series(np.nan, index=index)
    if ods_stock is not None and not ods_stock.empty:
        o = ods_stock.drop_duplicates("ts_code").set_index("ts_code")
        industry_ods = pd.to_numeric(o.get("ods"), errors="coerce").reindex(index)

    max_pe = float(settings.path("filters.value_trap.max_pe", 25))
    max_pb = float(settings.path("filters.value_trap.max_pb", 2.0))
    cheap = (pe_ttm < max_pe) | (pb < max_pb)
    deteriorating = (rev_yoy < 0) | (np_yoy < 0)
    weak_cashflow = cfo_to_np < 0.3
    weak_industry = industry_ods.isna() | (industry_ods < 60)
    value_trap = cheap.fillna(False) & deteriorating.fillna(False) & (weak_cashflow.fillna(False) | weak_industry)

    one_off_threshold = float(settings.path("filters.fake_turnaround.one_off_ratio", 0.6))
    one_off = pd.to_numeric(latest["one_off_ratio"], errors="coerce").reindex(index)
    low_base_threshold = float(settings.path("filters.fake_turnaround.low_base_ratio", 0.3))
    low_base = pd.to_numeric(latest["low_base_ratio"], errors="coerce").reindex(index)
    dedt_gap = pd.to_numeric(latest.get("dedt_gap_ratio"), errors="coerce").reindex(index)

    # 一次性收益 / 扣非差异 → 假反转（规格书第 50 节）
    # 回测验证：只按"一次性收益占比高"判定会把很多正常改善的公司误杀（命中组并不更差），
    # 因此收窄为"归母在涨、但扣非在跌或显著落后"——即利润改善确实由非经常性损益驱动。
    np_yoy = pd.to_numeric(latest["np_yoy"], errors="coerce").reindex(index)
    dedt_yoy = pd.to_numeric(latest.get("dedt_np_yoy"), errors="coerce").reindex(index)
    require_divergence = bool(settings.path("filters.fake_turnaround.require_dedt_divergence", True))
    gap = float(settings.path("filters.fake_turnaround.dedt_lag_gap", 0.3))
    divergence = ((np_yoy > 0) & ((dedt_yoy < 0) | (dedt_yoy < np_yoy - gap))).fillna(False)
    if not require_divergence:
        divergence = pd.Series(True, index=index)
    fake_one_off = (one_off > one_off_threshold).fillna(False) & divergence
    fake_dedt_gap = (dedt_gap > one_off_threshold).fillna(False) & divergence
    # 低基数只做提示，不直接判假（规格书第 50 节：低基数必须说明）
    note_low_base = (low_base < low_base_threshold).fillna(False)

    min_amount = float(settings.path("filters.risk_block.min_amount", 3000))
    liquidity_risk = (amount < min_amount).fillna(False)
    names = pd.Series(index=index, dtype=object)
    if dataset.universe is not None and not dataset.universe.empty:
        names = dataset.universe.drop_duplicates("ts_code").set_index("ts_code")["name"].reindex(index)
    st_risk = names.astype(str).str.contains("ST", na=False) | names.astype(str).str.contains("退", na=False)
    risk_block = liquidity_risk | st_risk

    out = pd.DataFrame(index=index)
    out["name"] = names
    out["is_value_trap"] = value_trap
    out["is_fake_turnaround"] = fake_one_off | fake_dedt_gap
    out["fake_one_off"] = fake_one_off
    out["note_low_base"] = note_low_base
    out["fake_dedt_gap"] = fake_dedt_gap
    out["risk_block"] = risk_block
    out["risk_liquidity"] = liquidity_risk
    out["risk_st"] = st_risk
    notes = []
    for ts_code in index:
        items = []
        if bool(value_trap.get(ts_code, False)):
            items.append("VALUE_TRAP")
        if bool(fake_one_off.get(ts_code, False)):
            items.append("一次性收益嫌疑")
        if bool(note_low_base.get(ts_code, False)):
            items.append("低基数（需说明）")
        if bool(fake_dedt_gap.get(ts_code, False)):
            items.append("扣非与归母差异大")
        if bool(risk_block.get(ts_code, False)):
            items.append("RISK_BLOCK")
        notes.append(" | ".join(items))
    out["filter_notes"] = notes
    out["filter_flag"] = np.select(
        [out["risk_block"], out["is_value_trap"], out["is_fake_turnaround"]],
        ["RISK_BLOCK", "VALUE_TRAP", "FAKE_TURNAROUND"], default="OK",
    )
    return out.reset_index()
