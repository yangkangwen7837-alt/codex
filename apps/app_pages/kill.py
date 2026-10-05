"""Kill 监控：候选池内与全市场的风险信号。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import common as C

scores = C.scores()
if scores.empty:
    st.info("还没有评分数据，请先到「运行与维护」页更新。")
    st.stop()

pool = C.pool(scores)
in_pool = pool.loc[pool["kill_level"].isin(["WARNING", "REDUCE", "EXIT"])]
market = scores.loc[scores["kill_level"].isin(["WARNING", "REDUCE", "EXIT"])]

with st.container(horizontal=True):
    st.metric("候选池内预警", f"{len(in_pool)} 只", border=True)
    st.metric("其中 REDUCE/EXIT", int(in_pool["kill_level"].isin(["REDUCE", "EXIT"]).sum()),
              border=True)
    st.metric("全市场预警", f"{len(market)} 只", border=True,
              help="弱市里全市场 Kill 分布本来就很热（约 4,900 只），只能看候选池内的口径")

st.divider()
level = st.segmented_control("级别", ["全部", "WARNING", "REDUCE", "EXIT"], default="全部")
view = in_pool if level == "全部" else in_pool.loc[in_pool["kill_level"].eq(level)]

with st.container(border=True):
    st.subheader("候选池内 Kill 明细")
    if view.empty:
        st.caption("候选池内没有该级别的预警。")
    else:
        cols = ["ts_code", "name", "industry", "kill_level", "kill_count", "bfs",
                "frs", "pcs", "action", "kill_reasons"]
        st.dataframe(view[[c for c in cols if c in view.columns]].sort_values(
            ["kill_count", "bfs"], ascending=[False, False]), hide_index=True, height=460)

with st.container(border=True):
    st.subheader("Kill 类型分布（候选池内）")
    if not in_pool.empty:
        fields = {"kill_fundamental": "基本面", "kill_technical": "技术面", "kill_thesis": "逻辑面"}
        dist = pd.DataFrame({
            label: (pd.to_numeric(in_pool[col], errors="coerce").fillna(0) > 0).sum()
            for col, label in fields.items() if col in in_pool.columns
        }, index=["命中只数"]).T.reset_index(names="类别")
        st.bar_chart(dist, x="类别", y="命中只数", height=260)
        st.caption("1 个信号 = WARNING，2 个 = REDUCE，≥3 个 = EXIT（规格书第 48 节）。")
    else:
        st.caption("无预警。")
