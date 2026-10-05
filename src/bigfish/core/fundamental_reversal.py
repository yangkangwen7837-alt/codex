"""LEVEL 3 · Fundamental Reversal Score（规格书第 7~14 节）。

核心判断：**公司经营趋势是否发生方向性变化**，而不是"公司好不好"。
评分沿用规格书的加分制（收入 20 / 利润 25 / 毛利率 15 / 现金流 15 / 经营质量 10 /
特殊信号 15，合计 100），缺失分项不填充，按可用分项重新归一化并记录覆盖率。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..pipeline.dataset import BigFishDataset
from ..utils import clip_score, data_flag, safe_div
from .common import (forecast_snapshot, industry_aggregate, stock_industry_map,
                     tail_wide)

TAIL_COLS = [
    "revenue", "revenue_yoy", "revenue_qoq", "revenue_accel",
    "np_yoy", "np_qoq", "np_accel", "dedt_np_yoy", "op_yoy",
    "gross_margin", "gross_margin_chg_yoy", "gross_margin_4q_avg",
    "op_margin", "net_margin",
    "n_cashflow_act", "cfo_to_np", "cfo_yoy",
    "inventory_to_revenue", "inventory_to_revenue_chg", "inventory_yoy",
    "receivable_yoy", "receivable_to_revenue", "contract_liab_yoy",
    "contract_liab_to_revenue", "debt_to_assets", "debt_ratio_chg_yoy",
    "roe_sq", "roe_ytd", "roic_ytd", "debt_to_assets_calc",
    "ttm_np", "ttm_revenue", "ttm_cfo", "ttm_dedt_np",
    "one_off_ratio", "dedt_gap_ratio", "low_base_ratio",
    "sq_np",
    # 金融行业专用（TTM 口径 + 净资产 + 投资收益占比）
    "ttm_revenue_yoy", "ttm_np_yoy", "ttm_dedt_np_yoy",
    "equity_yoy", "assets_yoy", "invest_income_to_np", "invest_income_to_np_chg",
]


def _award(condition: pd.Series, points: float, available: pd.Series) -> tuple[pd.Series, pd.Series]:
    """条件成立 → 给分；available 记录该规则在当前数据下是否可评估。"""
    awarded = pd.Series(np.where(condition.fillna(False), points, 0.0), index=condition.index)
    return awarded, available.astype(float) * points


def compute_frs(dataset: BigFishDataset) -> pd.DataFrame:
    settings = dataset.settings
    rules = settings.path("frs.rules", {}) or {}
    penalties = settings.path("frs.penalties", {}) or {}
    panel = dataset.fundamentals
    if panel is None or panel.empty:
        return pd.DataFrame()

    wide = tail_wide(panel, TAIL_COLS, n=6)
    if wide.empty:
        return pd.DataFrame()

    def col(name: str, t: int = 0, default=np.nan) -> pd.Series:
        key = f"{name}_t{t}"
        if key in wide.columns:
            return wide[key]
        return pd.Series(default, index=wide.index)

    out = pd.DataFrame(index=wide.index)
    reasons: dict[str, list[str]] = {code: [] for code in wide.index}
    penalty_notes: dict[str, list[str]] = {code: [] for code in wide.index}
    points = pd.Series(0.0, index=wide.index)
    max_points = pd.Series(0.0, index=wide.index)
    comp_points: dict[str, pd.Series] = {}
    comp_max: dict[str, pd.Series] = {}

    def apply(component: str, awarded: pd.Series, available: pd.Series) -> None:
        nonlocal points, max_points
        comp_points[component] = comp_points.get(component, pd.Series(0.0, index=wide.index)) + awarded
        comp_max[component] = comp_max.get(component, pd.Series(0.0, index=wide.index)) + available
        points = points + awarded
        max_points = max_points + available

    # ---------------- 行业基准（用于"明显强于行业"） ----------------
    industry_map = stock_industry_map(dataset)
    ind_agg = industry_aggregate(panel, industry_map, ["revenue_yoy", "np_yoy", "gross_margin_chg_yoy"])
    ind_rev = ind_agg["med_revenue_yoy"] if "med_revenue_yoy" in ind_agg.columns else pd.Series(dtype=float)
    ind_name = industry_map.drop_duplicates(subset=["ts_code"]).set_index("ts_code")["industry_name"]
    industry_rev_yoy = ind_name.reindex(wide.index).map(ind_rev)

    # ---------------- 8. 收入反转（20） ----------------
    rev0, rev1, rev2 = col("revenue_yoy", 0), col("revenue_yoy", 1), col("revenue_yoy", 2)
    rev_avail = rev0.notna() & rev1.notna()

    cond = (rev0 > rev1) & (rev1 > rev2)
    apply("revenue", *_award(cond, rules.get("revenue_two_quarter_improve", 5), rev_avail & rev2.notna()))
    reasons_cond = cond
    for code in wide.index[reasons_cond.fillna(False)]:
        reasons[code].append("收入同比连续两季改善")

    cond = (rev0 > 0) & (rev1 <= 0)
    apply("revenue", *_award(cond, rules.get("revenue_turn_positive", 5), rev_avail))
    for code in wide.index[cond.fillna(False)]:
        reasons[code].append("收入同比由负转正")

    cond = col("revenue_accel", 0) > 0
    apply("revenue", *_award(cond, rules.get("revenue_accelerate", 5), rev_avail))
    for code in wide.index[cond.fillna(False)]:
        reasons[code].append("收入同比增速加速")

    cond = rev0 > industry_rev_yoy
    apply("revenue", *_award(cond, rules.get("revenue_beat_industry", 5), rev0.notna() & industry_rev_yoy.notna()))
    for code in wide.index[cond.fillna(False)]:
        reasons[code].append("收入增速强于行业中位数")

    # ---------------- 9. 利润反转（25） ----------------
    np0, np1 = col("np_yoy", 0), col("np_yoy", 1)
    np_avail = np0.notna() & np1.notna()

    cond = (np0 > np1) & (np0 < 0)
    apply("profit", *_award(cond, rules.get("profit_narrow_loss", 5), np_avail))
    for code in wide.index[cond.fillna(False)]:
        reasons[code].append("利润同比降幅收窄")

    cond = (np0 > 0) & (np1 <= 0)
    apply("profit", *_award(cond, rules.get("profit_turn_positive", 5), np_avail))
    for code in wide.index[cond.fillna(False)]:
        reasons[code].append("利润同比由负转正")

    cond = (col("np_accel", 0) > 0) & (np0 > 0)
    apply("profit", *_award(cond, rules.get("profit_accelerate", 5), np_avail))
    for code in wide.index[cond.fillna(False)]:
        reasons[code].append("利润同比增速加速")

    cond = np0 > rev0
    apply("profit", *_award(cond, rules.get("profit_grow_faster_than_revenue", 5), np0.notna() & rev0.notna()))
    for code in wide.index[cond.fillna(False)]:
        reasons[code].append("利润增速快于收入增速")

    ded0, ded1 = col("dedt_np_yoy", 0), col("dedt_np_yoy", 1)
    cond = (ded0 > 0) & (ded0 > ded1)
    apply("profit", *_award(cond, rules.get("profit_deducted_improve", 5), ded0.notna() & ded1.notna()))
    for code in wide.index[cond.fillna(False)]:
        reasons[code].append("扣非利润同步改善")

    # ---------------- 10. 毛利率反转（15） ----------------
    gm0, gm1, gm2 = col("gross_margin", 0), col("gross_margin", 1), col("gross_margin", 2)
    gm_hist_min = pd.concat([col("gross_margin", t) for t in (1, 2, 3, 4)], axis=1).min(axis=1)
    gm_avail = gm0.notna() & gm1.notna()

    cond = (gm0 > gm1) & (gm1 <= gm_hist_min + 1e-9)
    apply("margin", *_award(cond, rules.get("margin_bottoming", 5), gm_avail & gm_hist_min.notna()))
    for code in wide.index[cond.fillna(False)]:
        reasons[code].append("毛利率触底回升")

    cond = (gm0 > gm1) & (gm1 > gm2)
    apply("margin", *_award(cond, rules.get("margin_continuous_improve", 5), gm_avail & gm2.notna()))
    for code in wide.index[cond.fillna(False)]:
        reasons[code].append("毛利率连续改善")

    cond = gm0 > col("gross_margin_4q_avg", 0)
    apply("margin", *_award(cond, rules.get("margin_above_4q_avg", 5), gm0.notna() & col("gross_margin_4q_avg", 0).notna()))
    for code in wide.index[cond.fillna(False)]:
        reasons[code].append("毛利率高于过去 4 季均值")

    # ---------------- 11. 现金流与资产负债表（15） ----------------
    cond = (col("n_cashflow_act", 0) > col("n_cashflow_act", 1)) & (col("n_cashflow_act", 0) > 0)
    apply("cashflow", *_award(cond, rules.get("cashflow_profit_and_cfo_up", 5),
                              col("n_cashflow_act", 0).notna() & col("n_cashflow_act", 1).notna()))
    for code in wide.index[cond.fillna(False)]:
        reasons[code].append("经营现金流改善转正")

    cond = col("inventory_to_revenue_chg", 0) < 0
    apply("cashflow", *_award(cond, rules.get("cashflow_inventory_down", 3), col("inventory_to_revenue_chg", 0).notna()))
    for code in wide.index[cond.fillna(False)]:
        reasons[code].append("库存占收入比下降")

    cond = col("receivable_yoy", 0) < col("revenue_yoy", 0)
    apply("cashflow", *_award(cond, rules.get("cashflow_receivable_improve", 3),
                              col("receivable_yoy", 0).notna() & rev0.notna()))
    for code in wide.index[cond.fillna(False)]:
        reasons[code].append("应收增速低于收入增速")

    cond = col("debt_ratio_chg_yoy", 0) < 0
    apply("cashflow", *_award(cond, rules.get("cashflow_debt_relief", 2), col("debt_ratio_chg_yoy", 0).notna()))
    for code in wide.index[cond.fillna(False)]:
        reasons[code].append("资产负债率同比下降")

    # 低质量反转扣分
    pen = pd.Series(0.0, index=wide.index)
    cond = col("cfo_to_np", 0) < 0
    pen = pen + np.where(cond.fillna(False), penalties.get("cfo_persistently_negative", 4), 0.0)
    cond2 = (col("receivable_yoy", 0) > 0.5) & (col("receivable_yoy", 0) > rev0)
    pen = pen + np.where(cond2.fillna(False), penalties.get("receivable_surge", 3), 0.0)
    cond3 = col("inventory_yoy", 0) > 0.5
    pen = pen + np.where(cond3.fillna(False), penalties.get("inventory_surge", 3), 0.0)
    for code in wide.index[(cond.fillna(False))]:
        penalty_notes[code].append("CFO/净利为负")
    for code in wide.index[(cond2.fillna(False))]:
        penalty_notes[code].append("应收账款暴增")
    for code in wide.index[(cond3.fillna(False))]:
        penalty_notes[code].append("存货暴增")

    # ---------------- 12. 经营质量（10） ----------------
    roic_hist_min = pd.concat([col("roic_ytd", t) for t in (1, 2, 3, 4)], axis=1).min(axis=1)
    cond = (col("roic_ytd", 0) > col("roic_ytd", 1)) & (col("roic_ytd", 1) <= roic_hist_min + 1e-9)
    apply("quality", *_award(cond, rules.get("quality_roic_bottoming", 5),
                             col("roic_ytd", 0).notna() & roic_hist_min.notna()))
    for code in wide.index[cond.fillna(False)]:
        reasons[code].append("ROIC 触底")

    cond = (col("roe_sq", 0) > col("roe_sq", 1)) & (col("roe_sq", 0) > 0)
    apply("quality", *_award(cond, rules.get("quality_roe_inflection", 5),
                             col("roe_sq", 0).notna() & col("roe_sq", 1).notna()))
    for code in wide.index[cond.fillna(False)]:
        reasons[code].append("单季 ROE 拐点向上")

    # ---------------- 13. 特殊基本面信号（15） ----------------
    fc = forecast_snapshot(dataset)
    fc_pos = fc["fc_positive"].reindex(wide.index) if not fc.empty else pd.Series(np.nan, index=wide.index)

    cond = (col("revenue_accel", 0) > 0) & (col("contract_liab_yoy", 0) > 0)
    apply("special", *_award(cond, rules.get("special_revenue_accel_with_order", 5),
                             col("contract_liab_yoy", 0).notna() & col("revenue_accel", 0).notna()))
    for code in wide.index[cond.fillna(False)]:
        reasons[code].append("收入加速且合同负债增长（订单/预收改善）")

    cond = col("gross_margin_chg_yoy", 0) > 0.01
    apply("special", *_award(cond, rules.get("special_margin_expansion", 5),
                             col("gross_margin_chg_yoy", 0).notna()))
    for code in wide.index[cond.fillna(False)]:
        reasons[code].append("毛利率同比显著扩张（>1pct）")

    cond = fc_pos > 0
    apply("special", *_award(cond, rules.get("special_forecast_positive", 5), fc_pos.notna()))
    for code in wide.index[cond.fillna(False)]:
        reasons[code].append("最新业绩预告为正增长")

    # ---------------- 汇总 ----------------
    # 金融行业（银行 / 非银金融）改用专用规则，覆盖制造业规则的结果
    fin_industries = set(settings.path("frs.financial_industries", []) or [])
    if fin_industries:
        ind_series = ind_name.reindex(wide.index)
        is_financial = ind_series.isin(fin_industries).fillna(False)
        if bool(is_financial.any()):
            fin = _financial_rule_points(col, settings, wide.index, is_financial, fc_pos)
            points = points.where(~is_financial, fin["points"])
            max_points = max_points.where(~is_financial, fin["max_points"])
            for key, series in fin["components"].items():
                comp_points[key] = comp_points.get(key, pd.Series(0.0, index=wide.index)).where(
                    ~is_financial, series["points"])
                comp_max[key] = comp_max.get(key, pd.Series(0.0, index=wide.index)).where(
                    ~is_financial, series["max"]) 
            for i, code in enumerate(wide.index):
                if bool(is_financial.iloc[i]):
                    reasons[code] = fin["reasons"][i]
            out["frs_template"] = np.where(is_financial, "FINANCIAL", "STANDARD")
        else:
            out["frs_template"] = "STANDARD"
    else:
        out["frs_template"] = "STANDARD"

    total = (points - pen).clip(lower=0)
    coverage = safe_div(max_points, pd.Series(100.0, index=wide.index))
    frs = pd.Series(np.where(max_points > 0, total / max_points * 100.0, np.nan), index=wide.index)
    frs = frs.clip(0, 100)

    out["frs_structure"] = frs

    # ---------------- 幅度分（回测暴露：加分制无法区分"微弱改善"与"大幅反转"） ----------------
    mag_weights = {"d_rev_yoy": 0.30, "d_np_yoy": 0.40, "d_margin_yoy": 0.30}
    mag_values = {
        "d_rev_yoy": rev0 - rev1,
        "d_np_yoy": np0 - np1,
        "d_margin_yoy": col("gross_margin_chg_yoy", 0),
    }
    if fin_industries:
        # 金融股用 TTM 口径的同比变化作为幅度分（单季口径会被投资收益放大）
        fin_mask = out["frs_template"].eq("FINANCIAL").to_numpy()
        mag_values["d_rev_yoy"] = mag_values["d_rev_yoy"].where(
            ~pd.Series(fin_mask, index=out.index), col("ttm_revenue_yoy", 0) - col("ttm_revenue_yoy", 1))
        mag_values["d_np_yoy"] = mag_values["d_np_yoy"].where(
            ~pd.Series(fin_mask, index=out.index), col("ttm_np_yoy", 0) - col("ttm_np_yoy", 1))
        mag_values["d_margin_yoy"] = mag_values["d_margin_yoy"].where(
            ~pd.Series(fin_mask, index=out.index), col("net_margin", 0) - col("net_margin", 4))
    mag_parts, mag_weights_used = [], []
    for key, weight in mag_weights.items():
        series = mag_values[key]
        percentile = series.rank(pct=True, na_option="keep") * 100.0
        mag_parts.append(percentile * weight)
        mag_weights_used.append(series.notna().astype(float) * weight)
    if mag_parts:
        magnitude = (sum(mag_parts) / sum(mag_weights_used).replace(0, np.nan)).clip(0, 100)
        mag_coverage = sum(mag_weights_used) / sum(mag_weights.values())
    else:
        magnitude = pd.Series(np.nan, index=out.index)
        mag_coverage = pd.Series(0.0, index=out.index)
    out["frs_magnitude"] = magnitude
    out["frs_magnitude_coverage"] = mag_coverage

    w_struct = float(settings.path("frs.structure_weight", 0.65))
    w_mag = float(settings.path("frs.magnitude_weight", 0.35))
    use_mag = magnitude.notna() & (mag_coverage >= 0.5)
    combined = pd.Series(np.nan, index=out.index)
    combined[use_mag] = (w_struct * frs[use_mag] + w_mag * magnitude[use_mag]) / (w_struct + w_mag)
    combined[~use_mag] = frs[~use_mag]

    # ---------------- 低基数降级（回测：低基数假反转平均亏 12.8%） ----------------
    base_profit = col("sq_np", 4)
    low_base_ratio = col("low_base_ratio", 0)
    low_base_threshold = float(settings.path("filters.fake_turnaround.low_base_ratio", 0.3))
    low_base_penalty = (
        (low_base_ratio < low_base_threshold) & (base_profit > 0) & (col("np_yoy", 0) > 0)
    ).fillna(False)
    discount = float(settings.path("frs.low_base_discount", 0.85))
    combined = combined.where(~low_base_penalty, combined * discount)
    out["low_base_penalty"] = low_base_penalty
    for code in wide.index[low_base_penalty]:
        penalty_notes[code].append("低基数同比失真（FRS 打折）")

    out["frs"] = combined
    for code in wide.index[low_base_penalty]:
        if "低基数同比失真（FRS 打折）" not in reasons[code]:
            reasons[code].append("低基数同比失真（FRS 打折）")
    out["frs_points"] = points
    out["frs_penalty"] = pen
    out["frs_points_available"] = max_points
    out["frs_coverage"] = coverage
    for component in ("revenue", "profit", "margin", "cashflow", "quality", "special"):
        granted = comp_points.get(component, pd.Series(0.0, index=wide.index))
        avail = comp_max.get(component, pd.Series(0.0, index=wide.index))
        out[f"frs_{component}"] = np.where(avail > 0, granted / avail * 100.0, np.nan)
        out[f"frs_{component}_max"] = avail
    out["frs_reasons"] = [" | ".join(reasons[c]) for c in wide.index]
    out["frs_penalties"] = [" | ".join(penalty_notes[c]) for c in wide.index]
    min_coverage = float(settings.path("frs.min_coverage", 0.5))
    out.loc[out["frs_coverage"] < min_coverage, "frs"] = np.nan
    out["frs_flag"] = [
        data_flag(cov, 0.9, min_coverage) if np.isfinite(cov) else "DATA INCOMPLETE" for cov in out["frs_coverage"]
    ]

    # 关键原文指标（供卡片 / 复核使用）
    for name in ("revenue_yoy", "np_yoy", "gross_margin", "net_margin", "inventory_to_revenue",
                 "receivable_yoy", "contract_liab_yoy", "debt_to_assets", "one_off_ratio",
                 "low_base_ratio", "ttm_np", "ttm_revenue", "ttm_cfo"):
        out[f"f_{name}"] = col(name, 0)

    latest_period = panel.sort_values(["ts_code", "end_date"]).drop_duplicates("ts_code", keep="last")
    out["latest_period"] = latest_period.set_index("ts_code")["end_date"].reindex(wide.index)
    out["latest_ann_date"] = latest_period.set_index("ts_code")["ann_date"].reindex(wide.index)
    out["quarters_available"] = latest_period.set_index("ts_code")["quarters_available"].reindex(wide.index)
    out["frs_stage"] = pd.cut(
        out["frs"], bins=[-1, 50, 60, 70, 80, 101],
        labels=["NO REVERSAL", "WEAK SIGNAL", "EARLY TURN", "CONFIRMED REVERSAL", "STRONG REVERSAL"],
    ).astype(str)
    out.loc[out["frs"].isna(), "frs_stage"] = "DATA INCOMPLETE"

    min_quarters = int(settings.path("frs.min_quarters", 6))
    out.loc[out["quarters_available"] < min_quarters, "frs_flag"] = "DATA INCOMPLETE"

    out = out.reset_index()
    out = out.merge(industry_map.rename(columns={"industry_name": "industry"}), on="ts_code", how="left")
    return out


def _financial_rule_points(col, settings, index, mask, fc_pos) -> dict:
    """银行 / 非银金融专用加分规则（TTM 口径 + 净资产 + ROE/ROA + 投资收益占比）。

    制造业规则里的毛利率、存货、应收、合同负债对金融企业不适用；
    而金融企业的利润又高度受投资收益波动影响，因此必须用 TTM 平滑，
    并要求"利润改善不是靠投资收益"。
    返回 dict(points, max_points, components, reasons)，只在 mask 为真的股票上生效。
    """
    rules = settings.path("frs.financial_rules", {}) or {}
    points = pd.Series(0.0, index=index)
    max_points = pd.Series(0.0, index=index)
    components: dict[str, dict] = {}
    reasons: list[list[str]] = [[] for _ in range(len(index))]

    def award(component: str, key: str, condition: pd.Series, available: pd.Series, label: str) -> None:
        nonlocal points, max_points
        weight = float(rules.get(key, 5))
        avail = available.fillna(False).astype(bool)
        hit = condition.fillna(False).astype(bool) & avail
        granted = pd.Series(np.where(hit.to_numpy(), weight, 0.0), index=index)
        capacity = pd.Series(np.where(avail.to_numpy(), weight, 0.0), index=index)
        points = points + granted
        max_points = max_points + capacity
        bucket = components.setdefault(component, {"points": pd.Series(0.0, index=index),
                                                   "max": pd.Series(0.0, index=index)})
        bucket["points"] = bucket["points"] + granted
        bucket["max"] = bucket["max"] + capacity
        for i, code in enumerate(index):
            if bool(hit.iloc[i]):
                reasons[i].append(label)

    rev0, rev1, rev2 = col("ttm_revenue_yoy", 0), col("ttm_revenue_yoy", 1), col("ttm_revenue_yoy", 2)
    np0, np1, np2 = col("ttm_np_yoy", 0), col("ttm_np_yoy", 1), col("ttm_np_yoy", 2)
    ded0, ded1 = col("ttm_dedt_np_yoy", 0), col("ttm_dedt_np_yoy", 1)
    nm0, nm1 = col("net_margin", 0), col("net_margin", 1)
    nm_hist = pd.concat([col("net_margin", t) for t in (1, 2, 3, 4)], axis=1)
    roe0, roe1 = col("roe_ytd", 0), col("roe_ytd", 1)
    roe_hist_min = pd.concat([col("roe_ytd", t) for t in (1, 2, 3, 4)], axis=1).min(axis=1)
    roa0, roa1 = col("roa_ytd", 0), col("roa_ytd", 1)
    roic0, roic1 = col("roic_ytd", 0), col("roic_ytd", 1)
    equity_yoy, assets_yoy = col("equity_yoy", 0), col("assets_yoy", 0)
    invest_chg = col("invest_income_to_np_chg", 0)

    # 收入（TTM）
    award("revenue", "revenue_ttm_two_quarter_improve", (rev0 > rev1) & (rev1 > rev2),
          rev0.notna() & rev1.notna() & rev2.notna(), "TTM 收入同比连续两季改善")
    award("revenue", "revenue_ttm_turn_positive", (rev0 > 0) & (rev1 <= 0),
          rev0.notna() & rev1.notna(), "TTM 收入同比由负转正")

    # 利润（TTM + 扣非）
    award("profit", "profit_ttm_two_quarter_improve", (np0 > np1) & (np1 > np2),
          np0.notna() & np1.notna() & np2.notna(), "TTM 归母同比连续两季改善")
    award("profit", "profit_ttm_turn_positive", (np0 > 0) & (np1 <= 0),
          np0.notna() & np1.notna(), "TTM 归母同比由负转正")
    award("profit", "profit_ttm_faster_than_revenue", np0 > rev0,
          np0.notna() & rev0.notna(), "TTM 利润增速快于收入增速")
    award("profit", "deducted_ttm_improve", (ded0 > 0) & (ded0 > ded1),
          ded0.notna() & ded1.notna(), "TTM 扣非利润同步改善")

    # 利润率（金融企业用净利率，不用毛利率）
    award("margin", "net_margin_bottoming", (nm0 > nm1) & (nm1 <= nm_hist.min(axis=1) + 1e-9),
          nm0.notna() & nm1.notna() & nm_hist.notna().any(axis=1), "净利率触底回升")
    award("margin", "net_margin_above_4q_avg", nm0 > nm_hist.mean(axis=1),
          nm0.notna() & nm_hist.notna().any(axis=1), "净利率高于过去 4 季均值")

    # 资本与资产扩张（替代制造业的现金流/存货/应收规则）
    award("cashflow", "equity_growth_positive", equity_yoy > 0,
          equity_yoy.notna(), "净资产同比正增长（内生资本积累）")
    award("cashflow", "asset_growth_healthy", (assets_yoy > 0) & (assets_yoy < 0.30),
          assets_yoy.notna(), "总资产同比在 0~30%（扩张但不失控）")

    # 经营质量
    award("quality", "roe_bottoming", (roe0 > roe1) & (roe1 <= roe_hist_min + 1e-9),
          roe0.notna() & roe_hist_min.notna(), "ROE 触底回升")
    award("quality", "roa_or_roic_inflection", (roa0 > roa1) | (roic0 > roic1),
          (roa0.notna() & roa1.notna()) | (roic0.notna() & roic1.notna()), "ROA / ROIC 拐点向上")

    # 特殊信号：利润改善不能靠投资收益 + 业绩预告
    award("special", "investment_income_ratio_declining", invest_chg < 0,
          invest_chg.notna(), "投资收益占利润比重下降（利润非投资收益驱动）")
    award("special", "forecast_positive", fc_pos > 0, fc_pos.notna(), "最新业绩预告为正增长")

    result = {"points": points, "max_points": max_points, "components": components, "reasons": reasons}
    return result


def classify_reversal_type(row: pd.Series, industry_context: dict) -> str:
    """规格书第 51 节：反转类型自动识别。"""
    rev_yoy = row.get("f_revenue_yoy", np.nan)
    gm_chg = row.get("f_gross_margin", np.nan)
    contract = row.get("f_contract_liab_yoy", np.nan)
    industry_price_up = bool(industry_context.get("price_vs_ma60", 0) > 0.02)
    industry_inventory_down = bool(industry_context.get("inventory_down", False))
    revenue_up = bool(np.isfinite(rev_yoy) and rev_yoy > 0.05)

    if industry_price_up and industry_inventory_down:
        return "SUPPLY_CONTRACTION"
    if revenue_up and industry_price_up:
        return "CYCLE_REVERSAL"
    if np.isfinite(contract) and contract > 0 and revenue_up:
        return "DEMAND_REVERSAL"
    if revenue_up:
        return "PRODUCT_REVERSAL"
    if np.isfinite(gm_chg) and gm_chg > 0 and not revenue_up:
        return "COST_REVERSAL"
    if np.isfinite(gm_chg) and gm_chg > 0:
        return "MARGIN_REVERSAL"
    return "CYCLE_REVERSAL"
