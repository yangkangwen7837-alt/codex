"""财务报表 → Point-in-Time 单季度基本面面板（规格书第 68 节）。

三条硬规则：
1. 一律使用 ``ann_date``（公告日）作为可见时间，不使用报告期结束日；
2. 利润表 / 现金流量表是累计口径，必须先做单季度化，再做 YoY / QoQ / 加速度；
3. 同一报告期存在多次公告（更正/重述）时，只保留**最早**公告，避免未来信息。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..periods import single_quarterize
from ..utils import pct_change, safe_div

INCOME_COLS = ["revenue", "total_revenue", "oper_cost", "operate_profit", "n_income_attr_p", "n_income",
               "invest_income", "fv_value_chg_gain", "non_oper_income", "total_cogs", "rd_exp",
               "sell_exp", "admin_exp", "fin_exp", "income_tax"]

CASHFLOW_COLS = ["n_cashflow_act", "c_pay_acq_const_fiolta", "free_cashflow",
                 "n_cashflow_inv_act", "n_cash_flows_fnc_act", "depr_fa_coga_dpba"]

BALANCE_COLS = ["inventories", "accounts_receiv", "notes_receiv", "accounts_receiv_bill",
                "contract_liab", "adv_receipts", "deferred_inc", "total_assets", "total_liab",
                "total_hldr_eqy_exc_min_int", "money_cap", "st_borr", "lt_borr",
                "notes_payable", "acct_payable", "goodwill", "r_and_d", "fix_assets", "cip"]

INDICATOR_COLS = ["profit_dedt", "roe", "roe_dt", "roic", "q_roe", "q_dt_roe", "q_npta",
                  "grossprofit_margin", "netprofit_margin", "op_of_gr", "debt_to_assets",
                  "ocf_to_or", "q_ocf_to_sales", "assets_turn", "ar_turn", "or_yoy",
                  "netprofit_yoy", "dt_netprofit_yoy", "q_sales_yoy", "q_op_qoq", "ocf_yoy"]


def _pick(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    return df[[c for c in cols if c in df.columns]].copy()


def _coerce_numeric(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """历史报告期里部分字段整列为 None，会退化成 object dtype，这里统一转数值。"""
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _dedupe_pit(df: pd.DataFrame, code_col: str = "ts_code", period_col: str = "end_date",
                ann_col: str = "ann_date") -> pd.DataFrame:
    """同一报告期多次公告 → 保留最早公告（point-in-time）。"""
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    out[ann_col] = out[ann_col].astype(str)
    out = out.sort_values([code_col, period_col, ann_col])
    out = out.drop_duplicates(subset=[code_col, period_col], keep="first")
    return out.reset_index(drop=True)


def build_fundamental_panel(income: pd.DataFrame, balance: pd.DataFrame, cashflow: pd.DataFrame,
                            indicator: pd.DataFrame) -> pd.DataFrame:
    """返回每行 = (ts_code, 报告期) 的单季度基本面面板，带 ann_date。"""
    if income is None or income.empty:
        return pd.DataFrame()

    inc = _pick(income, ["ts_code", "ann_date", "f_ann_date", "end_date"] + INCOME_COLS)
    inc = _coerce_numeric(inc, INCOME_COLS)
    inc["ann_date"] = inc["f_ann_date"].fillna(inc["ann_date"]).astype(str) if "f_ann_date" in inc else inc["ann_date"].astype(str)
    inc = _dedupe_pit(inc)

    # 单季度化（累计 → 单季）
    inc_sq = single_quarterize(inc, [c for c in INCOME_COLS if c in inc.columns])
    if "revenue" in inc_sq.columns and "total_revenue" in inc_sq.columns:
        inc_sq["revenue"] = inc_sq["revenue"].fillna(inc_sq["total_revenue"])
    elif "total_revenue" in inc_sq.columns:
        inc_sq["revenue"] = inc_sq["total_revenue"]

    panel = inc_sq

    if cashflow is not None and not cashflow.empty:
        cf = _pick(cashflow, ["ts_code", "ann_date", "end_date"] + CASHFLOW_COLS)
        cf = _coerce_numeric(cf, CASHFLOW_COLS)
        cf["ann_date"] = cf["ann_date"].astype(str)
        cf = _dedupe_pit(cf)
        cf_sq = single_quarterize(cf, [c for c in CASHFLOW_COLS if c in cf.columns])
        panel = panel.merge(cf_sq.drop(columns=["ann_date"]), on=["ts_code", "end_date"], how="left")

    if balance is not None and not balance.empty:
        bs = _pick(balance, ["ts_code", "ann_date", "end_date"] + BALANCE_COLS)
        bs = _coerce_numeric(bs, BALANCE_COLS)
        bs["ann_date"] = bs["ann_date"].astype(str)
        bs = _dedupe_pit(bs)
        # 资产负债表是时点数，不做单季度化
        panel = panel.merge(bs.drop(columns=["ann_date"]), on=["ts_code", "end_date"], how="left")

    if indicator is not None and not indicator.empty:
        fi = _pick(indicator, ["ts_code", "ann_date", "end_date"] + INDICATOR_COLS)
        fi = _coerce_numeric(fi, INDICATOR_COLS)
        fi["ann_date"] = fi["ann_date"].astype(str)
        fi = _dedupe_pit(fi)
        ded = fi.drop(columns=["ann_date"])
        # profit_dedt 是累计口径，需单独单季度化后再合并
        if "profit_dedt" in fi.columns:
            ded_sq = single_quarterize(fi[["ts_code", "end_date", "profit_dedt"]], ["profit_dedt"])
            ded = ded.drop(columns=["profit_dedt"], errors="ignore").merge(
                ded_sq[["ts_code", "end_date", "profit_dedt"]], on=["ts_code", "end_date"], how="left"
            )
        panel = panel.merge(ded, on=["ts_code", "end_date"], how="left")

    panel = panel.sort_values(["ts_code", "end_date"]).reset_index(drop=True)
    return _derive(panel)


def _derive(panel: pd.DataFrame) -> pd.DataFrame:
    """基于单季度面板派生 YoY / QoQ / 加速度 / TTM / 资产负债质量。"""
    df = panel.copy()
    df = df.sort_values(["ts_code", "end_date"]).reset_index(drop=True)
    grp = df.groupby("ts_code", sort=False)

    def lag(col: str, n: int) -> pd.Series:
        return grp[col].shift(n) if col in df.columns else pd.Series(np.nan, index=df.index)

    # ---- 收入 / 利润：单季 YoY（-4 季）、QoQ（-1 季）、加速度 ----
    for name, col in (("revenue", "revenue"), ("np", "n_income_attr_p"), ("op", "operate_profit"),
                      ("gross_profit", None), ("dedt_np", "profit_dedt"), ("cfo", "n_cashflow_act")):
        if name == "gross_profit":
            if "revenue" in df.columns and "oper_cost" in df.columns:
                df["sq_gross_profit"] = df["revenue"] - df["oper_cost"]
            continue
        if col not in df.columns:
            continue
        df[f"sq_{name}"] = df[col]
        df[f"{name}_yoy"] = pct_change(df[col], lag(col, 4))
        df[f"{name}_qoq"] = pct_change(df[col], lag(col, 1))

    for name in ("revenue", "np", "op"):
        if f"{name}_yoy" in df.columns:
            df[f"{name}_accel"] = df[f"{name}_yoy"] - grp[f"{name}_yoy"].shift(1)

    # ---- 利润率（单季）----
    if "sq_gross_profit" in df.columns:
        df["gross_margin"] = safe_div(df["sq_gross_profit"], df["revenue"])
    elif "grossprofit_margin" in df.columns:
        df["gross_margin"] = df["grossprofit_margin"] / 100.0
    if "operate_profit" in df.columns:
        df["op_margin"] = safe_div(df["operate_profit"], df["revenue"])
    if "n_income_attr_p" in df.columns:
        df["net_margin"] = safe_div(df["n_income_attr_p"], df["revenue"])
    if "gross_margin" in df.columns:
        df["gross_margin_chg_yoy"] = df["gross_margin"] - grp["gross_margin"].shift(4)
        df["gross_margin_4q_avg"] = grp["gross_margin"].transform(
            lambda s: s.rolling(4, min_periods=2).mean().shift(1)
        )

    # ---- TTM（最近 4 个单季）----
    for name in ("revenue", "np", "dedt_np", "cfo", "op"):
        col = f"sq_{name}"
        if col in df.columns:
            df[f"ttm_{name}"] = grp[col].transform(lambda s: s.rolling(4, min_periods=4).sum())

    # ---- TTM 同比 / 加速度（金融行业用 TTM 平滑单季投资收益波动）----
    for name in ("revenue", "np", "dedt_np"):
        col = f"ttm_{name}"
        if col in df.columns:
            df[f"{col}_yoy"] = pct_change(df[col], grp[col].shift(4))
            df[f"{col}_accel"] = df[f"{col}_yoy"] - grp[f"{col}_yoy"].shift(1)

    # ---- 现金流质量 ----
    if "ttm_cfo" in df.columns and "ttm_np" in df.columns:
        df["cfo_to_np"] = safe_div(df["ttm_cfo"], df["ttm_np"].abs())
    if "n_cashflow_act" in df.columns:
        df["cfo_negative"] = (df["n_cashflow_act"] < 0).astype(float)
        df["cfo_yoy"] = pct_change(df["n_cashflow_act"], lag("n_cashflow_act", 4))

    # ---- 资产负债质量（与去年同期比）----
    for name, col in (("inventory", "inventories"), ("receivable", "accounts_receiv"),
                      ("contract_liab", "contract_liab"), ("assets", "total_assets"),
                      ("debt", "total_liab"), ("equity", "total_hldr_eqy_exc_min_int")):
        if col in df.columns:
            df[f"{name}_yoy"] = pct_change(df[col], grp[col].shift(4))
    if "inventories" in df.columns and "revenue" in df.columns:
        df["inventory_to_revenue"] = safe_div(df["inventories"], df["ttm_revenue"])
        df["inventory_to_revenue_chg"] = df["inventory_to_revenue"] - grp["inventory_to_revenue"].shift(4)
    if "accounts_receiv" in df.columns and "revenue" in df.columns:
        df["receivable_to_revenue"] = safe_div(df["accounts_receiv"], df["ttm_revenue"])
    if "contract_liab" in df.columns and "revenue" in df.columns:
        df["contract_liab_to_revenue"] = safe_div(df["contract_liab"], df["ttm_revenue"])
    if "total_liab" in df.columns and "total_assets" in df.columns:
        df["debt_to_assets_calc"] = safe_div(df["total_liab"], df["total_assets"])
    if "debt_to_assets" in df.columns:
        df["debt_to_assets"] = df["debt_to_assets"] / 100.0
        df["debt_ratio_chg_yoy"] = df["debt_to_assets"] - grp["debt_to_assets"].shift(4)
    if "fin_exp" in df.columns:
        df["fin_exp_to_revenue"] = safe_div(df["fin_exp"], df["revenue"])

    # ---- 盈利质量（ROE / ROIC 单季）----
    for raw, out in (("q_roe", "roe_sq"), ("q_dt_roe", "roe_dt_sq"), ("roe", "roe_ytd"),
                     ("roic", "roic_ytd"), ("roa", "roa_ytd")):
        if raw in df.columns:
            df[out] = df[raw] / 100.0
    for name in ("roe_sq", "roe_ytd", "roic_ytd"):
        if name in df.columns:
            df[f"{name}_chg_yoy"] = df[name] - grp[name].shift(4)
            df[f"{name}_4q_avg"] = grp[name].transform(lambda s: s.rolling(4, min_periods=2).mean())

    # ---- 一次性因素（用于识破假反转）----
    one_off = pd.Series(0.0, index=df.index)
    for col in ("invest_income", "fv_value_chg_gain", "non_oper_income"):
        if col in df.columns:
            one_off = one_off + df[col].fillna(0)
    df["one_off_gain"] = one_off
    if "n_income_attr_p" in df.columns:
        df["one_off_ratio"] = safe_div(df["one_off_gain"], df["n_income_attr_p"].abs())
    if "invest_income" in df.columns and "n_income_attr_p" in df.columns:
        # 金融行业专用：投资收益占归母净利润比重（保险/券商的利润波动主要来自这里）
        df["invest_income_to_np"] = safe_div(df["invest_income"], df["n_income_attr_p"].abs())
        df["invest_income_to_np_chg"] = df["invest_income_to_np"] - grp["invest_income_to_np"].shift(4)
    if "n_income_attr_p" in df.columns and "profit_dedt" in df.columns:
        df["dedt_gap"] = df["n_income_attr_p"] - df["profit_dedt"]
        df["dedt_gap_ratio"] = safe_div(df["dedt_gap"], df["n_income_attr_p"].abs())

    # ---- 低基数识别 ----
    if "sq_np" in df.columns:
        df["np_4q_avg_prev"] = grp["sq_np"].transform(lambda s: s.rolling(4, min_periods=2).mean().shift(1))
        df["low_base_ratio"] = safe_div(grp["sq_np"].shift(4), df["np_4q_avg_prev"].abs())

    df["quarters_available"] = grp.cumcount() + 1
    return df
