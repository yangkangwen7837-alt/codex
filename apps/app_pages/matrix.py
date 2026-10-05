"""FRS × PCS 四象限：基本面强度 vs 价格确认，点大小=估值空间。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import common as C

scores = C.scores()
if scores.empty:
    st.info("还没有评分数据，请先到「运行与维护」页更新。")
    st.stop()

view = C.pool(scores).dropna(subset=["frs", "pcs"]).copy()
view["rps_plot"] = pd.to_numeric(view["rps"], errors="coerce").fillna(10).clip(lower=5)
view["size"] = 8 + view["rps_plot"] / 4

st.caption("X 轴 = PCS 价格确认，Y 轴 = FRS 基本面反转，圆点大小 = RPS 估值空间，"
           "颜色 = 评级。参考线：FRS 70 / PCS 65（规格书第 35 节的四象限边界）。")

with st.container(horizontal=True):
    quadrant = st.segmented_control(
        "象限", ["全部", "BUY ZONE", "WAIT", "SPECULATION"], default="全部")
    min_frs = st.slider("最低 FRS", 0, 100, 0, step=5)
    min_pcs = st.slider("最低 PCS", 0, 100, 0, step=5)

plot = view
if quadrant and quadrant != "全部":
    plot = plot.loc[plot["quadrant"].astype(str).eq(quadrant)]
plot = plot.loc[(plot["frs"] >= min_frs) & (plot["pcs"] >= min_pcs)]

with st.container(border=True):
    st.scatter_chart(plot, x="pcs", y="frs", size="size", color="quadrant", height=520)
    st.caption(f"图上 {len(plot)} 只（已按可入池筛选）")

left, right = st.columns(2, gap="medium")
with left:
    with st.container(border=True):
        st.subheader("象限分布")
        counts = view["quadrant"].value_counts().rename_axis("象限").reset_index(name="只数")
        st.dataframe(counts, hide_index=True)
        st.caption("BUY ZONE：基本面与价格双确认；WAIT：基本面好但价格未确认（= 早期线索池）；"
                   "SPECULATION：只有价格、没有基本面 → 不进主池。")

with right:
    with st.container(border=True):
        st.subheader("BUY ZONE 明细")
        zone = view.loc[view["quadrant"].astype(str).eq("BUY ZONE")].nlargest(30, "bfs")
        st.dataframe(zone[["ts_code", "name", "industry", "frs", "pcs", "rps", "bfs",
                           "grade", "action"]], hide_index=True, height=320)
