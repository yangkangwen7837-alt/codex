"""Top20 选股口径的诊断：用它对选股到底有什么问题。

输出 docs/10_top20_diagnostics.md，覆盖：
  1. 相对基准的超额是否显著（逐期配对检验，全样本 / 样本内 / 样本外）
  2. 逐年超额（超额是不是集中在某一年）
  3. 名单稳定性与换手（每只股票平均停留多久、月与月的重叠率）
  4. 收益依赖度（多少收益来自少数几笔）
  5. 组合画像（行业集中度、流动性/规模代理）
  6. 尾部风险（最差月份、连续亏损、回撤恢复）
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bigfish.backtest.analysis import ROUND_TRIP_COST  # noqa: E402
from bigfish.config import load_settings  # noqa: E402

TOP_N = 20
HORIZON = 20


def _net(series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce") - ROUND_TRIP_COST


def _picks(panel: pd.DataFrame, top_n: int = TOP_N) -> pd.DataFrame:
    col = f"ret{HORIZON}"
    pool = panel.loc[
        panel["tradeable"].fillna(False).astype(bool)
        & ~panel["risk_block"].fillna(False).astype(bool)
        & (panel["grade"].astype(str) != "Reject")
    ]
    picked = (pool.sort_values(["as_of", "bfs"], ascending=[True, False])
                  .groupby("as_of").head(top_n))
    return picked.loc[picked[col].notna()].copy()


def _benchmark(panel: pd.DataFrame) -> pd.Series:
    col = f"ret{HORIZON}"
    df = panel.loc[panel[col].notna()]
    return (df.groupby("as_of")[col].mean() - ROUND_TRIP_COST).dropna()


def _paired(a: pd.Series, b: pd.Series) -> dict:
    joined = pd.concat([a.rename("s"), b.rename("b")], axis=1).dropna()
    if joined.empty:
        return {}
    diff = joined["s"] - joined["b"]
    return {
        "n": len(joined),
        "strategy": float(joined["s"].mean()),
        "benchmark": float(joined["b"].mean()),
        "diff": float(diff.mean()),
        "t": float(diff.mean() / diff.std() * np.sqrt(len(diff))) if diff.std() > 0 else np.nan,
        "win": float((diff > 0).mean()),
    }


def main() -> None:
    settings = load_settings()
    split = str(settings.path("backtest.in_sample_end", "20231231"))
    panel = pd.read_parquet("data/processed/backtest_v4b.parquet")
    picked = _picks(panel)
    bench = _benchmark(panel)

    strat = picked.groupby("as_of")[f"ret{HORIZON}"].apply(lambda s: _net(s).mean()).dropna()
    strat_df = pd.DataFrame({"strategy": strat, "benchmark": bench.reindex(strat.index)}).dropna()
    strat_df["year"] = strat_df.index.str[:4]

    lines = ["# 10 · Top20 选股口径的诊断（能不能直接拿来用）", ""]
    lines.append(f"口径：默认候选池 × 每期按 BFS 取 Top{TOP_N} 等权，月频调仓、持有 {HORIZON} 个交易日、"
                 f"双边成本 {ROUND_TRIP_COST * 100:.2f}%。样本 {panel['as_of'].nunique()} 次调仓。")
    lines.append("")

    # 1. 超额显著性
    lines.append("## 1. 相对基准的超额是否显著（逐期配对检验）")
    lines.append("")
    lines.append("基准 = 同一套可交易样本的全市场等权（同样扣 0.16% 成本）。")
    lines.append("")
    lines.append("| 分段 | 期数 | 策略每期 | 基准每期 | 超额/期 | t 值 | 胜出比例 | 判读 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for name, seg in (("全样本", strat_df), ("样本内 ~2023-12", strat_df.loc[strat_df.index < split]),
                      ("样本外 2024-01 ~", strat_df.loc[strat_df.index >= split])):
        stat = _paired(seg["strategy"], seg["benchmark"])
        if not stat:
            continue
        verdict = "显著（|t|>2）" if abs(stat["t"]) > 2 else "**不显著（|t|<2，无法与噪声区分）**"
        lines.append(f"| {name} | {stat['n']} | {stat['strategy'] * 100:+.2f}% | {stat['benchmark'] * 100:+.2f}% | "
                     f"{stat['diff'] * 100:+.2f}% | {stat['t']:+.2f} | {stat['win'] * 100:.0f}% | {verdict} |")
    lines.append("")

    # 2. 逐年超额
    lines.append("## 2. 逐年超额（超额是不是集中在某一年）")
    lines.append("")
    lines.append("| 年份 | 期数 | 策略年化 | 基准年化 | 超额 | 策略最差月 |")
    lines.append("|---|---|---|---|---|---|")
    for year, sub in strat_df.groupby("year"):
        s_ann = (1 + sub["strategy"]).prod() ** (12 / len(sub)) - 1
        b_ann = (1 + sub["benchmark"]).prod() ** (12 / len(sub)) - 1
        lines.append(f"| {year} | {len(sub)} | {s_ann * 100:+.1f}% | {b_ann * 100:+.1f}% | "
                     f"{(s_ann - b_ann) * 100:+.1f}pp | {sub['strategy'].min() * 100:+.1f}% |")
    lines.append("")

    # 3. 名单稳定性与换手
    lines.append("## 3. 名单稳定性与换手（「按这个标准选股」实际要交易多少）")
    lines.append("")
    months = sorted(picked["as_of"].unique())
    appearances = picked.groupby("ts_code")["as_of"].nunique().sort_values(ascending=False)
    overlaps = []
    for prev, cur in zip(months, months[1:]):
        a = set(picked.loc[picked["as_of"] == prev, "ts_code"])
        b = set(picked.loc[picked["as_of"] == cur, "ts_code"])
        overlaps.append(len(a & b) / max(len(b), 1))
    lines.append(f"- 全期共出现过 {len(appearances):,} 只不同股票（{len(months)} 期 × {TOP_N} 只 = "
                 f"{len(months) * TOP_N:,} 个持仓位）")
    lines.append(f"- 平均每位股票在名单里停留 {appearances.mean():.1f} 期（中位 {appearances.median():.0f} 期）；"
                 f"停留 ≥3 期的占 {float((appearances >= 3).mean()) * 100:.0f}%")
    lines.append(f"- 相邻两期名单重叠率：平均 {np.mean(overlaps) * 100:.0f}%"
                 f"（即每期换掉约 {(1 - np.mean(overlaps)) * 100:.0f}% 的持仓）")
    lines.append(f"- 单边年换手率约 {12 * (1 - np.mean(overlaps)) * 100:.0f}%")
    lines.append("")

    # 4. 收益依赖度
    lines.append("## 4. 收益依赖度（多少收益来自少数几笔）")
    lines.append("")
    trades = _net(picked[f"ret{HORIZON}"]).dropna().sort_values(ascending=False)
    total = trades.sum()
    pos_total = trades[trades > 0].sum()
    for pct in (0.05, 0.10, 0.20):
        k = max(int(len(trades) * pct), 1)
        head = trades.head(k).sum()
        share_net = head / total if total else np.nan
        share_pos = head / pos_total if pos_total else np.nan
        lines.append(f"- 收益最高的 {pct * 100:.0f}% 交易（{k:,} 笔）：占**全部净收益** "
                     f"{share_net * 100:.0f}%，占全部正收益 {share_pos * 100:.0f}%")
    lines.append(f"- 单笔中位 {trades.median() * 100:+.2f}%、平均 {trades.mean() * 100:+.2f}%、"
                 f"最差 {trades.min() * 100:+.1f}%、最好 {trades.max() * 100:+.1f}%")
    lines.append("")

    # 5. 组合画像
    lines.append("## 5. 组合画像（行业与流动性）")
    lines.append("")
    top_ind = picked.groupby("industry").size().sort_values(ascending=False)
    uni_ind = panel.groupby("industry").size()
    lines.append("| 行业 | 被选中次数 | 占组合比重 | 该行业在样本中的比重 | 超配倍数 |")
    lines.append("|---|---|---|---|---|")
    for ind, cnt in top_ind.head(8).items():
        lines.append(f"| {ind} | {cnt:,} | {cnt / len(picked) * 100:.1f}% | "
                     f"{uni_ind.get(ind, 0) / len(panel) * 100:.1f}% | "
                     f"{(cnt / len(picked)) / max(uni_ind.get(ind, 1) / len(panel), 1e-9):.1f}× |")
    q = picked["amount20"].astype(float)
    qu = panel["amount20"].astype(float)
    lines.append("")
    lines.append(f"- 成交额（20 日均值，千元）分位：组合中位 {q.median():,.0f} vs 全样本中位 {qu.median():,.0f}"
                 f"（组合位于全样本的 {(qu < q.median()).mean() * 100:.0f}% 分位）")
    lines.append(f"- 组合中成交额低于全样本 30% 分位的比例："
                 f"{(q < qu.quantile(0.3)).mean() * 100:.0f}%")
    lines.append("")

    # 6. 尾部风险
    lines.append("## 6. 尾部风险")
    lines.append("")
    worst = strat_df["strategy"].nsmallest(5)
    lines.append(f"- 最差的 5 个月：{', '.join(f'{d}({v * 100:+.1f}%)' for d, v in worst.items())}")
    streak = cur = 0
    for value in strat_df["strategy"]:
        cur = cur + 1 if value < 0 else 0
        streak = max(streak, cur)
    lines.append(f"- 最长连续亏损月数：{streak}")
    curve = (1 + strat_df["strategy"]).cumprod()
    dd = curve / curve.cummax() - 1
    trough = dd.idxmin()
    peak = curve.loc[:trough].idxmax()
    recovery = curve.loc[trough:][curve.loc[trough:] >= curve.loc[peak]]
    lines.append(f"- 最大回撤 {dd.min() * 100:.1f}%：{peak} 见顶 → {trough} 见底 → "
                 f"{'恢复于 ' + recovery.index[0] if len(recovery) else '至今未恢复'}"
                 f"（历时约 {len(curve.loc[peak:trough])} 个月）")
    lines.append("")

    # 7. 结论
    lines.append("## 7. 结论：能直接用吗")
    lines.append("")
    full = _paired(strat_df["strategy"], strat_df["benchmark"])
    oos = _paired(strat_df.loc[strat_df.index >= split, "strategy"],
                  strat_df.loc[strat_df.index >= split, "benchmark"])
    lines.append(f"1. **超额不显著**：全样本相对等权基准的超额 {full['diff'] * 100:+.2f}%/月"
                 f"（t={full['t']:+.2f}），样本外为 {oos['diff'] * 100:+.2f}%/月（t={oos['t']:+.2f}）。"
                 "两者都落在噪声范围内，因此**不能用这份回测证明这个选股标准有效**。")
    lines.append(f"2. **它是一套高换手、宽分散、右偏的组合**：平均每只股票只停留 "
                 f"{appearances.mean():.1f} 期，每期换掉约 {(1 - np.mean(overlaps)) * 100:.0f}%，"
                 f"收益高度依赖少数几笔。")
    lines.append("3. **单笔口径的优势主要体现在「避开差股票」**，而不是「抓到远超市场的赢家」——"
                 "这对选股的含义是：它可以当作风险过滤器（剔除底部），但当成选股器时要接受 "
                 "样本外跑输等权基准的现实。")
    lines.append("4. **仍未建模的现实摩擦**：涨跌停无法成交（只过滤了开盘接近涨停）、"
                 "停牌、冲击成本、融券不可得。高换手口径下这些会显著侵蚀收益。")
    lines.append("5. 建议用法：把 Top20 当作**研究观察池**（每天看什么），"
                 "而不是**组合指令**（每天买什么）；若要实盘，先做纸面跟踪 3~6 个月，"
                 "记录信号与实际成交价的差。")
    out = Path("docs/10_top20_diagnostics.md")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"写入 {out}")
    print("\n".join(lines[:24]))
    print("...")
    print("\n".join(lines[-12:]))


if __name__ == "__main__":
    main()
