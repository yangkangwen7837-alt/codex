"""运行与维护：数据更新时间、手动更新、自动更新开关、运行历史、闪烁率跟踪。"""
from __future__ import annotations

import time

import pandas as pd
import streamlit as st

import common as C

status = C.update_status()
schedule = C.schedule_status()
settings = C.update_settings()
flicker = C.flicker_status()
summary = C.run_summary()

state = status.get("state", "idle")
icon = {"idle": "⚪", "running": "🔵", "done": "🟢", "failed": "🔴"}.get(state, "⚪")
st.subheader(f"{icon} 当前状态：{status.get('message', '—')}")

with st.container(horizontal=True):
    st.metric("数据基准日", C.latest_trade_date(), border=True)
    st.metric("本次耗时", f"{status.get('seconds', '—')} 秒", border=True)
    st.metric("调度器", "运行中" if schedule.get("running") else "未启动", border=True)
    st.metric("自动更新", "开启" if settings.get("auto_enabled") else "关闭", border=True)

st.divider()

left, right = st.columns([1, 1], gap="medium")

with left:
    with st.container(border=True):
        st.subheader("手动更新")
        if C.is_cloud():
            st.info("云端只读部署：抓数与跑批由本地或常驻进程执行后随代码发布，此处不触发更新。")
        else:
            steps = st.multiselect(
                "执行哪些步骤", ["fetch", "daily", "flicker"],
                default=settings.get("steps", ["fetch", "daily", "flicker"]),
                format_func=lambda s: {"fetch": "增量抓数", "daily": "全链路跑批",
                                       "flicker": "观察池闪烁率跟踪"}[s])
            if st.button("立即更新", type="primary", disabled=(state == "running")):
                # 先把状态置为 running，页面立即反馈，再执行
                with st.status("更新进行中…", expanded=True) as box:
                    st.write("正在执行：" + "、".join(steps))
                    result = C.run_update(trigger="manual")
                    if result.get("state") == "blocked":
                        st.write(result.get("message"))
                        box.update(label="云端只读，未执行更新", state="error")
                    elif result.get("state") == "failed":
                        st.write(result.get("message"))
                        box.update(label="更新失败", state="error")
                    else:
                        for step in result.get("steps", []):
                            st.write(f"✓ {step['label']}：{step.get('detail', '')}")
                        box.update(label="更新完成", state="complete")
                st.rerun(scope="app")
            st.caption("更新在后台按顺序执行；页面会在数据集变化后自动刷新。")

    with st.container(border=True):
        st.subheader("自动更新")
        auto = st.toggle("启用定时更新（盘前 08:30 / 收盘 16:30）",
                         value=bool(settings.get("auto_enabled", True)),
                         disabled=C.is_cloud())
        if not C.is_cloud() and auto != bool(settings.get("auto_enabled", True)):
            C.set_update_settings(auto_enabled=auto)
            st.rerun(scope="app")
        if C.is_cloud():
            st.caption("云端只读部署不运行调度器；定时抓数请在本地或常驻进程开启。")
        elif schedule.get("jobs"):
            st.dataframe(pd.DataFrame(schedule["jobs"]).rename(
                columns={"name": "任务", "next_run": "下次执行"}), hide_index=True)
        else:
            st.caption("调度器未启动（独立部署时可用 `python scripts/run_scheduler.py` 常驻）。")
        st.caption(f"当前时间：{schedule.get('now', '—')}")

with right:
    with st.container(border=True):
        st.subheader("观察池闪烁率跟踪")
        if flicker.get("days"):
            with st.container(horizontal=True):
                st.metric("样本交易日", flicker["days"], border=True)
                st.metric("闪烁率", f"{flicker.get('flicker', 0) * 100:.0f}%", border=True,
                          delta=f"基准 {flicker.get('baseline', 0.84) * 100:.0f}%",
                          delta_color="inverse")
                overlap = flicker.get("overlap")
                st.metric("相邻两日重叠", "—" if overlap is None else f"{overlap * 100:.0f}%",
                          border=True)
            if not flicker.get("conclusive", False):
                st.warning(f"样本 {flicker['days']} 个交易日 < 20，**尚不能下结论**。"
                           "自动更新会持续累积，达标后会在对话里通知一次。")
            else:
                st.success("样本已达标，可判断闪烁率是否下降。")
            st.caption("对照：回测月度口径的闪烁率是 84%（docs/11）。")
        else:
            st.caption("还没有观察池历史。")

    with st.container(border=True):
        st.subheader("今日跑批摘要")
        if summary:
            with st.container(horizontal=True):
                st.metric("市场", str(summary.get("regime", "—")), border=True)
                st.metric("观察池", summary.get("watchlist_rows", "—"), border=True)
                st.metric("重点", summary.get("ranking_rows", "—"), border=True)
                st.metric("早期线索", summary.get("early_rows", "—"), border=True)
            st.caption(f"评分覆盖 {summary.get('scored', '—')} 只；"
                       f"候选池内 Kill {summary.get('kill_alerts_in_pool', '—')} 只")
        else:
            st.caption("暂无摘要。")

st.divider()
with st.container(border=True):
    st.subheader("更新历史")
    rows = C.update_history()
    if rows:
        hist = pd.DataFrame(rows).rename(columns={
            "state": "状态", "trigger": "触发", "started_at": "开始",
            "finished_at": "结束", "seconds": "耗时(秒)", "message": "说明"})
        st.dataframe(hist, hide_index=True, height=280)
    else:
        st.caption("还没有运行记录。")

with st.container(border=True):
    st.subheader("数据版本")
    snapshot = C.data_version().split("|")
    st.dataframe(pd.DataFrame({"数据集": snapshot}), hide_index=True)
    st.caption("数据集一变，所有页面会自动清缓存并整页刷新（无需手动 F5）。")
