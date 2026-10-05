"""基本面趋势：选定候选股票，看最近 12 个季度的经营趋势。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import common as C

scores = C.scores()
trend = C.trend()
if scores.empty:
    st.info("还没有评分数据，请先到「运行与维护」页更新。")
    st.stop()
if trend.empty:
    st.warning("还没有财务趋势数据（fundamental_trend_latest.parquet），请重新跑一次批量更新。")
    st.stop()

pool = C.pool(scores)
candidates = pool.nlargest(100, "bfs")[["ts_code", "name", "industry"]].copy()
candidates["label"] = candidates["ts_code"] + " " + candidates["name"].astype(str)

default = candidates["label"].head(1).tolist()
labels = candidates["label"].tolist()
picked = st.multiselect("选择股票（观察池 Top100）", labels, default=default, max_selections=6)
if not picked:
    st.info("请至少选择一只股票。")
    st.stop()

codes = [candidates.loc[candidates["label"].eq(p), "ts_code"].iloc[0] for p in picked]
sub = trend.loc[trend["ts_code"].isin(codes)].sort_values(["ts_code", "end_date"]).copy()

left, right = st.columns(2, gap="medium")
with left:
    with st.container(border=True):
        st.subheader("单季营业收入（亿元）")
        st.line_chart(sub, x="end_date", y="revenue", color="ts_code", height=300)
    with st.container(border=True):
        st.subheader("单季毛利率（%）")
        margin = sub.assign(gross_margin=pd.to_numeric(sub["gross_margin"], errors="coerce") * 100)
        st.line_chart(margin, x="end_date", y="gross_margin", color="ts_code", height=300)

with right:
    with st.container(border=True):
        st.subheader("单季归母净利润（亿元）")
        st.line_chart(sub, x="end_date", y="n_income_attr_p", color="ts_code", height=300)
    with st.container(border=True):
        st.subheader("单季经营现金流（亿元）")
        st.line_chart(sub, x="end_date", y="n_cashflow_act", color="ts_code", height=300)

st.divider()
with st.container(border=True):
    st.subheader("关键同比指标（最新报告期）")
    cols = ["ts_code", "end_date", "ann_date", "revenue_yoy", "np_yoy", "ttm_revenue_yoy",
            "gross_margin_chg_yoy", "inventory_to_revenue", "receivable_yoy",
            "contract_liab_yoy", "debt_to_assets", "roe_ytd"]
    latest = sub.sort_values("end_date").groupby("ts_code").tail(1)
    show = latest[[c for c in cols if c in latest.columns]].copy()
    for col in ("revenue_yoy", "np_yoy", "ttm_revenue_yoy", "gross_margin_chg_yoy",
                "receivable_yoy", "contract_liab_yoy"):
        if col in show.columns:
            show[col] = pd.to_numeric(show[col], errors="coerce") * 100
    st.dataframe(
        show, hide_index=True,
        column_config={c: st.column_config.NumberColumn(c, format="%.1f%%")
                       for c in ("revenue_yoy", "np_yoy", "ttm_revenue_yoy",
                                 "gross_margin_chg_yoy", "receivable_yoy", "contract_liab_yoy")
                       if c in show.columns},
    )
    st.caption("数据按公告日（point-in-time）截取；金额单位已换算为亿元。")
