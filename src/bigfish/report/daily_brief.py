"""每日输出（规格书第 91 节）。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _table(frame: pd.DataFrame, columns: list[str]) -> str:
    if frame is None or frame.empty:
        return "_无_\n"
    cols = [c for c in columns if c in frame.columns]
    view = frame[cols].copy()
    for col in view.columns:
        if pd.api.types.is_float_dtype(view[col]):
            view[col] = view[col].round(1)
    header = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join(["---"] * len(cols)) + "|"
    rows = ["| " + " | ".join(str(v) for v in record) + " |" for record in view.itertuples(index=False)]
    return "\n".join([header, sep] + rows) + "\n"


def render_daily_brief(as_of: str, regime: dict, ponds: pd.DataFrame, watchlist: pd.DataFrame,
                       focus: pd.DataFrame, early: pd.DataFrame | None = None,
                       previous: pd.DataFrame | None = None,
                       kill_frame: pd.DataFrame | None = None) -> str:
    """候选生成器漏斗式日报：观察池 Top100 → 重点 Top20 → 卡片 Top5 + 早期线索榜。"""
    lines = [f"# Big Fish 每日输出 · {as_of}", ""]
    lines.append("> 漏斗：**观察池 Top100（召回优先）→ 重点 Top20（人工深研入口）→ 卡片 Top5（完整论证）**，"
                 "外加**早期线索榜**（基本面已确认、价格尚未确认）。")
    lines.append("")

    lines.append("## MARKET")
    regime_score = regime.get("regime_score")
    score_text = f"{regime_score:.1f}" if isinstance(regime_score, (int, float)) else "DATA INCOMPLETE"
    lines.append(f"- 市场状态：**{regime.get('regime')}**（评分 {score_text}）")
    lines.append(f"- 交易优先级系数：{regime.get('action_priority')}（只影响优先级，不删除基本面候选）")
    for reason in regime.get("reasons", []):
        lines.append(f"- {reason}")
    lines.append("")

    lines.append("## TOP OPPORTUNITY PONDS")
    if ponds is not None and not ponds.empty:
        top = ponds.head(5)[["ods_rank", "industry_name", "ods", "zone"]]
        lines.append(_table(top, list(top.columns)))
    else:
        lines.append("_无_\n")

    # ---------- 区块一：观察池 ----------
    lines.append(f"## ① 观察池 TOP100（共 {0 if watchlist is None else len(watchlist)} 只）")
    if watchlist is not None and not watchlist.empty:
        cols = ["pool_rank", "ts_code", "name", "industry", "channel", "upside_score",
                "downside_score", "bfs", "frs", "lis", "pcs", "streak"]
        lines.append(_table(watchlist.head(30), cols))
        lines.append(f"> 完整 {len(watchlist)} 只见 `bigfish_watchlist_{as_of}.csv`；"
                     "这里只列前 30。观察池是**漏斗上游**，不是建议名单。")
        lines.append("")
        ind = watchlist["industry"].value_counts().head(8)
        lines.append("- 行业分布（前 8）：" + "、".join(f"{k} {v}" for k, v in ind.items()))
        lines.append("")
    else:
        lines.append("_无_\n")

    # ---------- 区块二：重点 ----------
    lines.append("## ② 重点 TOP20（人工深研入口）")
    if focus is not None and not focus.empty:
        cols = ["pool_rank", "ts_code", "name", "industry", "reversal_type", "upside_score",
                "downside_score", "frs", "lis", "pcs", "rps", "bfs", "grade", "stage", "action",
                "streak", "ind_seq", "type_seq"]
        lines.append(_table(focus, [c for c in cols if c in focus.columns]))
    else:
        lines.append("_无_\n")

    # ---------- 区块三：早期线索榜 ----------
    lines.append("## ③ 早期线索榜（FRS 已确认、价格尚未确认）")
    if early is not None and not early.empty:
        cols = ["early_rank", "ts_code", "name", "industry", "reversal_type", "frs", "lis",
                "pcs", "upside_score", "downside_score", "stage", "streak"]
        lines.append(_table(early, [c for c in cols if c in early.columns]))
        lines.append("> 实测这一榜**更早也更险**（250 日踩雷率 14.7% vs 主榜 10.8%），"
                     "只用于研究提前量，不并入主榜。")
        lines.append("")
    else:
        lines.append("_今日无符合条件的早期线索_\n")

    # ---------- 区块四：卡片 ----------
    lines.append("## ④ 完整卡片 TOP5")
    if focus is not None and not focus.empty:
        lines.append("> 见 `bigfish_cards_" + as_of + ".md`："
                     + "、".join(focus.head(5)["name"].astype(str).tolist()))
        lines.append("")

    lines.append("## SUPER CANDIDATES")
    supers = pd.DataFrame()
    if focus is not None and not focus.empty and "special_situation" in focus.columns:
        supers = focus.loc[focus["special_situation"].fillna(False)]
    if supers.empty:
        lines.append("_本日无 SPECIAL SITUATION（行业 ODS 低的公司级独立反转）_\n")
    else:
        lines.append(_table(supers, ["pool_rank", "ts_code", "name", "frs", "lis", "pcs", "bfs", "action"]))

    new_entries, upgrades, downgrades = _diff(focus, previous)
    lines.append("## NEW ENTRIES")
    lines.append(_table(new_entries, ["pool_rank", "ts_code", "name", "industry", "bfs", "grade"]))
    if previous is None or previous.empty:
        lines.append("> 首次运行，没有上一期快照可对比；下一次跑批起此处显示真正的新增候选。\n")
    lines.append("## UPGRADE")
    lines.append(_table(upgrades, ["ts_code", "name", "prev_grade", "grade", "bfs"]))
    lines.append("## DOWNGRADE")
    lines.append(_table(downgrades, ["ts_code", "name", "prev_grade", "grade", "bfs"]))

    lines.append("## KILL ALERT")
    alerts = pd.DataFrame()
    if kill_frame is not None and not kill_frame.empty:
        alerts = kill_frame.loc[kill_frame["kill_level"].isin(["WARNING", "REDUCE", "EXIT"])]
        # 只报候选池内的风险（全市场 Kill 分布见数据质量报告）
        if focus is not None and not focus.empty:
            alerts = alerts.loc[alerts["ts_code"].isin(set(focus["ts_code"]))]
            alerts = alerts.merge(focus[["ts_code", "name", "bfs"]], on="ts_code", how="left")
    lines.append(_table(alerts, ["ts_code", "name", "bfs", "kill_count", "kill_level", "kill_reasons"]))

    lines.append("## ACTION")
    if focus is not None and not focus.empty:
        actionable = focus.loc[focus["action"].isin(
            ["STARTER_POSITION", "WAIT_PULLBACK", "WAIT_TRIGGER", "HOLD", "REDUCE", "EXIT"])]
        lines.append(_table(actionable, ["pool_rank", "ts_code", "name", "action", "entry_ref", "stop_loss",
                                         "target_1", "risk_reward", "pcs", "frs"]))
    else:
        lines.append("_无_\n")
    lines.append("> 本文件由基本面反转 Big Fish Agent 自动生成；动作集合不含「强烈买入」这类措辞，"
                 "所有估值情景均为研究结论而非目标价承诺。")
    return "\n".join(lines)


def _diff(ranking: pd.DataFrame, previous: pd.DataFrame | None):
    empty = pd.DataFrame()
    if ranking is None or ranking.empty:
        return empty, empty, empty
    if previous is None or previous.empty or "grade" not in previous.columns:
        return ranking.head(10), empty, empty
    prev = previous.drop_duplicates("ts_code").set_index("ts_code")
    merged = ranking.merge(prev[["grade", "bfs"]].rename(columns={"grade": "prev_grade", "bfs": "prev_bfs"}),
                           left_on="ts_code", right_index=True, how="left")
    new = merged.loc[merged["prev_grade"].isna()]
    upgraded = merged.loc[merged["prev_grade"].notna() & (merged["bfs"] > merged["prev_bfs"] + 3)]
    upgraded = upgraded.assign(prev_grade=upgraded["prev_grade"])
    downgraded = merged.loc[merged["prev_grade"].notna() & (merged["bfs"] < merged["prev_bfs"] - 3)]
    return new, upgraded, downgraded
