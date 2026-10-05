"""早期线索榜：基本面已确认、价格尚未确认（更早，也更险）。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import common as C

scores = C.scores()
if scores.empty:
    st.info("还没有评分数据，请先到「运行与维护」页更新。")
    st.stop()

pool = C.pool(scores)
focus = pool.nlargest(20, "bfs")
early = pool.loc[pool["channel"].astype(str).eq("EARLY")
                 & ~pool["ts_code"].isin(set(focus["ts_code"]))].nlargest(40, "upside_score")

st.caption("入选条件：**FRS ≥ 70 且 PCS < 60**——基本面反转已经出现，但价格还没确认。")

with st.container(horizontal=True):
    st.metric("今日早期线索", f"{len(early)} 只", border=True)
    st.metric("实测大鱼率", "15.8%", border=True, delta="主榜 19.1%", delta_color="inverse")
    st.metric("实测踩雷率", "14.7%", border=True, delta="主榜 10.8%", delta_color="inverse")

if early.empty:
    st.info("今日没有符合条件的早期线索。")
    st.stop()

cols = ["ts_code", "name", "industry", "reversal_type", "upside_score", "downside_score",
        "frs", "lis", "pcs", "rps", "ods", "stage", "action", "kill_count"]
st.dataframe(early[[c for c in cols if c in early.columns]], hide_index=True, height=520,
             column_config={"bfs": st.column_config.NumberColumn(format="%.1f")})

st.download_button("下载早期线索 CSV", early.to_csv(index=False).encode("utf-8-sig"),
                   file_name="bigfish_early.csv", mime="text/csv")

st.divider()
st.markdown(
    "**怎么用这一榜**：它是「提前量」清单——主要用于把研究日程提前排出来（读财报、查订单、"
    "盯价格确认信号），而不是直接建仓。实测数据显示它的平均回报更低、下行风险更高，"
    "因此**必须与主榜分开对待**。"
)
