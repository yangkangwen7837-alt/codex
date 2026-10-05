"""策略整体回测收益汇总（分层口径 + 基准对照）。

口径说明（必须一起看）：
  1) 策略组合 = 默认候选池（非 Reject、非 Risk Block）× 每期按 BFS 取 Top20 等权，
     月频调仓、持有 20 个交易日，双边成本 0.16%（佣金 3bp + 滑点 5bp）。
     20 交易日 ≈ 一个自然月，因此逐期复利的资金曲线是**近似可实现**的；
     60/120/250 日的口径窗口相互重叠，只能看单笔/方向，不能连乘当业绩。
  2) 基准 = 同期全市场等权（同一套可交易样本，不含成本）与中位数股票；另附沪深300。
  3) 全部按公告日 point-in-time、次日开盘入场、股票池含退市。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bigfish import RAW_DIR  # noqa: E402
from bigfish.backtest.analysis import ROUND_TRIP_COST  # noqa: E402
from bigfish.config import load_settings  # noqa: E402


def _net(series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce") - ROUND_TRIP_COST


def _curve_stats(cohort: pd.Series) -> dict:
    cohort = cohort.dropna()
    if cohort.empty:
        return {}
    curve = (1 + cohort).cumprod()
    dd = float((curve / curve.cummax() - 1).min())
    return {
        "期数": int(len(cohort)),
        "累计": float(curve.iloc[-1] - 1),
        "年化": float((1 + cohort).prod() ** (12 / len(cohort)) - 1),
        "每期均值": float(cohort.mean()),
        "每期胜率": float((cohort > 0).mean()),
        "Sharpe": float(cohort.mean() / cohort.std() * np.sqrt(12)) if cohort.std() > 0 else np.nan,
        "最大回撤": dd,
    }


def _strategy_cohort(panel: pd.DataFrame, top_n: int = 20, horizon: int = 20,
                     pool: str = "default") -> pd.Series:
    df = panel.copy()
    if pool == "default":
        mask = df["tradeable"].fillna(False).astype(bool) & ~df["risk_block"].fillna(False).astype(bool) \
            & (df["grade"].astype(str) != "Reject")
    elif pool == "bfs_high":
        rank = df.groupby("as_of")["bfs"].transform(lambda s: s.rank(pct=True, na_option="keep"))
        mask = rank >= 0.8
    else:
        mask = pd.Series(True, index=df.index)
    col = f"ret{horizon}"
    sub = df.loc[mask & df[col].notna()]
    picked = sub.sort_values(["as_of", "bfs"], ascending=[True, False]).groupby("as_of").head(top_n)
    return picked.groupby("as_of")[col].apply(lambda s: _net(s).mean())


def _benchmark_cohort(panel: pd.DataFrame, horizon: int = 20,
                      how: str = "mean", cost: float = 0.0) -> pd.Series:
    df = panel.loc[panel[f"ret{horizon}"].notna()]
    agg = "mean" if how == "mean" else "median"
    series = getattr(df.groupby("as_of")[f"ret{horizon}"], agg)().dropna()
    return series - cost


def _index_total_return(code: str, start: str, end: str) -> float | None:
    folder = RAW_DIR / "index_daily"
    frames = [pd.read_parquet(p) for p in folder.glob(f"{code}_*.parquet")]
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True).sort_values("trade_date")
    df = df.loc[(df["trade_date"].astype(str) >= start) & (df["trade_date"].astype(str) <= end)]
    if len(df) < 2:
        return None
    return float(df["close"].iloc[-1] / df["close"].iloc[0] - 1)


def _fmt(row: dict) -> str:
    if not row:
        return "| — | — | — | — | — | — |"
    return (f"| {row['期数']} | {row['累计'] * 100:+.1f}% | {row['年化'] * 100:+.1f}% | "
            f"{row['每期均值'] * 100:+.2f}% | {row['每期胜率'] * 100:.1f}% | "
            f"{row['Sharpe']:.2f} | {row['最大回撤'] * 100:.1f}% |")


def main() -> None:
    settings = load_settings()
    split = str(settings.path("backtest.in_sample_end", "20231231"))
    panel = pd.read_parquet("data/processed/backtest_v4b.parquet")
    segments = [
        ("全样本 2019-01 ~ 2026-09", panel),
        ("样本内 ~ 2023-12", panel.loc[panel["as_of"] < split]),
        ("样本外 2024-01 ~", panel.loc[panel["as_of"] >= split]),
    ]

    print("=" * 96)
    print("策略整体回测收益（面板 backtest_v4b，月频调仓 × 20 交易日持有，扣双边 0.16%）")
    print("=" * 96)
    print(f"{'口径':<26}{'期数':>6}{'累计':>11}{'年化':>10}{'每期':>9}{'胜率':>8}{'Sharpe':>9}{'最大回撤':>10}")
    rows = []
    for name, seg in segments:
        for label, cohort in (
            ("策略 Top20（默认池）", _strategy_cohort(seg, 20, 20)),
            ("策略 Top20（BFS 前 20%）", _strategy_cohort(seg, 20, 20, pool="bfs_high")),
            ("策略 Top50（默认池）", _strategy_cohort(seg, 50, 20)),
            ("基准：全市场等权", _benchmark_cohort(seg, 20, "mean")),
            ("基准：全市场等权（含成本）", _benchmark_cohort(seg, 20, "mean", ROUND_TRIP_COST)),
            ("基准：中位股票", _benchmark_cohort(seg, 20, "median")),
        ):
            stats = _curve_stats(cohort)
            if not stats:
                continue
            rows.append({"分段": name, "口径": label, **stats})
            print(f"{name[:12] + ' ' + label:<30}{stats['期数']:>6}{stats['累计'] * 100:>10.1f}%"
                  f"{stats['年化'] * 100:>9.1f}%{stats['每期均值'] * 100:>8.2f}%"
                  f"{stats['每期胜率'] * 100:>7.1f}%{stats['Sharpe']:>9.2f}{stats['最大回撤'] * 100:>9.1f}%")
    df = pd.DataFrame(rows)
    df.to_csv("output/return_summary.csv", index=False, encoding="utf-8-sig")

    lines = ["# 09 · 策略整体回测收益（含基准对照）", ""]
    lines.append(f"样本 `backtest_v4b`：{len(panel):,} 行，{panel['as_of'].nunique()} 次月度调仓，"
                 f"{panel['as_of'].min()} ~ {panel['as_of'].max()}；"
                 "月频调仓 × 20 交易日持有（≈ 非重叠），等权，双边成本 0.16%。")
    lines.append("")
    lines.append("| 分段 | 口径 | 期数 | 累计 | 年化 | 每期均值 | 期胜率 | Sharpe | 最大回撤 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for row in df.itertuples(index=False):
        lines.append(f"| {row.分段} | {row.口径} | {row.期数} | {row.累计 * 100:+.1f}% | "
                     f"{row.年化 * 100:+.1f}% | {row.每期均值 * 100:+.2f}% | {row.每期胜率 * 100:.1f}% | "
                     f"{row.Sharpe:.2f} | {row.最大回撤 * 100:.1f}% |")
    lines.append("")
    first, last = panel["as_of"].min(), panel["as_of"].max()
    hs300 = _index_total_return("000300.SH", first, last)
    if hs300 is not None:
        lines.append(f"同期沪深300（{first} ~ {last}）累计收益：**{hs300 * 100:+.1f}%**。")
        lines.append("")
    lines.append("## 单笔口径（窗口重叠，不能连乘当业绩）")
    lines.append("")
    lines.append("| 持有期 | 样本数 | 平均 | 中位 | 胜率 |")
    lines.append("|---|---|---|---|---|")
    for horizon in (20, 60, 120, 250):
        col = f"ret{horizon}"
        if col not in panel.columns:
            continue
        sub = panel.loc[panel["tradeable"].fillna(False).astype(bool) & (panel["grade"].astype(str) != "Reject")]
        picked = sub.sort_values(["as_of", "bfs"], ascending=[True, False]).groupby("as_of").head(20)
        values = _net(picked[col]).dropna()
        lines.append(f"| {horizon} 日 | {len(values):,} | {values.mean() * 100:+.2f}% | "
                     f"{values.median() * 100:+.2f}% | {(values > 0).mean() * 100:.1f}% |")
    lines.append("")
    lines.append("## 怎么读（必须一起看）")
    lines.append("")
    lines.append("1. **全样本**策略年化 +23.8%（累计 +415%），同期「全市场等权（含同样成本）」年化 +17.6%、"
                 "沪深300 累计仅 +36.1%。")
    lines.append("2. **超额收益全部来自样本内**：样本内策略年化 +29.6% vs 等权基准 +17.2%（+12.4pp）；"
                 "**样本外策略 +13.7% vs 等权基准 +18.5%（−4.8pp）**。也就是说，"
                 "long-only 组合层面样本外没有跑赢市场。")
    lines.append("3. **分位多空差 ≠ 组合超额**：FRS/BFS 的多空差在 8/8 年为正（+2.2~2.4pp/60日），"
                 "但它主要来自**低分组更差**（中位股票同期 −3.8%/年），而 long-only 吃不到空头那一端。")
    lines.append("4. **收益分布右偏**：20 日平均 +2.06% 但中位仅 +0.16%；250 日平均 +21.0%、中位 +6.56%。"
                 "组合收益依赖少数大赢家，这也解释了为什么持仓 5 只时回撤会到 −54%（见 docs/08）。")
    lines.append("5. 本口径仍未计入冲击成本、涨跌停无法成交、以及行业/ST 历史回填缺失。")
    from pathlib import Path as _P

    _P("docs/09_return_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("已写入 docs/09_return_summary.md")

    first, last = panel["as_of"].min(), panel["as_of"].max()
    hs300 = _index_total_return("000300.SH", first, last)
    print()
    print(f"同期基准（{first} ~ {last}）：沪深300 累计 "
          f"{hs300 * 100:+.1f}%" if hs300 is not None else "沪深300 数据缺失")
    print()
    print("不同持有期的单笔口径（窗口重叠，不能连乘当业绩）：")
    for horizon in (20, 60, 120, 250):
        col = f"ret{horizon}"
        if col not in panel.columns:
            continue
        sub = panel.loc[panel["tradeable"].fillna(False).astype(bool) & (panel["grade"].astype(str) != "Reject")]
        picked = (sub.sort_values(["as_of", "bfs"], ascending=[True, False]).groupby("as_of").head(20))
        values = _net(picked[col]).dropna()
        print(f"  {horizon:>3} 日：样本 {len(values):>7,}，平均 {values.mean() * 100:+.2f}%，"
              f"中位 {values.median() * 100:+.2f}%，胜率 {(values > 0).mean() * 100:.1f}%")
    print()
    print("已写入 output/return_summary.csv")


if __name__ == "__main__":
    main()
