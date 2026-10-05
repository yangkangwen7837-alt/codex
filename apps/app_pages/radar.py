"""雷达总览：市场状态 → 行业鱼塘 → 漏斗四个区块 → 风险提示。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import common as C

scores = C.scores()
summary = C.run_summary()
flicker = C.flicker_status()

if scores.empty:
    st.info("还没有评分数据。请到「运行与维护」页点击「立即更新」。")
    st.stop()

pool = C.pool(scores)
watch = pool.nlargest(100, "bfs") if not pool.empty else pool
focus = watch.head(20)
early = pool.loc[pool["channel"].astype(str).eq("EARLY")
                 & ~pool["ts_code"].isin(set(focus["ts_code"]))].nlargest(20, "upside_score")

regime = summary.get("regime", "—")
kill_in_pool = int(pool["kill_level"].isin(["WARNING", "REDUCE", "EXIT"]).sum()) if not pool.empty else 0

with st.container(horizontal=True):
    st.metric("市场状态", str(regime), border=True)
    st.metric("观察池", f"{len(watch)} 只", border=True,
              help="漏斗上游，召回优先（Top100 精度 17.6% / 召回 8.3%）")
    st.metric("重点", f"{len(focus)} 只", border=True, help="人工深研入口")
    st.metric("早期线索", f"{len(early)} 只", border=True, help="基本面已确认、价格尚未确认")
    st.metric("候选池内 Kill", kill_in_pool, border=True,
              delta=None if kill_in_pool == 0 else "需关注", delta_color="inverse")

if flicker.get("days"):
    conclusive = bool(flicker.get("conclusive"))
    st.caption(
        f"观察池跟踪：{flicker['days']} 个交易日；闪烁率 {flicker.get('flicker', 0) * 100:.0f}%"
        f"（回测基准 84%）" + ("" if conclusive else " —— 样本 <20 个交易日，尚不能下结论")
    )

st.divider()

left, right = st.columns([1.05, 1], gap="medium")

with left:
    with st.container(border=True):
        st.subheader("重点 Top20")
        cols = ["ts_code", "name", "industry", "reversal_type", "upside_score",
                "downside_score", "frs", "lis", "pcs", "bfs", "grade", "stage", "action"]
        st.dataframe(focus[[c for c in cols if c in focus.columns]], hide_index=True,
                     height=430)

with right:
    with st.container(border=True):
        st.subheader("观察池行业分布")
        dist = watch["industry"].value_counts().head(12).rename_axis("行业").reset_index(name="只数")
        st.bar_chart(dist, x="行业", y="只数", height=250)
        st.caption("注意力配额不由系统强制：请在表中用 ind_seq / type_seq 自行把握。")

    with st.container(border=True):
        st.subheader("行业机会密度 Top5")
        ponds = C.industry_ods()
        if not ponds.empty:
            top = ponds.nlargest(5, "ods")[["industry_name", "ods", "zone"]]
            st.dataframe(top, hide_index=True)

st.divider()

with st.container(border=True):
    st.subheader("早期线索榜（更早，也更险）")
    if early.empty:
        st.caption("今日无符合条件的早期线索。")
    else:
        cols = ["ts_code", "name", "industry", "reversal_type", "frs", "lis", "pcs",
                "upside_score", "downside_score", "stage"]
        st.dataframe(early[[c for c in cols if c in early.columns]], hide_index=True, height=280)
        st.caption("实测大鱼率 15.8%（主榜 19.1%）、踩雷率 14.7%（主榜 10.8%）→ 只作研究提前量。")

st.divider()
with st.container(border=True):
    st.subheader("今日 Big Fish 卡片（Top5）")
    cards = C.cards_text()
    if cards:
        with st.expander("展开完整卡片", expanded=False):
            st.markdown(cards)
    else:
        st.caption("暂无卡片。")

with st.expander("今日简报原文（daily_brief）", expanded=False):
    brief = C.daily_brief_text()
    st.markdown(brief if brief else "_暂无_")
