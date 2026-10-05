"""完整链路（规格书第 55 节）：
Market Regime → ODS → FRS → LIS → PCS → RPS → BFS → Kill → Lifecycle → 输出。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

import numpy as np
import pandas as pd

from .. import OUTPUT_DIR, PROCESSED_DIR
from ..config import Settings, load_settings, resolve_end_date
from ..storage import latest_local_trade_date
from ..core import (big_fish_score, catalyst, filters as filters_mod, fundamental_reversal,
                    kill_signal, leading_indicator, lifecycle, market_regime,
                    opportunity_density, price_confirmation, valuation)
from ..report.cards import render_cards
from ..report.daily_brief import render_daily_brief
from .dataset import load_dataset

log = logging.getLogger(__name__)


def _log(message: str) -> None:
    print(f"[run] {message}", flush=True)


def run_all(settings: Settings | None = None, as_of: str | None = None,
            top_n: int | None = None) -> dict:
    settings = settings or load_settings()
    as_of = as_of or latest_local_trade_date(resolve_end_date(settings)) or resolve_end_date(settings)
    pool = settings.path("action.pool", {}) or {}
    top_n = top_n or int(pool.get("top_n_ranking", 20))

    _log(f"加载数据集 as_of={as_of}")
    dataset = load_dataset(settings, as_of=as_of)
    _log(f"股票池 {len(dataset.universe)}，行情 {len(dataset.prices)} 行，财务 {len(dataset.fundamentals)} 行")
    scored = score_dataset(dataset)
    return _finalize(dataset, scored, settings, as_of, top_n)


def score_dataset(dataset, settings: Settings | None = None, narratives: bool = True) -> dict:
    """对给定 as-of 数据集跑完六层评分（日频跑批与回测共用）。

    返回 dict：regime / ods_industry / ods_stock / frs / lis / pcs / rps / catalyst / kill /
    filters / full。
    """
    settings = settings or dataset.settings
    _log("LEVEL 1 Market Regime")
    regime = market_regime.compute_market_regime(dataset)
    _log(f"市场状态 {regime['regime']}（{regime['regime_score']:.1f}）")

    _log("LEVEL 2 Opportunity Density")
    ods_industry, ods_stock = opportunity_density.compute_ods(dataset)
    _log(f"行业 ODS 完成：{len(ods_industry)} 个申万一级行业，覆盖率中位数 "
         f"{ods_industry['coverage'].median() if not ods_industry.empty else float('nan'):.2f}")

    _log("LEVEL 3 Fundamental Reversal")
    frs = fundamental_reversal.compute_frs(dataset)
    _log(f"FRS 完成：{len(frs)} 只，STRONG/CONFIRMED "
         f"{int((frs['frs_stage'].isin(['STRONG REVERSAL', 'CONFIRMED REVERSAL'])).sum())} 只")

    _log("LEVEL 4 Leading Indicator")
    lis = leading_indicator.compute_lis(dataset)

    _log("LEVEL 5 Price Confirmation")
    pcs = price_confirmation.compute_pcs(dataset)

    _log("LEVEL 6 Valuation + Catalyst")
    rps = valuation.compute_rps(dataset)
    cs = catalyst.compute_catalyst(dataset)

    _log("过滤器（Value Trap / Fake Turnaround / Risk Block）")
    filt = filters_mod.compute_filters(dataset, frs, rps, ods_stock)

    _log("Kill Signal")
    kill = kill_signal.compute_kill(dataset, pcs, ods_stock)

    _log("汇总 Big Fish Score")
    full = big_fish_score.assemble(dataset, regime, ods_industry, ods_stock, frs, lis, pcs, rps,
                                   cs, kill, filt, narratives=narratives)
    return {
        "regime": regime,
        "ods_industry": ods_industry,
        "ods_stock": ods_stock,
        "frs": frs,
        "lis": lis,
        "pcs": pcs,
        "rps": rps,
        "catalyst": cs,
        "kill": kill,
        "filters": filt,
        "full": full,
    }


def _finalize(dataset, scored: dict, settings: Settings, as_of: str, top_n: int) -> dict:
    regime = scored["regime"]
    full = scored["full"]
    ods_industry = scored["ods_industry"]
    lis, pcs, rps, cs, kill = scored["lis"], scored["pcs"], scored["rps"], scored["catalyst"], scored["kill"]
    pool = settings.path("action.pool", {}) or {}
    full["as_of"] = as_of
    full["trade_date"] = as_of

    # ---------------- 候选生成器漏斗（观察池 / 重点 / 卡片 / 早期线索） ----------------
    history = _load_history()
    funnel = big_fish_score.build_funnel(full, settings, history)
    watchlist, ranking, early, cards = (funnel["watchlist"], funnel["focus"],
                                        funnel["early"], funnel["cards"])
    for table in (watchlist, ranking, early, cards):
        if not table.empty:
            table["as_of"] = as_of

    # ---------------- 落盘 ----------------
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    full.to_parquet(PROCESSED_DIR / f"bigfish_score_{as_of}.parquet", index=False)
    full.to_parquet(PROCESSED_DIR / "bigfish_score_latest.parquet", index=False)
    if not ods_industry.empty:
        ods_industry.to_parquet(PROCESSED_DIR / f"industry_ods_{as_of}.parquet", index=False)

    previous = _load_previous(as_of)
    _append_history(watchlist, as_of)

    ranking.to_csv(OUTPUT_DIR / f"bigfish_ranking_{as_of}.csv", index=False, encoding="utf-8-sig")
    watchlist.to_csv(OUTPUT_DIR / f"bigfish_watchlist_{as_of}.csv", index=False, encoding="utf-8-sig")
    if not early.empty:
        early.to_csv(OUTPUT_DIR / f"bigfish_early_{as_of}.csv", index=False, encoding="utf-8-sig")
    if not ods_industry.empty:
        ods_industry.to_csv(OUTPUT_DIR / f"opportunity_ponds_{as_of}.csv", index=False, encoding="utf-8-sig")

    (OUTPUT_DIR / f"bigfish_cards_{as_of}.md").write_text(
        render_cards(cards, len(cards)), encoding="utf-8")
    (OUTPUT_DIR / f"bigfish_ranking_{as_of}.md").write_text(
        _render_ranking(ranking, as_of) + _render_early(early, as_of), encoding="utf-8")
    (OUTPUT_DIR / f"daily_brief_{as_of}.md").write_text(
        render_daily_brief(as_of, regime, ods_industry, watchlist, ranking, early, previous, kill),
        encoding="utf-8")

    interface = {
        "as_of": as_of,
        "strategy": "fundamental_turnaround",
        "market_regime": regime["regime"],
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "candidates": [big_fish_score.to_interface_json(row) for _, row in full.iterrows()]
        if settings.path("output.interface_full", False) else
        [big_fish_score.to_interface_json(row) for _, row in watchlist.iterrows()],
    }
    (OUTPUT_DIR / f"interface_{as_of}.json").write_text(
        json.dumps(interface, ensure_ascii=False, indent=2), encoding="utf-8")

    quality = _data_quality_report(dataset, full, ods_industry, lis, pcs, rps, cs, kill)
    (OUTPUT_DIR / f"data_quality_{as_of}.md").write_text(quality, encoding="utf-8")

    # ---------------- 基本面趋势（供网站的个股趋势页使用，只存候选名单） ----------------
    trend_codes = set(watchlist["ts_code"]) | set(early["ts_code"])
    trend = _fundamental_trend(dataset, trend_codes)
    if not trend.empty:
        trend.to_parquet(PROCESSED_DIR / f"fundamental_trend_{as_of}.parquet", index=False)
        trend.to_parquet(PROCESSED_DIR / "fundamental_trend_latest.parquet", index=False)

    summary = {
        "as_of": as_of,
        "regime": regime["regime"],
        "universe": int(len(dataset.universe)),
        "scored": int(full["bfs"].notna().sum()),
        "watchlist_rows": int(len(watchlist)),
        "ranking_rows": int(len(ranking)),
        "early_rows": int(len(early)),
        "grades": ranking["grade"].value_counts().to_dict() if not ranking.empty else {},
        "stages": ranking["stage"].value_counts().to_dict() if not ranking.empty else {},
        "kill_alerts_in_pool": int((ranking["kill_level"].isin(["WARNING", "REDUCE", "EXIT"])).sum())
        if not ranking.empty and "kill_level" in ranking.columns else 0,
        "kill_alerts_market_wide": int((full["kill_level"].isin(["WARNING", "REDUCE", "EXIT"])).sum()),
        "top_industries": ods_industry.head(5)[["industry_name", "ods"]].to_dict("records")
        if not ods_industry.empty else [],
    }
    (PROCESSED_DIR / "run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _log(f"完成：观察池 {len(watchlist)} 只 / 重点 {len(ranking)} 只 / 早期线索 {len(early)} 只，"
         f"Top1 {ranking.iloc[0]['name'] if not ranking.empty else '-'}")
    return {"full": full, "watchlist": watchlist, "ranking": ranking, "early": early,
            "ods_industry": ods_industry, "regime": regime, "kill": kill, "summary": summary}


def _fundamental_trend(dataset, ts_codes: set[str], quarters: int = 12) -> pd.DataFrame:
    """截取候选股票的最近 N 个季度财务趋势（供看板画图，避免看板重算全量面板）。"""
    panel = getattr(dataset, "fundamentals", None)
    if panel is None or panel.empty or not ts_codes:
        return pd.DataFrame()
    cols = ["ts_code", "end_date", "ann_date", "revenue", "n_income_attr_p", "gross_margin",
            "net_margin", "n_cashflow_act", "ttm_revenue", "ttm_np", "ttm_revenue_yoy",
            "revenue_yoy", "np_yoy", "gross_margin_chg_yoy", "inventory_to_revenue",
            "receivable_yoy", "contract_liab_yoy", "debt_to_assets", "roe_ytd", "invest_income"]
    keep = [c for c in cols if c in panel.columns]
    sub = panel.loc[panel["ts_code"].isin(ts_codes), keep].copy()
    if sub.empty:
        return sub
    sub = sub.sort_values(["ts_code", "end_date"]).groupby("ts_code").tail(quarters)
    for col in ("revenue", "n_income_attr_p", "n_cashflow_act", "ttm_revenue", "ttm_np", "invest_income"):
        if col in sub.columns:
            sub[col] = pd.to_numeric(sub[col], errors="coerce") / 1e8   # 元 → 亿元，便于画图
    return sub.reset_index(drop=True)


def _render_early(early: pd.DataFrame, as_of: str) -> str:
    """早期线索榜单独成段（更早但更险，必须与主榜分开看）。"""
    if early is None or early.empty:
        return f"\n## 早期线索榜（FRS 已确认、价格尚未确认）· {as_of}\n\n_今日无符合条件的早期线索_\n"
    cols = ["early_rank", "ts_code", "name", "industry", "reversal_type", "frs", "lis",
            "pcs", "rps", "ods", "upside_score", "downside_score", "stage", "streak"]
    view = early[[c for c in cols if c in early.columns]].copy()
    for col in view.columns:
        if pd.api.types.is_float_dtype(view[col]):
            view[col] = view[col].round(1)
    lines = [f"\n## 早期线索榜（FRS 已确认、价格尚未确认）· {as_of}", "",
             "| " + " | ".join(view.columns) + " |",
             "|" + "|".join(["---"] * len(view.columns)) + "|"]
    for record in view.itertuples(index=False):
        lines.append("| " + " | ".join(str(v) for v in record) + " |")
    lines.append("")
    lines.append("> 这一榜是「基本面已经反转、但市场还没反应」的早期名单，**实测更早也更险**"
                 "（250 日踩雷率 14.7% vs 主榜 10.8%），因此不并入主榜，"
                 "只作为研究提前量使用。")
    return "\n".join(lines) + "\n"


def _render_ranking(ranking: pd.DataFrame, as_of: str) -> str:
    if ranking.empty:
        return f"# Big Fish Ranking · {as_of}\n\n_无候选_\n"
    cols = ["rank", "ts_code", "name", "industry", "reversal_type", "ods", "frs", "lis", "pcs",
            "rps", "catalyst", "bfs", "grade", "stage", "kill_count", "action"]
    view = ranking[[c for c in cols if c in ranking.columns]].copy()
    for col in view.columns:
        if pd.api.types.is_float_dtype(view[col]):
            view[col] = view[col].round(1)
    header = "| " + " | ".join(view.columns) + " |"
    sep = "|" + "|".join(["---"] * len(view.columns)) + "|"
    rows = ["| " + " | ".join(str(v) for v in rec) + " |" for rec in view.itertuples(index=False)]
    note = ("\n> BFS = 0.30·FRS + 0.20·LIS + 0.20·PCS + 0.10·ODS + 0.15·RPS + 0.05·Catalyst；"
            "S 级另需 FRS≥75、LIS≥70、PCS≥70 且无重大风险（规格书第 32~34 节）。\n")
    return "\n".join([f"# Big Fish Ranking · {as_of}", "", header, sep] + rows) + "\n" + note


def _load_history() -> pd.DataFrame | None:
    history = PROCESSED_DIR / "bigfish_history.parquet"
    if not history.exists():
        return None
    try:
        return pd.read_parquet(history)
    except Exception:  # noqa: BLE001
        return None


def _load_previous(as_of: str) -> pd.DataFrame | None:
    history = PROCESSED_DIR / "bigfish_history.parquet"
    if not history.exists():
        return None
    try:
        hist = pd.read_parquet(history)
    except Exception:  # noqa: BLE001
        return None
    if hist.empty or "as_of" not in hist.columns:
        return None
    dates = sorted(d for d in hist["as_of"].astype(str).unique() if d < as_of)
    if not dates:
        return None
    return hist.loc[hist["as_of"].astype(str) == dates[-1]]


def _append_history(watchlist: pd.DataFrame, as_of: str, keep: int = 250) -> None:
    """把当日**观察池**写入历史（用于连续在榜期数与次日的新增/退出对比）。"""
    history = PROCESSED_DIR / "bigfish_history.parquet"
    cols = ["as_of", "ts_code", "name", "industry", "pool_rank", "upside_score", "downside_score",
            "bfs", "frs", "lis", "pcs", "ods", "rps", "grade", "stage", "action",
            "channel", "kill_count", "kill_level", "streak"]
    snapshot = watchlist[[c for c in cols if c in watchlist.columns]].copy()
    if history.exists():
        try:
            old = pd.read_parquet(history)
            old = old.loc[old["as_of"].astype(str) != as_of] if "as_of" in old.columns else old
            snapshot = pd.concat([old, snapshot], ignore_index=True)
        except Exception:  # noqa: BLE001
            pass
    dates = sorted(snapshot["as_of"].astype(str).unique())[-keep:]
    snapshot = snapshot.loc[snapshot["as_of"].astype(str).isin(dates)]
    snapshot.to_parquet(history, index=False)


def _data_quality_report(dataset, full, ods_industry, lis, pcs, rps, cs, kill) -> str:
    lines = [f"# 数据质量与缺口 · {dataset.as_of}", ""]
    lines.append("## 覆盖率")
    rows = [
        ("个股行情", len(dataset.prices)),
        ("每日指标", len(dataset.basic)),
        ("财务面板（PIT）", len(dataset.fundamentals)),
        ("业绩预告", len(dataset.forecast)),
        ("资金流（大单）", len(dataset.moneyflow)),
        ("股东增减持", len(dataset.holder_trades)),
        ("回购", len(dataset.repurchase)),
    ]
    lines.append("| 数据集 | 行数 |")
    lines.append("|---|---|")
    for name, n in rows:
        lines.append(f"| {name} | {n} |")
    lines.append("")
    lines.append("## 分项覆盖率（各评分模块）")
    lines.append("| 模块 | 覆盖率中位数 | 有效标的数 |")
    lines.append("|---|---|---|")
    for label, frame, col in (("FRS 基本面反转", full, "frs_coverage"),
                              ("LIS 前瞻指标", lis, "lis_coverage"),
                              ("PCS 价格确认", pcs, "pcs_coverage"),
                              ("Catalyst 催化", cs, "catalyst_coverage")):
        if frame is not None and not frame.empty and col in frame.columns:
            lines.append(f"| {label} | {frame[col].median():.2f} | {int(frame[col].notna().sum())} |")
    lines.append("")
    lines.append("## 已知数据缺口（如实标注，不做主观填充）")
    gaps = [
        "一致预期（EPS Revision）：账号无权限，用业绩预告区间中枢 + 预告广度替代（规格书第 6.3 节允许）",
        "行业高频数据（开工率 / 产能利用率 / 现货价格）：不在 Tushare 覆盖范围，LIS 中该分项不参与打分",
        "同花顺/东财热度、龙虎榜机构席位：无权限，聪明钱因子使用大单+超大单资金流（moneyflow）",
        "质押比例 / 监管调查 / 造假：当前未接入，Risk Block 仅覆盖 ST 与流动性",
    ]
    for gap in gaps:
        lines.append(f"- {gap}")
    mismatch = full.loc[(full["data_flag"] == "DATA INCOMPLETE") & full["tradeable"].fillna(False)]
    lines.append("")
    lines.append(f"## DATA INCOMPLETE 标的（可入池）：{len(mismatch)} / {int(full['tradeable'].fillna(False).sum())}")
    if not mismatch.empty:
        sample = mismatch.head(15)[["ts_code", "name", "frs", "lis", "pcs", "rps", "bfs", "data_confidence"]]
        sample = sample.copy()
        for col in sample.columns:
            if pd.api.types.is_float_dtype(sample[col]):
                sample[col] = sample[col].round(1)
        lines.append("| " + " | ".join(sample.columns) + " |")
        lines.append("|" + "|".join(["---"] * len(sample.columns)) + "|")
        for record in sample.itertuples(index=False):
            lines.append("| " + " | ".join(str(v) for v in record) + " |")
    return "\n".join(lines) + "\n"
