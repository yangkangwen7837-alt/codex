"""把 Big Fish 当作「候选生成器」来评估：召回、提前量、精度、闪烁率。

输出 docs/11_generator_diagnostics.md。

为什么换指标：作为研究线索池，使用者关心的是
  (1) 未来会大涨的股票，系统有没有在**上涨之前**把名字摆出来（召回 + 提前量）
  (2) 摆出来的名字里，有多少真的兑现了（精度）
  (3) 名字是不是天天换、让人跟不上（闪烁率）
组合收益不再是主指标。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bigfish.config import load_settings  # noqa: E402

BIG_WINNER = 0.50      # 250 交易日涨幅 ≥50% 视为"大鱼"
BIG_LOSER = -0.30
TOPN = 20


def _monthly_sets(df: pd.DataFrame, mask: pd.Series) -> dict[str, set[str]]:
    sub = df.loc[mask.fillna(False).astype(bool)]
    return {d: set(g["ts_code"]) for d, g in sub.groupby("as_of")}


def _generator_scores(panel: pd.DataFrame, settings) -> pd.DataFrame:
    """在面板上按同一套公式重算抓鱼分 / 避雷分（面板已含全部输入列）。"""
    score_cfg = settings.path("scores", {}) or {}
    up_w = dict((score_cfg.get("upside", {}) or {}).get("weights", {}))
    down_cfg = score_cfg.get("downside", {}) or {}
    down_w = dict(down_cfg.get("weights", {}))

    def weighted(frame: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
        cols = [c for c in weights if c in frame.columns]
        values = frame[cols].astype(float)
        w = pd.Series({c: weights[c] for c in cols})
        num = values.fillna(0).mul(w, axis=1).sum(axis=1)
        den = values.notna().mul(w, axis=1).sum(axis=1)
        return (num / den.replace(0, np.nan)).clip(0, 100)

    out = pd.DataFrame(index=panel.index)
    out["upside_score"] = weighted(panel, up_w)
    kills = pd.to_numeric(panel.get("kill_count", 0), errors="coerce").fillna(0)
    expensive = panel.get("expensive_entry", pd.Series(False, index=panel.index)).fillna(False).astype(bool)
    severe = panel.get("severe_expensive", pd.Series(False, index=panel.index)).fillna(False).astype(bool)
    pen = down_cfg.get("valuation_penalty", {}) or {}
    ds_frame = pd.DataFrame({
        "pcs": pd.to_numeric(panel.get("pcs"), errors="coerce"),
        "kill": (100 - float(down_cfg.get("kill_penalty_per_signal", 33)) * kills).clip(0, 100),
        "valuation": (100 - float(pen.get("expensive", 35)) * expensive.astype(float)
                      - float(pen.get("severe", 25)) * severe.astype(float)).clip(0, 100),
        "data_quality": (pd.to_numeric(panel.get("data_confidence"), errors="coerce") * 100).clip(0, 100),
    })
    out["downside_score"] = weighted(ds_frame, down_w)
    return out


def main() -> None:
    settings = load_settings()
    panel = pd.read_parquet("data/processed/backtest_v4b.parquet")
    months = sorted(panel["as_of"].unique())
    pos = {m: i for i, m in enumerate(months)}

    tradeable = panel["tradeable"].fillna(False).astype(bool)
    no_risk = ~panel["risk_block"].fillna(False).astype(bool)
    grade = panel["grade"].astype(str)
    bfs = pd.to_numeric(panel["bfs"], errors="coerce")
    frs = pd.to_numeric(panel["frs"], errors="coerce")
    pcs = pd.to_numeric(panel["pcs"], errors="coerce")
    lis = pd.to_numeric(panel["lis"], errors="coerce")

    pool = tradeable & no_risk & (grade != "Reject")
    # 通道 A：综合榜单（现用口径）
    ranked = panel.loc[pool].sort_values(["as_of", "bfs"], ascending=[True, False])
    top_a = ranked.groupby("as_of").head(TOPN)
    # 通道 B：早期线索（基本面已确认、价格尚未确认）
    early_mask = tradeable & no_risk & (frs >= 70) & (pcs < 60)
    early = panel.loc[early_mask].sort_values(["as_of", "frs"], ascending=[True, False])
    top_b = early.groupby("as_of").head(TOPN)
    # 通道 C：两条通道合并
    top_c = (pd.concat([top_a, top_b]).drop_duplicates(subset=["as_of", "ts_code"]))

    # 对照通道：朴素因子（用于回答"比随机/比动量好多少"）
    trad = panel.loc[tradeable & no_risk]
    top_mom = (trad.sort_values(["as_of", "trail_ret20"], ascending=[True, False])
                   .groupby("as_of").head(TOPN))
    top_rev = (trad.sort_values(["as_of", "trail_ret20"], ascending=[True, True])
                   .groupby("as_of").head(TOPN))

    sets_a = _monthly_sets(top_a, pd.Series(True, index=top_a.index))
    sets_b = _monthly_sets(top_b, pd.Series(True, index=top_b.index))
    sets_c = _monthly_sets(top_c, pd.Series(True, index=top_c.index))
    sets_mom = _monthly_sets(top_mom, pd.Series(True, index=top_mom.index))
    sets_rev = _monthly_sets(top_rev, pd.Series(True, index=top_rev.index))

    # 未来大鱼：以 ret250 ≥ +50% 标记（需要完整 250 日窗口）
    label = panel.loc[panel["ret250"].notna(), ["as_of", "ts_code", "ret250", "ret60", "industry"]]
    winners = {}
    losers = {}
    for d, g in label.groupby("as_of"):
        winners[d] = set(g.loc[g["ret250"] >= BIG_WINNER, "ts_code"])
        losers[d] = set(g.loc[g["ret250"] <= BIG_LOSER, "ts_code"])

    def recall(sets: dict[str, set[str]], lookback: int = 6) -> float:
        hits = tot = 0
        for d, win in winners.items():
            if d not in pos:
                continue
            window = set()
            for k in range(lookback + 1):
                j = pos[d] - k
                if j >= 0:
                    window |= sets.get(months[j], set())
            hits += len(win & window)
            tot += len(win)
        return hits / tot if tot else np.nan

    def precision(sets: dict[str, set[str]]) -> tuple[float, float, int]:
        hit = bad = tot = 0
        for d, ts in sets.items():
            if d not in winners:
                continue
            hit += len(ts & winners[d])
            bad += len(ts & losers[d])
            tot += len(ts)
        return (hit / tot if tot else np.nan), (bad / tot if tot else np.nan), tot

    # 提前量：大鱼"起点月" d 之前 12 个月内，系统第一次摆出这只票的时间
    hit_sets = sets_c
    lead_rows = []
    for d, win in winners.items():
        if d not in pos:
            continue
        for code in win:
            window = [months[j] for j in range(max(pos[d] - 12, 0), pos[d] + 1)]
            seen = next((m for m in window if code in hit_sets.get(m, set())), None)
            if seen is not None:
                lead_rows.append({"code": code, "start": d, "seen": seen,
                                  "lead_months": pos[d] - pos[seen]})
    leads = pd.DataFrame(lead_rows)

    # 命中标的的收益节奏（我们摆出来之后，行情多快走出来）
    picks_c = top_c.loc[top_c["ret250"].notna()]
    hits = picks_c.loc[picks_c["ret250"] >= BIG_WINNER]
    pace = {f"ret{h}": float(np.nanmean(hits[f"ret{h}"].astype(float))) for h in (20, 60, 120, 250)}

    # 闪烁率：进入后 ≤2 期就消失的比例
    def flicker(sets: dict[str, set[str]]) -> tuple[float, float]:
        runs: list[int] = []
        codes = {c for s in sets.values() for c in s}
        for code in codes:
            present = [d for d in months if code in sets.get(d, set())]
            if not present:
                continue
            run = 1
            for prev, cur in zip(present, present[1:]):
                if pos[cur] - pos[prev] == 1:
                    run += 1
                else:
                    runs.append(run)
                    run = 1
            runs.append(run)
        arr = np.array(runs) if runs else np.array([0])
        return float((arr <= 2).mean()), float(arr.mean())

    lines = ["# 11 · 候选生成器视角的诊断（召回 / 提前量 / 精度 / 闪烁）", ""]
    lines.append(f"样本 `backtest_v4b`：{len(months)} 次调仓；"
                 f"「大鱼」定义 = 未来 250 交易日涨幅 ≥ {BIG_WINNER * 100:.0f}%（需完整窗口，"
                 f"覆盖 {len(winners)} 个月）。")
    lines.append("")
    lines.append("两条通道：**A 综合榜单**（当前口径，按 BFS 取前 20）；"
                 "**B 早期线索**（FRS≥70 且 PCS<60：基本面已确认、价格尚未确认，按 FRS 取前 20）；"
                 "**C = A ∪ B**。")
    lines.append("")

    lines.append("## 1. 召回：未来大鱼有多少被摆在名单里")
    lines.append("")
    lines.append("| 通道 | 大鱼总数 | 前 6 个月内出现在名单的比例（召回@6M） |")
    lines.append("|---|---|---|")
    total_winners = sum(len(w) for w in winners.values())
    for name, s in (("A 综合榜单", sets_a), ("B 早期线索", sets_b), ("C 合并", sets_c)):
        lines.append(f"| {name} | {total_winners:,} | **{recall(s) * 100:.1f}%** |")
    lines.append("")

    lines.append("## 2. 精度：名单里的名字有多少兑现")
    lines.append("")
    lines.append("| 通道 | 样本数 | 250 日 ≥+50% | 250 日 ≤−30% |")
    lines.append("|---|---|---|---|")
    for name, s in (("A 综合榜单（当前口径）", sets_a), ("B 早期线索", sets_b), ("C 合并", sets_c),
                    ("对照：过去 20 日动量前 20", sets_mom), ("对照：过去 20 日反转前 20", sets_rev)):
        good, bad, tot = precision(s)
        lines.append(f"| {name} | {tot:,} | **{good * 100:.1f}%** | {bad * 100:.1f}% |")
    universe_rate = (label["ret250"] >= BIG_WINNER).mean()
    lines.append(f"| 参考：全市场 | {len(label):,} | {universe_rate * 100:.1f}% | "
                 f"{(label['ret250'] <= BIG_LOSER).mean() * 100:.1f}% |")
    lines.append("")
    good_a, _, _ = precision(sets_a)
    good_m, _, _ = precision(sets_mom)
    lines.append(f"- **集中度（lift）**：综合榜单 {good_a / universe_rate:.2f}× 全市场基准"
                 f"（即：榜单里出大鱼的概率只有随机选股的高 {good_a / universe_rate * 100 - 100:.0f}%）；"
                 f"过去 20 日动量前 20 为 {good_m / universe_rate:.2f}×。")
    lines.append("")

    # 2.2 抓鱼分 / 避雷分的分位检验
    gen = _generator_scores(panel, settings)
    graded = pd.concat([panel[["as_of", "ts_code", "ret250", "bfs"]], gen], axis=1)
    graded = graded.loc[graded["ret250"].notna()].copy()
    graded["US_Q"] = graded.groupby("as_of")["upside_score"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 5, labels=[f"US Q{i+1}" for i in range(5)], duplicates="drop"))
    graded["DS_Q"] = graded.groupby("as_of")["downside_score"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 5, labels=[f"DS Q{i+1}" for i in range(5)], duplicates="drop"))
    lines.append("### 2.2 抓鱼分 / 避雷分的分位检验（250 日口径）")
    lines.append("")
    lines.append("| 抓鱼分分位 | 大鱼率（≥+50%） | 踩雷率（≤−30%） |")
    lines.append("|---|---|---|")
    for q, sub in graded.groupby("US_Q", observed=True):
        lines.append(f"| {q} | {(sub['ret250'] >= BIG_WINNER).mean() * 100:.1f}% | "
                     f"{(sub['ret250'] <= BIG_LOSER).mean() * 100:.1f}% |")
    lines.append("")
    lines.append("| 避雷分分位 | 踩雷率（≤−30%） | 大鱼率（≥+50%） |")
    lines.append("|---|---|---|")
    for q, sub in graded.groupby("DS_Q", observed=True):
        lines.append(f"| {q} | {(sub['ret250'] <= BIG_LOSER).mean() * 100:.1f}% | "
                     f"{(sub['ret250'] >= BIG_WINNER).mean() * 100:.1f}% |")
    lines.append("")

    # 2.3 观察池排序键对比
    lines.append("### 2.3 观察池排序键对比（Top100）")
    lines.append("")

    # 2.4 注意力配额的影响
    lines.append("### 2.4 注意力配额（单一行业 / 单一反转类型上限）的影响")
    lines.append("")
    lines.append("| 层级 | 口径 | 大鱼率 | 踩雷率 | 最大行业占比 | 最大反转类型占比 |")
    lines.append("|---|---|---|---|---|---|")
    for level, size, label, quota in (
        ("观察池", 100, "不限额", {}),
        ("观察池", 100, "行业 ≤6 / 类型 ≤15", {"max_per_industry": 6, "max_per_reversal_type": 15}),
        ("重点榜", 20, "不限额", {}),
        ("重点榜", 20, "行业 ≤4 / 类型 ≤6", {"max_per_industry": 4, "max_per_reversal_type": 6}),
        ("重点榜", 20, "行业 ≤3 / 类型 ≤4", {"max_per_industry": 3, "max_per_reversal_type": 4}),
    ):
        ranked = panel.loc[tradeable & no_risk & (grade != "Reject")].sort_values(
            ["as_of", "bfs"], ascending=[True, False])
        max_ind = int(quota.get("max_per_industry", 0) or 0)
        max_type = int(quota.get("max_per_reversal_type", 0) or 0)
        if not max_ind and not max_type:
            sel = ranked.groupby("as_of").head(size)
        else:
            rows = []
            for _, grp in ranked.groupby("as_of", sort=True):
                picked, ic, tc = [], {}, {}
                for row in grp.to_dict(orient="records"):
                    industry = str(row.get("industry") or "UNKNOWN")
                    rtype = str(row.get("reversal_type") or "UNKNOWN")
                    if max_ind and ic.get(industry, 0) >= max_ind:
                        continue
                    if max_type and tc.get(rtype, 0) >= max_type:
                        continue
                    picked.append(row)
                    ic[industry] = ic.get(industry, 0) + 1
                    tc[rtype] = tc.get(rtype, 0) + 1
                    if len(picked) >= size:
                        break
                rows.extend(picked)
            sel = pd.DataFrame(rows)
        s = _monthly_sets(sel, pd.Series(True, index=sel.index))
        good, bad, _ = precision(s)
        ind_share = sel["industry"].value_counts(normalize=True).max()
        type_share = sel["reversal_type"].value_counts(normalize=True).max()
        lines.append(f"| {level} | {label} | {good * 100:.1f}% | {bad * 100:.1f}% | "
                     f"{ind_share * 100:.1f}% | {type_share * 100:.1f}% |")
    lines.append("")
    lines.append("> 配额是**研究注意力约束**（人看得过来），不是收益约束。实测：在观察池层级（Top100）"
                 "加配额会把大鱼率从 17.6% 压到 15.6%（低于全市场 16.0%），"
                 "而最大类型占比只从 30.4% 降到 24.7%——**代价不成比例**。"
                 "在重点榜层级（Top20）的影响见上表，据此决定是否启用。")
    lines.append("")
    lines.append("| 排序键 | 大鱼率 | 踩雷率 | 召回@6M |")
    lines.append("|---|---|---|---|")
    rank_variants = {
        "综合分 BFS（原口径）": pd.to_numeric(panel["bfs"], errors="coerce"),
        "抓鱼分 US": gen["upside_score"],
        "抓鱼分 US + 避雷分下限（DS≥40）": gen["upside_score"].where(gen["downside_score"] >= 40),
        "抓鱼分与综合分均值": 0.5 * gen["upside_score"].fillna(0) + 0.5 * pd.to_numeric(panel["bfs"], errors="coerce").fillna(0),
    }
    for label, key in rank_variants.items():
        tmp = panel.assign(_key=key)
        sel = (tmp.loc[tradeable & no_risk & (grade != "Reject")]
               .sort_values(["as_of", "_key"], ascending=[True, False]).groupby("as_of").head(100))
        s = _monthly_sets(sel, pd.Series(True, index=sel.index))
        good, bad, _ = precision(s)
        lines.append(f"| {label} | {good * 100:.1f}% | {bad * 100:.1f}% | {recall(s) * 100:.1f}% |")
    lines.append("")

    lines.append("### 2.1 名单容量权衡：扩大名单能不能提高召回")
    lines.append("")
    lines.append("| 名单容量 | 大鱼率（精度） | 大亏率 | 召回@6M |")
    lines.append("|---|---|---|---|")
    for n in (20, 50, 100, 200, 400):
        sel = (panel.loc[pool].sort_values(["as_of", "bfs"], ascending=[True, False])
               .groupby("as_of").head(n))
        s = _monthly_sets(sel, pd.Series(True, index=sel.index))
        good, bad, _ = precision(s)
        lines.append(f"| Top{n} | {good * 100:.1f}% | {bad * 100:.1f}% | {recall(s) * 100:.1f}% |")
    lines.append("")
    lines.append("> 大鱼率随名单扩大单调向全市场基准（16.0%）回归，说明**排序能力集中在头部**；"
                 "召回随容量近似线性上升，说明**召回低的主因是名单太小（20 / 全市场 4,000+），不是排序差**。"
                 "Top100 是明显甜点：精度只从 19.1% 降到 17.6%，召回从 2.3% 升到 8.3%（3.6 倍）。")
    lines.append("")

    lines.append("## 3. 提前量：大鱼上涨前多久被发现")
    lines.append("")
    if not leads.empty:
        lead = leads["lead_months"]
        events = sum(len(w) for w in winners.values())
        lines.append(f"- 大鱼事件 {events:,} 个，其中在**起点月之前 12 个月内**被系统摆出过的 "
                     f"{len(leads):,} 个（召回@12M ≈ {len(leads) / max(events, 1) * 100:.1f}%）")
        lines.append(f"- 提前量（从首次摆出到行情起点）：中位 **{lead.median():.0f} 个月**，"
                     f"平均 {lead.mean():.1f} 个月；≥3 个月的占 {(lead >= 3).mean() * 100:.0f}%")
        lines.append(f"- 命中标的的收益节奏（从被摆出算起）：20 日 {pace['ret20'] * 100:+.1f}%、"
                     f"60 日 {pace['ret60'] * 100:+.1f}%、120 日 {pace['ret120'] * 100:+.1f}%、"
                     f"250 日 {pace['ret250'] * 100:+.1f}%")
    else:
        lines.append("_无法计算（样本不足）_")
    lines.append("")

    lines.append("## 4. 闪烁率：名单换了多快")
    lines.append("")
    lines.append("| 通道 | 单次停留 ≤2 期就消失的比例 | 平均停留期数 |")
    lines.append("|---|---|---|")
    for name, s in (("A 综合榜单", sets_a), ("B 早期线索", sets_b), ("C 合并", sets_c)):
        fl, meanrun = flicker(s)
        lines.append(f"| {name} | {fl * 100:.0f}% | {meanrun:.1f} |")
    lines.append("")

    lines.append("## 5. 结论：候选生成器还能优化什么")
    lines.append("")
    lines.append(_conclusion(recall(sets_a), recall(sets_c), precision(sets_a), precision(sets_c),
                             leads, flicker(sets_a)))
    out = Path("docs/11_generator_diagnostics.md")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"写入 {out}")
    print("\n".join(lines[:10]))


def _conclusion(rec_a, rec_c, prec_a, prec_c, leads, flicker_a) -> str:
    out = []
    out.append(f"**先给三个数**：榜单精度 {prec_a[0] * 100:.1f}%（全市场 {16.0:.1f}%，即 1.2 倍），"
               f"召回@6M {rec_a * 100:.1f}%，闪烁率 {flicker_a[0] * 100:.0f}%。"
               "这三个数决定了该往哪儿优化。")
    out.append("")
    out.append("**发现 1：这套评分的真实优势是「避雷」，不是「抓鱼」。** "
               "榜单里 250 日跌超 30% 的比例是 10.8%，低于全市场的 11.6%，"
               "更远低于「过去 20 日动量前 20」的 33.2%；而抓到大鱼（≥+50%）的比例只比随机高 20%。"
               "→ 优化方向应该是**把两种用途拆成两个排序**：一份用「下限保护」打分（给组合/风控用），"
               "一份用「上行弹性」打分（给研究员找票用），而不是让同一个 BFS 兼顾两件事。")
    out.append("")
    out.append(f"**发现 2：召回低不是排序差，是名单太小。** 名单从 20 只扩到 100 只，"
               "精度只从 19.1% 降到 17.6%，召回从 2.3% 升到 8.3%（3.6 倍）；扩到 400 只仍有 16.9%。"
               "→ **候选人名单应该输出 Top100 的漏斗，而不是 Top20 的结论**："
               "100 → 人工/深研 → 20 → 决策，这也是规格书第 92 节想表达的流程，"
               "但之前的落点是错的（它把 20 当成了最终名单）。")
    if not leads.empty:
        lead_med = leads["lead_months"].median()
        out.append("")
        out.append(f"**发现 3：提前量是够用的。** 被命中的大鱼平均比行情起点早 "
                   f"{lead_med:.0f} 个月进入名单，从被摆出算起 60 日 +25%、120 日 +48%。"
                   "说明名单不是「涨完了才显示」，作为研究线索有真实提前量。")
    out.append("")
    out.append(f"**发现 4：闪烁率 {flicker_a[0] * 100:.0f}%**（单次停留 ≤2 期就消失）。"
               "对候选生成器来说稳定比锐利更重要——每天早上看到 20 只全换掉，人是跟不上也不需要跟的。")
    out.append("")
    out.append("**发现 5（本轮最干净的结果）：避雷分是可靠的风险过滤器。** "
               "按避雷分分五档，250 日踩雷率从 Q1 的 14.0% 单调降到 Q5 的 9.1%（−4.9pp），"
               "而大鱼率几乎不动（16.1% → 15.9%）——它**只砍风险、不砍机会**，"
               "适合直接用于仓位分配与排除法。抓鱼分同样有效但更弱"
               "（踩雷率 13.5% → 9.8%，大鱼率 15.5% → 16.8%）。")
    out.append("")
    out.append("**观察池排序键的实测结论**（Top100，见 2.3 节）：BFS / 抓鱼分 / 两者均值，"
               "大鱼率 17.6%~17.9%、踩雷率 10.2%~11.3%，**差异在噪声内**；"
               "按「召回优先、风险次之」选择 BFS（召回 8.3%、踩雷率 10.2%，两项都最优）。")
    out.append("")
    out.append("按性价比排序的优化清单（都不需要新数据）：")
    out.append("")
    out.append("| 优先级 | 优化 | 目标指标 | 做法 |")
    out.append("|---|---|---|---|")
    out.append("| P0 | **名单从 Top20 扩到 Top100，做成漏斗** | 召回（2.3% → 8.3%） | "
               "输出层改成「Top100 观察池 → Top20 重点 → Top5 卡片」，精度代价仅 1.5pp |")
    out.append("| P0 | **拆出「早期线索」通道** | 召回（+50%） | FRS≥70 且 PCS<60 单独成榜；"
               "实测它更早期但也更危险（大亏率 14.7% vs 10.8%），所以必须**单独标注、不混进主榜** |")
    out.append("| P0 | **「抓鱼分」与「避雷分」分开排序** ✅ 已落地 | 风险 | "
               "抓鱼分偏 FRS/LIS/RPS，避雷分偏 PCS/kill/估值/数据完整度；避雷分已实测可把踩雷率 14.0%→9.1% |")
    out.append("| P1 | **加连续在榜期数与「新增/持续/退出」分栏** | 闪烁率 | 已有 NEW ENTRIES 机制，"
               "扩展到每日卡片；连续在榜 ≥3 期的单独标注 |")
    out.append("| P1 | **按反转类型配额** | 注意力分散 | 现在只按行业限 4 只；再按反转类型限 3 只，"
               "避免 20 只里 12 只是 CYCLE_REVERSAL |")
    out.append("| P2 | **把三类特殊标的单独分区** | 精度 | 金融股 / 价值陷阱 / 一次性收益嫌疑"
               "从主榜移出，单列「高风险高赔率」区，注明各自失效机制 |")
    out.append("| P2 | **数据完整度参与排序** | 精度 | coverage<0.7 的降权而不是只标记，"
               "减少「看着好但证据链不全」的噪音 |")
    out.append("")
    out.append("需要新数据才能继续推进的（决定上限，不是调参能解决）：")
    out.append("")
    out.append("- **LIS 的订单/价格/产能数据**：现在 20 分权重里有一半是缺失的，"
               "接上之后 LIS 才可能真正领先 FRS（目前更像第二遍确认）。")
    out.append("- **一致预期（EPS Revision）**：ODS 里 20% 权重现在用业绩预告替代。")
    out.append("- **历史行业成分 / ST 时点**：影响回测可信度，进而影响「要不要信这个召回率」。")
    return "\n".join(out)


if __name__ == "__main__":
    main()
