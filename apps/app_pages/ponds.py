"""行业鱼塘：ODS 排名与六个分项拆解。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import common as C

ods = C.industry_ods()
if ods.empty:
    st.info("还没有行业 ODS 数据，请先到「运行与维护」页更新。")
    st.stop()

ods = ods.sort_values("ods", ascending=False).reset_index(drop=True)
ods.insert(0, "排名", range(1, len(ods) + 1))

top_n = st.slider("显示前 N 个行业", 5, len(ods), min(15, len(ods)))
view = ods.head(top_n).copy()

with st.container(border=True):
    st.subheader("ODS 排名")
    st.bar_chart(view, x="industry_name", y="ods", height=320)

component_cols = [c for c in view.columns if c.startswith("ods_")]
labels = {
    "ods_fundamental_trend": "基本面趋势", "ods_capital_trend": "资金趋势",
    "ods_earnings_revision": "盈利预期", "ods_price_inventory_cycle": "价格/库存周期",
    "ods_valuation_position": "估值位置", "ods_policy_catalyst": "政策/催化",
}

left, right = st.columns([1.15, 1], gap="medium")
with left:
    with st.container(border=True):
        st.subheader("分项拆解")
        show = view[["排名", "industry_name", "ods", "zone", "coverage"] + component_cols].rename(
            columns={"industry_name": "行业", "ods": "ODS", "zone": "区域", "coverage": "覆盖率",
                     **labels})
        st.dataframe(show, hide_index=True, height=420)

with right:
    with st.container(border=True):
        st.subheader("选中行业的分项对比")
        picked = st.multiselect("行业", view["industry_name"].tolist(),
                                default=view["industry_name"].head(5).tolist())
        if picked:
            sub = view.loc[view["industry_name"].isin(picked)]
            melted = sub.melt(id_vars="industry_name", value_vars=component_cols,
                              var_name="分项", value_name="得分")
            melted["分项"] = melted["分项"].map(labels).fillna(melted["分项"])
            st.bar_chart(melted, x="分项", y="得分", color="industry_name", height=360,
                         stack=False)

st.caption("ODS = 30% 行业基本面趋势 + 20% 资金趋势 + 20% 盈利预期 + 15% 价格/库存周期 "
           "+ 10% 估值位置 + 5% 政策催化；缺失分项不填充，按可用项重新归一化。")
st.download_button("下载行业 ODS 明细 CSV", ods.to_csv(index=False).encode("utf-8-sig"),
                   file_name="industry_ods.csv", mime="text/csv")
