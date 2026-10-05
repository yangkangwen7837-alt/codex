"""基本面反转 Big Fish Agent · 看板入口。

启动：streamlit run apps/streamlit_app.py
自动更新：进程内启动 APScheduler（盘前 08:30 / 收盘 16:30，可在「运行与维护」页开关），
页面按数据集版本自动刷新。
"""
from __future__ import annotations

import os

import streamlit as st

import common as C

st.set_page_config(
    page_title="基本面反转 Big Fish Agent",
    page_icon=":material/search_insights:",
    layout="wide",
)


@st.cache_resource(show_spinner=False)
def _boot() -> dict:
    """进程内只启动一次调度器（由 start_site.py 启动时不重复起）。"""
    from bigfish.config import is_streamlit_cloud
    from bigfish.update import start_scheduler

    if os.environ.get("BIGFISH_DISABLE_SCHEDULER") == "1":
        return {"started": False, "reason": "由启动脚本负责调度"}
    if is_streamlit_cloud():
        # Streamlit Community Cloud 是只读部署：页面只展示数据，抓数/跑批在本地或常驻进程完成。
        return {"started": False, "reason": "云端只读部署：调度器关闭"}
    start_scheduler()
    return {"started": True}


_boot()

page = st.navigation(
    {
        "查看": [
            st.Page("app_pages/radar.py", title="雷达总览", icon=":material/radar:", default=True),
            st.Page("app_pages/watchlist.py", title="观察池 Top100", icon=":material/list_alt:"),
            st.Page("app_pages/early.py", title="早期线索榜", icon=":material/schedule:"),
            st.Page("app_pages/ponds.py", title="行业鱼塘", icon=":material/waves:"),
        ],
        "分析": [
            st.Page("app_pages/matrix.py", title="FRS × PCS 四象限", icon=":material/scatter_plot:"),
            st.Page("app_pages/fundamental.py", title="基本面趋势", icon=":material/trending_up:"),
            st.Page("app_pages/kill.py", title="Kill 监控", icon=":material/warning:"),
        ],
        "运行与维护": [
            st.Page("app_pages/ops.py", title="数据更新与状态", icon=":material/settings:"),
        ],
    },
    position="sidebar",
)

with st.sidebar:
    st.caption(f"数据基准日：**{C.latest_trade_date()}**")
    if C.USING_SNAPSHOT:
        st.caption(":grey[数据来源：随仓库发布的最新一期快照（只读）]")
    status = C.update_status()
    state = status.get("state", "idle")
    label = {"idle": ":grey[尚未更新]", "running": ":blue[更新中]",
             "done": ":green[更新完成]", "failed": ":red[更新失败]"}.get(state, state)
    st.caption(f"最近更新：{label}")
    if status.get("finished_at"):
        st.caption(f"完成于 {status['finished_at']}")
    if status.get("state") == "failed":
        st.caption(f"{(status.get('message') or '')[:120]}")

st.title(page.title, icon=page.icon)


@st.fragment(run_every=int(C.settings().path("site.refresh_seconds", 60)))
def _watch_dataset() -> None:
    """数据集一变（跑批完成）就整页刷新。"""
    version = C.data_version()
    if st.session_state.get("_dataset_version") != version:
        st.session_state["_dataset_version"] = version
        st.cache_data.clear()
        st.rerun(scope="app")


_watch_dataset()
page.run()
