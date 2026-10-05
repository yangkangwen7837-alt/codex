"""观察池 Top100：筛选、排序、下载，以及连续在榜与名单变化。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import common as C

scores = C.scores()
if scores.empty:
    st.info("还没有评分数据，请先到「运行与维护」页更新。")
    st.stop()

pool = C.pool(scores)
watch = pool.nlargest(100, "bfs").copy()

history = C.history()
if not history.empty:
    counts = history.groupby("ts_code")["as_of"].nunique()
    watch["streak"] = watch["ts_code"].map(counts).fillna(0).astype(int) + 1
else:
    watch["streak"] = 1

st.caption("观察池是**漏斗上游**（召回优先），不是建议名单；重点榜才是人工深研入口。")

with st.container(horizontal=True):
    industries = st.multiselect("行业", sorted(watch["industry"].dropna().unique()),
                                default=[], placeholder="全部行业")
    channels = st.segmented_control("通道", ["全部", "CORE", "EARLY"], default="全部")
    grades = st.multiselect("评级", sorted(watch["grade"].dropna().unique()), default=[])
    min_ds = st.slider("最低避雷分", 0, 100, 0, step=5,
                       help="避雷分 Q5 踩雷率 9.1% vs Q1 14.0%（docs/11 第 2.2 节）")

view = watch.copy()
if industries:
    view = view.loc[view["industry"].isin(industries)]
if channels and channels != "全部":
    view = view.loc[view["channel"].astype(str).eq(channels)]
if grades:
    view = view.loc[view["grade"].isin(grades)]
view = view.loc[pd.to_numeric(view["downside_score"], errors="coerce").fillna(0) >= min_ds]

cols = ["rank_in_pool", "ts_code", "name", "industry", "reversal_type", "channel",
        "upside_score", "downside_score", "bfs", "frs", "lis", "pcs", "rps", "ods",
        "grade", "stage", "action", "kill_count", "streak"]
view = view.sort_values("bfs", ascending=False).reset_index(drop=True)
view.insert(0, "rank_in_pool", range(1, len(view) + 1))
view = view[[c for c in cols if c in view.columns]]

st.dataframe(
    view, hide_index=True, height=560,
    column_config={
        "upside_score": st.column_config.NumberColumn("抓鱼分", format="%.1f"),
        "downside_score": st.column_config.NumberColumn("避雷分", format="%.1f"),
        "bfs": st.column_config.ProgressColumn("BFS", min_value=0, max_value=100, format="%.1f"),
        "streak": st.column_config.NumberColumn("连续在榜(期)", format="%d"),
    },
)
st.caption(f"当前筛选后 {len(view)} 只 / 观察池共 {len(watch)} 只")
st.download_button("下载当前筛选结果 CSV", view.to_csv(index=False).encode("utf-8-sig"),
                   file_name="bigfish_watchlist_filtered.csv", mime="text/csv")
