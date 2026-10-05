"""对比两次回测（结构改进前 / 后），输出 docs/07_structural_change_review.md。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bigfish import PROCESSED_DIR, PROJECT_ROOT  # noqa: E402
from bigfish.backtest.analysis import ROUND_TRIP_COST  # noqa: E402
from bigfish.config import load_settings  # noqa: E402


def _net(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce") - ROUND_TRIP_COST


def _rank_pct(frame: pd.DataFrame, col: str) -> pd.Series:
    return frame.groupby("as_of")[col].transform(lambda s: s.rank(pct=True, na_option="keep"))


def _frs_groups(panel: pd.DataFrame, horizon: int = 60) -> dict[str, float]:
    rank = _rank_pct(panel, "frs")
    col = f"ret{horizon}"
    out = {}
    for label, mask in (
        ("top10", rank > 0.90),
        ("top20", (rank > 0.80) & (rank <= 0.90)),
        ("middle", (rank > 0.20) & (rank <= 0.80)),
        ("bottom", rank <= 0.20),
    ):
        out[label] = float(_net(panel.loc[mask, col]).mean())
    out["spread"] = out["top10"] - out["bottom"]
    return out


def _bfs_spread(panel: pd.DataFrame, horizon: int = 60) -> dict[str, float]:
    rank = _rank_pct(panel, "bfs")
    col = f"ret{horizon}"
    top = _net(panel.loc[rank >= 0.8, col]).mean()
    bottom = _net(panel.loc[rank <= 0.2, col]).mean()
    mid = _net(panel.loc[(rank > 0.4) & (rank <= 0.6), col]).mean()
    return {"q5": float(top), "q1": float(bottom), "mid": float(mid), "spread": float(top - bottom)}


def _grade_returns(panel: pd.DataFrame, horizon: int = 60) -> pd.DataFrame:
    col = f"ret{horizon}"
    rows = []
    for grade in ["S", "A+", "A", "B", "C", "Reject"]:
        sub = panel.loc[panel["grade"] == grade]
        if sub.empty:
            rows.append({"grade": grade, "n": 0, "mean": np.nan, "win": np.nan, "median": np.nan})
            continue
        values = _net(sub[col]).dropna()
        rows.append({"grade": grade, "n": int(len(values)), "mean": float(values.mean()),
                     "median": float(values.median()), "win": float((values > 0).mean())})
    return pd.DataFrame(rows)


def _gate_effect(panel: pd.DataFrame, horizon: int = 60) -> pd.DataFrame:
    """硬约束是否真的挡掉了差样本（在 FRS 前 20% 内部比较）。"""
    rank = _rank_pct(panel, "frs")
    top = panel.loc[rank >= 0.8].copy()
    _ensure_gate_flags(top)
    col = f"ret{horizon}"
    rows = []
    for flag, label in (("expensive_entry", "估值偏高"), ("severe_expensive", "估值严重偏高"),
                        ("low_base_penalty", "低基数"), ("fake_turnaround_flag", "一次性收益"),
                        ("value_trap_flag", "价值陷阱")):
        if flag not in top.columns:
            continue
        mask = top[flag].fillna(False).astype(bool)
        inside = _net(top.loc[mask, col]).dropna()
        outside = _net(top.loc[~mask, col]).dropna()
        rows.append({
            "flag": label, "命中样本": int(len(inside)), "命中mean": float(inside.mean()) if len(inside) else np.nan,
            "未命中样本": int(len(outside)), "未命中mean": float(outside.mean()) if len(outside) else np.nan,
            "差值pp": (float(inside.mean() - outside.mean()) * 100) if len(inside) and len(outside) else np.nan,
        })
    return pd.DataFrame(rows)


def _ensure_gate_flags(frame: pd.DataFrame) -> None:
    """面板里若没有硬约束标记，就用面板保存的原始字段按同一规则重算。

    （回测面板保存的是 current_pe / industry_pe / upside_base / f_low_base_ratio，
    与 valuation.py / fundamental_reversal.py 的判定口径一致。）
    """
    settings = load_settings()
    gate = settings.path("rps.gate", {}) or {}
    if "expensive_entry" not in frame.columns:
        pe = pd.to_numeric(frame.get("current_pe"), errors="coerce")
        ind_pe = pd.to_numeric(frame.get("industry_pe"), errors="coerce")
        upside = pd.to_numeric(frame.get("upside_base"), errors="coerce")
        cheap_pe = pe.where(pe.between(0, 500))
        mult = float(gate.get("pe_vs_industry_multiple", 1.5))
        severe_mult = float(gate.get("severe_pe_multiple", 2.0))
        frame["expensive_entry"] = (
            (cheap_pe > ind_pe * mult) | (upside < float(gate.get("min_base_upside", 0.15)))
        ).fillna(False)
        frame["severe_expensive"] = (
            (cheap_pe > ind_pe * severe_mult) | (upside < float(gate.get("severe_upside", 0.0)))
        ).fillna(False)
    if "low_base_penalty" not in frame.columns:
        low_base = pd.to_numeric(frame.get("f_low_base_ratio"), errors="coerce")
        np_yoy = pd.to_numeric(frame.get("f_np_yoy"), errors="coerce")
        threshold = float(settings.path("filters.fake_turnaround.low_base_ratio", 0.3))
        frame["low_base_penalty"] = ((low_base < threshold) & (np_yoy > 0)).fillna(False)
    if "fake_turnaround_flag" not in frame.columns:
        one_off = pd.to_numeric(frame.get("f_one_off_ratio"), errors="coerce")
        dedt_gap = pd.to_numeric(frame.get("dedt_gap_ratio"), errors="coerce")
        if "is_fake_turnaround" in frame.columns:
            frame["fake_turnaround_flag"] = frame["is_fake_turnaround"].fillna(False).astype(bool)
        else:
            frame["fake_turnaround_flag"] = ((one_off > 0.6) | (dedt_gap > 0.6)).fillna(False)
    if "value_trap_flag" not in frame.columns:
        if "is_value_trap" in frame.columns:
            frame["value_trap_flag"] = frame["is_value_trap"].fillna(False).astype(bool)
        else:
            frame["value_trap_flag"] = pd.Series(False, index=frame.index)


def _fmt(value) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "—"
    return f"{value * 100:+.2f}%" if abs(value) < 50 else f"{value:.2f}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old", default="backtest_2019_2026")
    parser.add_argument("--new", default="backtest_v2")
    args = parser.parse_args()

    settings = load_settings()
    split = str(settings.path("backtest.in_sample_end", "20231231"))
    old = pd.read_parquet(PROCESSED_DIR / f"{args.old}.parquet")
    new = pd.read_parquet(PROCESSED_DIR / f"{args.new}.parquet")

    lines = ["# 07 · 结构改进验证（回测前 vs 回测后）", ""]
    lines.append(f"对比两次回测：`{args.old}`（改进前）与 `{args.new}`（改进后）。")
    lines.append("两次使用**完全相同的数据、样本区间、成本与入场规则**，唯一变量是评分结构。")
    lines.append("")
    lines.append("> 注：带 `b` 后缀的面板是用**修正后的前视收益函数**重建的"
                 "（每个持有期独立判断可用性）。修正前的实现要求 250 个交易日的完整窗口，"
                 "否则该调仓日的所有持有期一起丢弃，导致 2025-09 之后的信号被静默排除。")
    lines.append("")
    lines.append(f"- 样本：前 {len(old):,} 行 / 后 {len(new):,} 行；样本内/外分界 {split}")
    lines.append("")

    lines.append("## 1. FRS 分层（60 日净收益）")
    lines.append("")
    lines.append("| 样本段 | 版本 | TOP10% | TOP10-20% | MIDDLE | BOTTOM20% | 多空差 |")
    lines.append("|---|---|---|---|---|---|---|")
    segments = [("全样本", old, new)]
    segments.append(("样本内", old.loc[old["as_of"] < split], new.loc[new["as_of"] < split]))
    segments.append(("样本外", old.loc[old["as_of"] >= split], new.loc[new["as_of"] >= split]))
    for name, o, n in segments:
        for label, frame in (("改进前", o), ("改进后", n)):
            g = _frs_groups(frame)
            lines.append(f"| {name} | {label} | {_fmt(g['top10'])} | {_fmt(g['top20'])} | "
                         f"{_fmt(g['middle'])} | {_fmt(g['bottom'])} | **{g['spread'] * 100:+.2f}pp** |")
    lines.append("")

    lines.append("## 2. BFS 五分位多空差（60 日净收益）")
    lines.append("")
    lines.append("| 样本段 | 版本 | Q1(低) | 中位 | Q5(高) | 多空差 |")
    lines.append("|---|---|---|---|---|---|")
    for name, o, n in segments:
        for label, frame in (("改进前", o), ("改进后", n)):
            b = _bfs_spread(frame)
            lines.append(f"| {name} | {label} | {_fmt(b['q1'])} | {_fmt(b['mid'])} | "
                         f"{_fmt(b['q5'])} | **{b['spread'] * 100:+.2f}pp** |")
    lines.append("")

    lines.append("## 3. 等级收益（60 日净收益，改进后）")
    lines.append("")
    lines.append("| 等级 | 样本数 | 平均 | 中位 | 胜率 |")
    lines.append("|---|---|---|---|---|")
    for row in _grade_returns(new).itertuples(index=False):
        lines.append(f"| {row.grade} | {row.n} | {_fmt(row.mean)} | {_fmt(row.median)} | {_fmt(row.win)} |")
    lines.append("")
    lines.append("（S/A 级样本量过小时，该行数字仅有参考意义，不能作为结论。）")
    lines.append("")

    lines.append("## 3.1 金融股单独看（银行 / 非银金融）")
    lines.append("")
    fin_industries = set(settings.path("frs.financial_industries", []) or [])
    lines.append("| 面板 | 金融股样本 | 金融股 FRS 中位 | 金融股 FRS 多空差(60日) | 非金融 FRS 多空差(60日) | BFS 前 20% 里金融股占比 |")
    lines.append("|---|---|---|---|---|---|")
    for label, frame in (("改进前", old), ("改进后", new)):
        if "industry" not in frame.columns:
            continue
        is_fin = frame["industry"].isin(fin_industries)
        fin, nonfin = frame.loc[is_fin], frame.loc[~is_fin]
        rank_fin = _rank_pct(fin, "frs")
        spread_fin = np.nan
        if not fin.empty:
            top = _net(fin.loc[rank_fin >= 0.8, "ret60"]).dropna()
            bottom = _net(fin.loc[rank_fin <= 0.2, "ret60"]).dropna()
            if len(top) and len(bottom):
                spread_fin = float(top.mean() - bottom.mean())
        spread_nonfin = np.nan
        if not nonfin.empty:
            r = _rank_pct(nonfin, "frs")
            top = _net(nonfin.loc[r >= 0.8, "ret60"]).dropna()
            bottom = _net(nonfin.loc[r <= 0.2, "ret60"]).dropna()
            if len(top) and len(bottom):
                spread_nonfin = float(top.mean() - bottom.mean())
        rank_bfs = _rank_pct(frame, "bfs")
        top_bfs = frame.loc[rank_bfs >= 0.8]
        share = float(is_fin.loc[top_bfs.index].mean()) if len(top_bfs) else np.nan
        lines.append(f"| {label} | {int(is_fin.sum()):,} | {fin['frs'].median():.1f} | "
                     f"{_fmt(spread_fin)} | {_fmt(spread_nonfin)} | {_fmt(share)} |")
    lines.append("")
    lines.append("> 金融股专用模板的目标不是提高总分，而是让金融股的分数**由经营指标决定**"
                 "（TTM 平滑 + 净资产 + ROE/ROA + 投资收益占比），而不是被投资收益波动带偏。"
                 "因此这里看的是金融股内部的 FRS 多空差是否改善。")
    lines.append("")

    lines.append("## 4. 硬约束是否真的挡住了差样本（FRS 前 20% 内部，改进前后各算一次）")
    lines.append("")
    lines.append("> ⚠️ 口径说明：第 6 节的 False Positive Library 统计的是**亏损样本内部**的亏损幅度，"
                 "它不能回答「这个类别整体是不是更差」。判断某个硬约束是否有效，"
                 "必须比较**命中组与未命中组的无条件平均收益**。下面分别用改进前/后的面板计算。")
    lines.append("")
    lines.append("| 面板 | 约束 | 命中样本 | 命中平均 | 未命中样本 | 未命中平均 | 差值 |")
    lines.append("|---|---|---|---|---|---|---|")
    for label, frame in (("改进前", old), ("改进后", new)):
        for row in _gate_effect(frame).itertuples(index=False):
            lines.append(f"| {label} | {row.flag} | {row.命中样本} | {_fmt(row.命中mean)} | "
                         f"{row.未命中样本} | {_fmt(row.未命中mean)} | **{row.差值pp:+.2f}pp** |")
    lines.append("")

    lines.append("## 5. 评分分布变化")
    lines.append("")
    lines.append("| 指标 | 改进前 | 改进后 |")
    lines.append("|---|---|---|")
    for col in ("frs", "lis", "pcs", "bfs"):
        if col in old.columns and col in new.columns:
            lines.append(f"| {col} 中位数 | {old[col].median():.1f} | {new[col].median():.1f} |")
            lines.append(f"| {col} ≥70 占比 | {(old[col] >= 70).mean() * 100:.1f}% | "
                         f"{(new[col] >= 70).mean() * 100:.1f}% |")
    for grade in ("S", "A+", "A", "B", "C", "Reject"):
        o = (old["grade"] == grade).mean() * 100 if "grade" in old.columns else np.nan
        n = (new["grade"] == grade).mean() * 100 if "grade" in new.columns else np.nan
        lines.append(f"| 等级 {grade} 占比 | {o:.2f}% | {n:.2f}% |")
    lines.append("")

    lines.append("## 6. 结论（如实汇报）")
    lines.append("")
    lines.append(_conclusion(old, new, split))
    lines.append("")
    lines.append("## 7. 候选池规则调整的 what-if（在改进后面板上模拟，不需要重跑回测）")
    lines.append("")
    lines.append("回测显示 VALUE_TRAP / FAKE_TURNAROUND 在 FRS 前 20% 内**优于**其余样本，"
                 "因此把这两条从「硬排除」改成「封顶 B + 披露」。下面量化这个改动对候选池的影响。")
    lines.append("")
    lines.append("| 候选池口径 | 样本数 | 60 日平均净收益 | 20 日平均净收益 |")
    lines.append("|---|---|---|---|")
    rank = _rank_pct(new, "bfs")
    top = new.loc[rank >= 0.8].copy()
    for label, mask in (
        ("旧规则（剔除 VALUE_TRAP + 假反转 + RISK_BLOCK）",
         ~top.get("is_value_trap", pd.Series(False, index=top.index)).fillna(False).astype(bool)
         & ~top.get("is_fake_turnaround", pd.Series(False, index=top.index)).fillna(False).astype(bool)
         & ~top.get("risk_block", pd.Series(False, index=top.index)).fillna(False).astype(bool)),
        ("新规则（只剔除 RISK_BLOCK；其余封顶 B 并披露）",
         ~top.get("risk_block", pd.Series(False, index=top.index)).fillna(False).astype(bool)),
    ):
        sub = top.loc[mask.fillna(False)]
        lines.append(f"| {label} | {len(sub):,} | {_fmt(_net(sub['ret60']).mean())} | "
                     f"{_fmt(_net(sub['ret20']).mean())} |")
    lines.append("")
    out = PROJECT_ROOT / "docs" / "07_structural_change_review.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"写入 {out}")
    print("\n".join(lines[:40]))


def _conclusion(old: pd.DataFrame, new: pd.DataFrame, split: str) -> str:
    out = []
    for name, o, n in (("全样本", old, new),
                       ("样本内", old.loc[old["as_of"] < split], new.loc[new["as_of"] < split]),
                       ("样本外", old.loc[old["as_of"] >= split], new.loc[new["as_of"] >= split])):
        go, gn = _frs_groups(o), _frs_groups(n)
        out.append(f"- {name} FRS 多空差：{go['spread'] * 100:+.2f}pp → **{gn['spread'] * 100:+.2f}pp**"
                   f"（{'改善' if gn['spread'] > go['spread'] else '未改善'}）")
    bo, bn = _bfs_spread(old), _bfs_spread(new)
    out.append(f"- 全样本 BFS 多空差：{bo['spread'] * 100:+.2f}pp → **{bn['spread'] * 100:+.2f}pp**")

    fin_industries = set(load_settings().path("frs.financial_industries", []) or [])
    for label, frame in (("改进前", old), ("改进后", new)):
        if "industry" not in frame.columns:
            continue
        fin = frame.loc[frame["industry"].isin(fin_industries)]
        if fin.empty:
            continue
        r = _rank_pct(fin, "frs")
        tv = _net(fin.loc[r >= 0.8, "ret60"]).dropna()
        bv = _net(fin.loc[r <= 0.2, "ret60"]).dropna()
        if len(tv) and len(bv):
            out.append(f"- {label} 金融股 FRS 多空差：{(tv.mean() - bv.mean()) * 100:+.2f}pp"
                       f"（top {_fmt(tv.mean())} / bottom {_fmt(bv.mean())}，样本 {len(fin):,}）")
    out.append("")
    out.append("**硬约束有效性（FRS 前 20% 内部，命中组 − 未命中组）**")
    for label, frame in (("改进前", old), ("改进后", new)):
        for row in _gate_effect(frame).itertuples(index=False):
            verdict = "有效（命中组更差）" if row.差值pp < 0 else "无效（命中组并不更差）"
            out.append(f"- {label} ·「{row.flag}」：{row.差值pp:+.2f}pp → {verdict}")
    out.append("")
    out.append("> 判读原则：多空差改善才算结构改进有效；若只是绝对收益变化，不算。"
               "样本外表现是主要判据，样本内改善但样本外恶化视为过拟合。")
    return "\n".join(out)


if __name__ == "__main__":
    main()
