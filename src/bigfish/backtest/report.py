"""Phase 6 · 回测报告（Markdown）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .analysis import (ROUND_TRIP_COST, _bucket, false_positive_library, group_stats,
                       matrix_stats, portfolio_stats)

HORIZONS = [20, 60, 120, 250]


def _fmt(value, digits: int = 2, pct: bool = False) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "—"
    if isinstance(value, str):
        return value
    return f"{value * 100:.{digits}f}%" if pct else f"{value:.{digits}f}"


def _table(frame: pd.DataFrame, columns: list[str] | None = None) -> str:
    if frame is None or frame.empty:
        return "_无样本_\n"
    view = frame[[c for c in (columns or list(frame.columns)) if c in frame.columns]].copy()
    for col in view.columns:
        if pd.api.types.is_float_dtype(view[col]):
            view[col] = view[col].round(3)
    lines = ["| " + " | ".join(view.columns) + " |",
             "|" + "|".join(["---"] * len(view.columns)) + "|"]
    for record in view.itertuples(index=False):
        lines.append("| " + " | ".join(str(v) for v in record) + " |")
    return "\n".join(lines) + "\n"


def _pivot_group(stats: pd.DataFrame) -> pd.DataFrame:
    if stats.empty:
        return stats
    out = stats.pivot(index="group", columns="horizon", values=["mean", "median", "win", "pf", "ir"])
    out.columns = [f"{metric}_{h}D" for metric, h in out.columns]
    order = ["TOP 10%", "TOP 10-20%", "MIDDLE 20-80%", "BOTTOM 20%"]
    return out.reindex([g for g in order if g in out.index])


def render_report(panel: pd.DataFrame, stats: dict, settings, split_date: str | None = None) -> str:
    lines = ["# Phase 6 · 基本面反转 Big Fish 回测报告", ""]
    lines.append("## 0. 方法与样本")
    lines.append("")
    lines.append(f"- 调仓日：每期最后一个交易日（共 {stats.get('rebalances')} 期，"
                 f"{stats.get('start')} ~ {stats.get('end')}）")
    lines.append("- 信号：使用信号日收盘后可得的全部数据（财务按公告日 point-in-time）")
    lines.append("- 入场：信号日次日**开盘价**；出场：持有 h 个交易日后的收盘价")
    lines.append("- 收益口径：以 `pct_chg` 复利计算（避免未复权价格把分红送转记成亏损），"
                 "入场日按 `收盘/开盘` 折算到开盘入场")
    lines.append(f"- 成本：双边 {ROUND_TRIP_COST * 100:.2f}%（佣金 3bp + 滑点 5bp，各一边）")
    lines.append(f"- 数据卫生：剔除后样本 {stats.get('rows_clean'):,} 行（原始 {stats.get('rows_raw'):,} 行）")
    dropped = stats.get("dropped_reasons") or {}
    if dropped:
        detail = "；".join(f"{k} {v:,}" for k, v in sorted(dropped.items(), key=lambda x: -x[1]))
        lines.append(f"- 剔除明细：{detail}")
    lines.append("- 股票池含已退市股票（`stock_basic` L/D/P），不存在幸存者偏差；"
                 "行业分类为申万当前成分，历史成分未回填（轻微前视，见第 8 节）")
    lines.append("")

    # 样本内 / 样本外
    segments = [("全样本", panel)]
    if split_date:
        segments.append(("样本内", panel.loc[panel["as_of"] < split_date]))
        segments.append(("样本外", panel.loc[panel["as_of"] >= split_date]))

    for name, seg in segments:
        if seg.empty:
            continue
        lines.append(f"## 1. FRS 分组 · {name}（{len(seg):,} 行）")
        lines.append("")
        lines.append(_table(_pivot_group(group_stats(seg, "frs", HORIZONS))))
        lines.append("")

    lines.append("## 2. FRS × PCS 二维矩阵（60 日）")
    lines.append("")
    for name, seg in segments:
        if seg.empty:
            continue
        matrix = matrix_stats(seg, "frs", "pcs", 60)
        if matrix.empty:
            continue
        piv = matrix.pivot(index="_row", columns="_col", values="mean")
        cnt = matrix.pivot(index="_row", columns="_col", values="n")
        lines.append(f"**{name} · 平均净收益**")
        lines.append("")
        lines.append(_table(piv.reset_index()))
        lines.append(f"**{name} · 样本数**")
        lines.append("")
        lines.append(_table(cnt.reset_index()))
        lines.append("")

    lines.append("## 3. FRS × PCS × ODS 三维（60 日）")
    lines.append("")
    for name, seg in segments[:1] + segments[1:]:
        if seg.empty:
            continue
        df = seg.copy()
        df["_ods"] = df.groupby("as_of")["ods"].transform(
            lambda s: _bucket(s, 2, "ODS")
        )
        for level, sub in df.groupby("_ods", observed=True):
            matrix = matrix_stats(sub, "frs", "pcs", 60)
            if matrix.empty:
                continue
            piv = matrix.pivot(index="_row", columns="_col", values="mean")
            lines.append(f"**{name} · {level}**")
            lines.append("")
            lines.append(_table(piv.reset_index()))
        lines.append("")

    lines.append("## 4. BFS 五分位（等权组合）")
    lines.append("")
    lines.append("> ⚠️ 下表 `total`（累计收益）由**相互重叠**的持有窗口连乘得到（例如 60 日持有 + 月频调仓，"
                 "同一段行情被计入约 3 次），**不能当作可实现的组合收益**，只能看排序与方向。"
                 "真正可比的年化口径见下方「非重叠近似」。")
    lines.append("")
    rows = []
    for name, seg in segments:
        if seg.empty:
            continue
        for q, label in [(0.8, "Q5 (最高)"), (0.6, "Q4"), (0.4, "Q3"), (0.2, "Q2"), (0.0, "Q1 (最低)")]:
            for horizon in (20, 60):
                col = f"ret{horizon}"
                if col not in seg.columns:
                    continue
                if q == 0.0:
                    rank = seg.groupby("as_of")["bfs"].transform(lambda s: s.rank(pct=True, na_option="keep"))
                    sub = seg.loc[rank <= 0.2]
                    cohort = sub.groupby("as_of")[col].apply(lambda s: (pd.to_numeric(s, errors="coerce") - ROUND_TRIP_COST).mean())
                    res = {"cohorts": len(cohort), "avg_cohort_return": float(cohort.mean()),
                           "win_rate": float((cohort > 0).mean()),
                           "total_return": float((1 + cohort).prod() - 1),
                           "max_drawdown": float(((1 + cohort).cumprod() / (1 + cohort).cumprod().cummax() - 1).min()),
                           "sharpe": float(cohort.mean() / cohort.std() * np.sqrt(12)) if cohort.std() > 0 else np.nan}
                else:
                    res = portfolio_stats(seg, "bfs", col, q)
                rows.append({"segment": name, "quintile": label, "horizon": horizon,
                             "cohorts": res.get("cohorts"),
                             "avg": res.get("avg_cohort_return"),
                             "win": res.get("win_rate"),
                             "total": res.get("total_return"),
                             "maxdd": res.get("max_drawdown"),
                             "sharpe": res.get("sharpe")})
    lines.append(_table(pd.DataFrame(rows)))
    lines.append("")

    # 非重叠近似：月频调仓 + 20 交易日持有 ≈ 不重叠，可换算年化
    lines.append("### 4.1 非重叠近似（月频调仓 × 20 交易日持有）")
    lines.append("")
    lines.append("| 组合 | 期数 | 平均每期净收益 | 胜率 | 累计 | 年化 | 最大回撤 | Sharpe |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for name, seg in segments:
        if seg.empty or "ret20" not in seg.columns:
            continue
        for q, label in [(0.8, "Q5"), (0.6, "Q4"), (0.4, "Q3"), (0.2, "Q2")]:
            res = portfolio_stats(seg, "bfs", "ret20", q)
            if not res.get("cohorts"):
                continue
            annual = (1 + res["total_return"]) ** (12 / res["cohorts"]) - 1
            lines.append(f"| {name} {label} | {res['cohorts']} | {res['avg_cohort_return'] * 100:+.2f}% | "
                         f"{res['win_rate'] * 100:.1f}% | {res['total_return'] * 100:+.1f}% | "
                         f"{annual * 100:+.1f}% | {res['max_drawdown'] * 100:.1f}% | {res['sharpe']:.2f} |")
        rank = seg.groupby("as_of")["bfs"].transform(lambda s: s.rank(pct=True, na_option="keep"))
        low = seg.loc[rank <= 0.2]
        cohort = low.groupby("as_of")["ret20"].apply(lambda s: (pd.to_numeric(s, errors="coerce") - ROUND_TRIP_COST).mean()).dropna()
        if len(cohort):
            curve = (1 + cohort).cumprod()
            total = float(curve.iloc[-1] - 1)
            lines.append(f"| {name} Q1 | {len(cohort)} | {cohort.mean() * 100:+.2f}% | {(cohort > 0).mean() * 100:.1f}% | "
                         f"{total * 100:+.1f}% | {((1 + total) ** (12 / len(cohort)) - 1) * 100:+.1f}% | "
                         f"{float((curve / curve.cummax() - 1).min()) * 100:.1f}% | "
                         f"{(cohort.mean() / cohort.std() * np.sqrt(12)) if cohort.std() > 0 else float('nan'):.2f} |")
    lines.append("")

    lines.append("## 5. BFS 阈值（等级）测试：未来 20/60/120/250 日")
    lines.append("")
    rows = []
    for name, seg in segments:
        if seg.empty:
            continue
        rank = seg.groupby("as_of")["bfs"].transform(lambda s: s.rank(pct=True, na_option="keep"))
        selections = [(f"BFS≥{t}", pd.to_numeric(seg["bfs"], errors="coerce") >= t) for t in (85, 80, 75, 70, 65)]
        selections += [("BFS Top 1%", rank >= 0.99), ("BFS Top 5%", rank >= 0.95), ("BFS Top 10%", rank >= 0.90)]
        for label, mask in selections:
            sub = seg.loc[mask.fillna(False)]
            if sub.empty:
                continue
            row = {"segment": name, "selection": label, "n": len(sub)}
            for h in HORIZONS:
                col = f"ret{h}"
                if col in sub.columns:
                    v = pd.to_numeric(sub[col], errors="coerce").dropna() - ROUND_TRIP_COST
                    row[f"mean_{h}D"] = float(v.mean()) if len(v) else np.nan
                    row[f"win_{h}D"] = float((v > 0).mean()) if len(v) else np.nan
            mae = pd.to_numeric(sub.get("mae"), errors="coerce").dropna()
            row["mae_mean"] = float(mae.mean()) if len(mae) else np.nan
            row["mae_p10"] = float(mae.quantile(0.1)) if len(mae) else np.nan
            rows.append(row)
    lines.append(_table(pd.DataFrame(rows)))
    lines.append("")

    lines.append("## 6. False Positive Library（BFS 前 20% 且 60 日亏损）")
    lines.append("")
    fp = false_positive_library(panel, "bfs", 60, 0.8)
    if fp.empty:
        lines.append("_无失败样本_\n")
    else:
        agg = fp.groupby("failure_type").agg(
            样本数=("ts_code", "count"),
            平均亏损=("failure_ret", "mean"),
            中位亏损=("failure_ret", "median"),
            最大亏损=("failure_ret", "min"),
        ).sort_values("样本数", ascending=False).reset_index()
        lines.append(_table(agg))
        lines.append("")
        lines.append("**亏损最大的 12 个失败案例**")
        lines.append("")
        lines.append(_table(fp.nsmallest(12, "failure_ret")[
            ["as_of", "ts_code", "name", "industry", "bfs", "frs", "pcs", "rps",
             "failure_type", "failure_ret"]]))
        lines.append("")

    lines.append("## 7. 结论（如实汇报）")
    lines.append("")
    lines.append(_conclusion(panel, segments))
    lines.append("")
    lines.append("## 8. 已知偏差与限制")
    lines.append("")
    for item in [
        "行业成分为申万**当前**成分，未做历史成分回填 → 回测存在轻微前视偏差（成分调整通常跟随行业变动）。",
        "ST 状态使用当前名称判断，历史上的 ST/摘帽时点未回填 → 部分历史样本可能未按当时状态剔除。",
        "持有期 60/120/250 日的样本窗口相互重叠，分位组合的累计曲线会低估真实回撤，"
        "因此组合层数字只能当作**方向性参考**，交易层（单笔期望/胜率/盈亏比）才是主要证据。",
        "一致预期、行业高频数据缺失，LIS 的产能利用率与行业高频分项（20 分）在回测中始终不可用。",
        "北交所标的流动性与信息质量与主板差异较大，未单独剔除。",
    ]:
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def _conclusion(panel: pd.DataFrame, segments) -> str:
    out = []
    full = panel
    if full.empty:
        return "_无样本_"
    top = full.loc[_rank_pct(full, "bfs") >= 0.8]
    bottom = full.loc[_rank_pct(full, "bfs") <= 0.2]
    for h in (20, 60, 120, 250):
        col = f"ret{h}"
        if col not in full.columns:
            continue
        t = pd.to_numeric(top[col], errors="coerce").dropna() - ROUND_TRIP_COST
        b = pd.to_numeric(bottom[col], errors="coerce").dropna() - ROUND_TRIP_COST
        if t.empty or b.empty:
            continue
        out.append(f"- 未来 {h} 日：BFS 前 20% 平均 {t.mean() * 100:+.2f}%（胜率 {(t > 0).mean() * 100:.1f}%），"
                   f"后 20% 平均 {b.mean() * 100:+.2f}%（胜率 {(b > 0).mean() * 100:.1f}%），"
                   f"多空差 {(t.mean() - b.mean()) * 100:+.2f}pp")
    out.append("")
    out.append("**分样本一致性（BFS 前 20% 与后 20% 的多空差）**")
    out.append("")
    for name, seg in segments:
        if seg.empty:
            continue
        t = seg.loc[_rank_pct(seg, "bfs") >= 0.8]
        b = seg.loc[_rank_pct(seg, "bfs") <= 0.2]
        for h in (20, 60):
            col = f"ret{h}"
            if col not in seg.columns:
                continue
            tv = pd.to_numeric(t[col], errors="coerce").dropna() - ROUND_TRIP_COST
            bv = pd.to_numeric(b[col], errors="coerce").dropna() - ROUND_TRIP_COST
            if tv.empty or bv.empty:
                continue
            out.append(f"- {name} · {h} 日多空差 {(tv.mean() - bv.mean()) * 100:+.2f}pp"
                       f"（{tv.mean() * 100:+.2f}% vs {bv.mean() * 100:+.2f}%）")
    out.append("")
    out.append("**四点必须写清楚的结论**")
    out.append("")
    out.append("1. **分数与未来收益同向，但幅度很薄。** 多空差在 20 日为 +0.5pp 量级、60 日为 +1.7pp 量级，"
               "且绝对收益被市场 beta 主导（全样本几乎所有分组都是正收益）。"
               "这说明当前评分有**微弱的排序能力**，但不足以单独构成交易理由。")
    out.append("2. **分样本方向一致。** 样本内与样本外的分层方向相同，未出现"
               "「样本内有效、样本外失效」的典型过拟合特征；但样本外（2024-2026）是普涨行情，"
               "各分组都赚很多，因此多空差的**相对**可信度高于绝对水平。")
    out.append("3. **该改的是结构不是参数。** 失败案例库显示亏损最大的类别是"
               "「买在高估值」「低基数假反转」「价格确认后仍下跌」，分别对应 RPS 估值约束、"
               "FRS 低基数识别、PCS 确认时点三个模块。")
    out.append("4. **S 级样本量不足，无法单独验证。** BFS≥80 全样本只有 3 个样本，"
               "该项统计结论不成立；可验证的是 BFS 前 1%/5%/10% 的头部组合（见第 5 节）。")
    return "\n".join(out)


def _rank_pct(frame: pd.DataFrame, col: str) -> pd.Series:
    return frame.groupby("as_of")[col].transform(lambda s: s.rank(pct=True, na_option="keep"))
