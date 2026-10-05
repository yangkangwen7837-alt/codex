"""Big Fish Score 汇总（规格书第 32~35、51~54、59~61、77 节）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..pipeline.dataset import BigFishDataset
from ..utils import data_flag, nan_weighted_mean, weighted_frame
from .common import forecast_snapshot, price_features, stock_industry_map, tail_wide
from .fundamental_reversal import classify_reversal_type
from .lifecycle import classify_stage
from .trade_plan import build_trade_plan


def assemble(dataset: BigFishDataset, regime: dict, ods_industry: pd.DataFrame, ods_stock: pd.DataFrame,
             frs: pd.DataFrame, lis: pd.DataFrame, pcs: pd.DataFrame, rps: pd.DataFrame,
             catalyst: pd.DataFrame, kill: pd.DataFrame, filters: pd.DataFrame,
             narratives: bool = True) -> pd.DataFrame:
    settings = dataset.settings
    weights = dict(settings.path("bfs.weights", {}))

    frames = [frs, lis, pcs, rps, catalyst, kill, filters, ods_stock]
    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame()
    base = frames[0].copy()
    for frame in frames[1:]:
        if "ts_code" not in frame.columns:
            continue
        deduped = frame.drop_duplicates(subset=["ts_code"])
        overlap = [c for c in deduped.columns if c in base.columns and c != "ts_code"]
        base = base.merge(deduped.drop(columns=overlap, errors="ignore"), on="ts_code", how="outer")

    pf = price_features(dataset)
    if not pf.empty:
        base = base.merge(pf, on="ts_code", how="left")

    # ---------------- BFS ----------------
    records = base.to_dict(orient="records")
    bfs_components = pd.DataFrame({
        key: pd.to_numeric(base[key], errors="coerce") if key in base.columns
        else pd.Series(np.nan, index=base.index)
        for key in weights
    }, index=base.index)
    score, coverage = weighted_frame(bfs_components, weights)
    base["bfs"] = score
    base["bfs_coverage"] = coverage

    # ---------------- 等级（规格书第 33/34 节） ----------------
    grades = settings.path("bfs.grades", {}) or {}
    gate = settings.path("bfs.s_gate", {}) or {}

    # 向量化评级（回测里每个调仓日要算 6000+ 只，逐行 apply 太慢）
    bfs_col = pd.to_numeric(base.get("bfs"), errors="coerce")
    frs_col = pd.to_numeric(base.get("frs"), errors="coerce")
    lis_col = pd.to_numeric(base.get("lis"), errors="coerce")
    pcs_col = pd.to_numeric(base.get("pcs"), errors="coerce")
    clean_gate = (
        (pd.to_numeric(base.get("kill_count", 0), errors="coerce").fillna(1) == 0)
        & ~base.get("risk_block", pd.Series(False, index=base.index)).fillna(False).astype(bool)
        & ~base.get("is_fake_turnaround", pd.Series(False, index=base.index)).fillna(False).astype(bool)
        & ~base.get("is_value_trap", pd.Series(False, index=base.index)).fillna(False).astype(bool)
    )
    hard_gate = (
        (frs_col >= gate.get("frs", 75)) & (lis_col >= gate.get("lis", 70))
        & (pcs_col >= gate.get("pcs", 70)) & clean_gate
    )
    base["grade"] = np.select(
        [
            bfs_col.isna(),
            (bfs_col >= grades.get("S", 85)) & hard_gate,
            bfs_col >= grades.get("S", 85),
            bfs_col >= grades.get("A+", 80),
            bfs_col >= grades.get("A", 75),
            bfs_col >= grades.get("B", 65),
            bfs_col >= grades.get("C", 55),
        ],
        ["Reject", "S", "A+", "A+", "A", "B", "C"],
        default="Reject",
    )

    # ---------------- 硬约束降级（回测失败案例库驱动） ----------------
    if "expensive_entry" in base.columns:
        expensive = base["expensive_entry"].fillna(False).astype(bool)
        base.loc[expensive & base["grade"].isin(["S", "A+", "A"]), "grade"] = "B"
    if "severe_expensive" in base.columns:
        severe = base["severe_expensive"].fillna(False).astype(bool)
        base.loc[severe & base["grade"].isin(["S", "A+", "A", "B"]), "grade"] = "C"
    base["gate_notes"] = ""
    notes = pd.Series("", index=base.index, dtype=object)
    if "expensive_entry" in base.columns:
        notes = notes.mask(base["expensive_entry"].fillna(False).astype(bool),
                           "估值硬约束：PE 高于行业中位数 1.5 倍或 Base 上行空间 <15% → 封顶 B")
    if "severe_expensive" in base.columns:
        notes = notes.mask(base["severe_expensive"].fillna(False).astype(bool),
                           "估值硬约束（严重）：封顶 C")
    if "low_base_penalty" in base.columns:
        notes = notes.mask(base["low_base_penalty"].fillna(False).astype(bool),
                           "低基数（需说明；已回测验证不构成降级理由，见 docs/07）")

    # 回测验证（docs/07 第 4 节）：VALUE_TRAP / FAKE_TURNAROUND / 金融股 这三类
    # 在 FRS 前 20% 内部的 60 日净收益**不低于**其余样本（分别 +0.77~0.95pp / +0.78pp），
    # 即"剔除它们"会剔掉更好的样本。因此从"硬排除"降级为"封顶 B + 披露"：
    # 规格书第 49/50 节的识别要求保留，但不再把它们踢出候选池。
    capped_notes = []
    if "is_value_trap" in base.columns:
        vt = base["is_value_trap"].fillna(False).astype(bool)
        base.loc[vt & base["grade"].isin(["S", "A+", "A"]), "grade"] = "B"
        notes = notes.mask(vt, "VALUE_TRAP（低估值+仍恶化）：已回测验证剔除会损失收益，改为封顶 B + 披露")
        capped_notes.append("VALUE_TRAP")
    if "is_fake_turnaround" in base.columns:
        ft = base["is_fake_turnaround"].fillna(False).astype(bool)
        base.loc[ft & base["grade"].isin(["S", "A+", "A"]), "grade"] = "B"
        notes = notes.mask(ft, "假反转嫌疑（利润改善靠非经常性损益）：封顶 B + 披露")
    if "frs_template" in base.columns:
        fin = base["frs_template"].astype(str).eq("FINANCIAL").fillna(False)
        base.loc[fin & base["grade"].isin(["S", "A+", "A"]), "grade"] = "B"
        notes = notes.mask(fin, "金融行业：专用模板仍无法验证排序能力（多空差 -0.14pp）→ 封顶 B、禁止试探仓")
    base["gate_notes"] = notes

    # ---------------- 四象限（规格书第 35 节） ----------------
    frs_high = frs_col >= 70
    pcs_high = pcs_col >= 65
    base["quadrant"] = np.select(
        [frs_high & pcs_high, frs_high & ~pcs_high, ~frs_high & pcs_high],
        ["BUY ZONE", "WAIT", "SPECULATION"], default="AVOID",
    )

    # ---------------- 反转类型（规格书第 51 节） ----------------
    industry_ctx = {}
    if ods_industry is not None and not ods_industry.empty:
        for row in ods_industry.itertuples(index=False):
            industry_ctx[getattr(row, "industry_name")] = {
                "price_vs_ma60": getattr(row, "price_vs_ma60", 0),
                "inventory_down": bool(getattr(row, "med_inventory_to_revenue_chg", 0) < 0),
            }
    base["reversal_type"] = [
        classify_reversal_type(record, industry_ctx.get(record.get("industry"), {}))
        for record in records
    ]

    # ---------------- 加速确认（生命周期 ACCELERATION 需要的证据） ----------------
    wide = tail_wide(dataset.fundamentals, ["np_yoy", "revenue_yoy"], n=3)
    if not wide.empty:
        np_now = wide.get("np_yoy_t0")
        np_prev = wide.get("np_yoy_t1")
        accel = (np_now > np_prev).reindex(base["ts_code"]).to_numpy()
    else:
        accel = np.zeros(len(base), dtype=bool)
    base["acceleration_confirm"] = (
        pd.Series(accel, index=base.index).fillna(False)
        & (base.get("p_dist_ma20", pd.Series(np.nan, index=base.index)) > 1.0)
        & (base.get("frs", pd.Series(np.nan, index=base.index)) >= 70)
    )
    base.loc[base["acceleration_confirm"], "quadrant"] = "BUY ZONE"

    # ---------------- 生命周期 + 交易动作 ----------------
    base["stage"] = base.apply(lambda r: classify_stage(r, settings), axis=1)
    plans = [build_trade_plan(record, settings) for record in base.to_dict(orient="records")]
    plan_frame = pd.DataFrame(plans, index=base.index)
    base = pd.concat([base, plan_frame], axis=1)

    # ---------------- SPECIAL SITUATION（规格书第 54 节） ----------------
    base["special_situation"] = (
        (base.get("frs", 0) > 85) & (base.get("lis", 0) > 80) & (base.get("pcs", 0) > 75)
    )
    low_ods = base.get("ods", pd.Series(np.nan, index=base.index)) < 60
    base["special_situation"] = base["special_situation"] & low_ods

    # ---------------- 数据可靠性（规格书第 67 节） ----------------
    conf_cols = ["frs_coverage", "lis_coverage", "pcs_coverage", "catalyst_coverage"]
    conf = base[[c for c in conf_cols if c in base.columns]].min(axis=1)
    base["data_confidence"] = (conf * 0.9).round(2)
    base["data_flag"] = [
        data_flag(c, 0.8, 0.5) if np.isfinite(c) else "DATA INCOMPLETE" for c in base["data_confidence"]
    ]
    base["data_source"] = "tushare"
    base["update_time"] = pd.Timestamp.now().isoformat(timespec="seconds")
    base["is_point_in_time"] = True

    # ---------------- 可交易性（无名称 / 无行业 / 当日未交易 → 不入池） ----------------
    name_ok = base["name"].notna() if "name" in base.columns else pd.Series(False, index=base.index)
    close_ok = base["close"].notna() if "close" in base.columns else pd.Series(False, index=base.index)
    traded = (base["last_date"].astype(str) == dataset.as_of) if "last_date" in base.columns \
        else pd.Series(False, index=base.index)
    base["tradeable"] = name_ok.astype(bool) & close_ok.astype(bool) & traded.astype(bool)

    # ---------------- 叙事字段（规格书第 59~61 节，仅日频输出需要） ----------------
    if narratives:
        base["first_rejection"] = base.apply(_first_rejection, axis=1)
        base["what_is_priced_in"] = base.apply(_what_is_priced_in, axis=1)
        base["variant_perception"] = base.apply(_variant_perception, axis=1)
        base["next_verification"] = base.apply(_next_verification, axis=1)

    # ---------------- 抓鱼分 / 避雷分（候选生成器口径，见 docs/11） ----------------
    base = add_generator_scores(base, settings)
    return base


def add_generator_scores(base: pd.DataFrame, settings) -> pd.DataFrame:
    """拆出「抓鱼分（上行弹性）」与「避雷分（下限保护）」两个独立分数。

    依据 docs/11：同一套 BFS 同时兼顾"抓鱼"和"避雷"会两头不靠——
    实测这套评分的真实优势在下限保护（踩雷率 10.8% vs 全市场 11.6%，
    而"追 20 日涨幅"的因子是 33.2%），抓大鱼只比随机高 20%。
    """
    score_weights = settings.path("scores", {}) or {}
    upside_weights = dict((score_weights.get("upside", {}) or {}).get("weights", {}))
    downside_cfg = score_weights.get("downside", {}) or {}
    downside_weights = dict(downside_cfg.get("weights", {}))

    kill_count = pd.to_numeric(base.get("kill_count", 0), errors="coerce").fillna(0)
    kill_penalty = float(downside_cfg.get("kill_penalty_per_signal", 33))
    valuation_penalty = downside_cfg.get("valuation_penalty", {}) or {}
    expensive = base.get("expensive_entry", pd.Series(False, index=base.index)).fillna(False).astype(bool)
    severe = base.get("severe_expensive", pd.Series(False, index=base.index)).fillna(False).astype(bool)
    valuation_score = (
        100.0
        - float(valuation_penalty.get("expensive", 35)) * expensive.astype(float)
        - float(valuation_penalty.get("severe", 25)) * severe.astype(float)
    ).clip(0, 100)
    data_quality_score = (pd.to_numeric(base.get("data_confidence", np.nan), errors="coerce") * 100).clip(0, 100)

    if upside_weights:
        upside_frame = pd.DataFrame(
            {k: pd.to_numeric(base[k], errors="coerce") for k in upside_weights if k in base.columns},
            index=base.index,
        )
        us, us_cov = weighted_frame(upside_frame, {k: v for k, v in upside_weights.items() if k in upside_frame})
        base["upside_score"] = us
        base["upside_coverage"] = us_cov
    else:
        base["upside_score"] = np.nan
        base["upside_coverage"] = 0.0

    if downside_weights:
        downside_frame = pd.DataFrame({
            "pcs": pd.to_numeric(base.get("pcs", np.nan), errors="coerce"),
            "kill": (100 - kill_penalty * kill_count).clip(0, 100),
            "valuation": valuation_score,
            "data_quality": data_quality_score,
        }, index=base.index)
        ds, ds_cov = weighted_frame(downside_frame, downside_weights)
        base["downside_score"] = ds
        base["downside_coverage"] = ds_cov
    else:
        base["downside_score"] = np.nan
        base["downside_coverage"] = 0.0

    # 通道：EARLY = 基本面已确认、价格尚未确认（更早但更险，必须单独成榜）
    early_cfg = settings.path("funnel.early_channel", {}) or {}
    frs_min = float(early_cfg.get("frs_min", 70))
    pcs_max = float(early_cfg.get("pcs_max", 60))
    frs_v = pd.to_numeric(base.get("frs", np.nan), errors="coerce")
    pcs_v = pd.to_numeric(base.get("pcs", np.nan), errors="coerce")
    base["channel"] = np.where((frs_v >= frs_min) & (pcs_v < pcs_max), "EARLY", "CORE")
    base.loc[frs_v.isna(), "channel"] = "CORE"
    return base


def _first_rejection(row: pd.Series) -> str:
    if row.get("is_fake_turnaround", False):
        return "利润改善主要来自一次性收益/低基数"
    if row.get("f_revenue_yoy", np.nan) is not None and np.isfinite(row.get("f_revenue_yoy", np.nan)) \
            and row.get("f_revenue_yoy", np.nan) > 0:
        if row.get("f_contract_liab_yoy", np.nan) is not None and np.isfinite(row.get("f_contract_liab_yoy", np.nan)):
            return "下季度收入增速回落到行业中枢以下（合同负债未能继续增长）"
        return "下季度收入同比增速重新转负"
    if np.isfinite(row.get("f_gross_margin", np.nan)):
        return "毛利率未能延续改善（回到 4 季均值以下）"
    return "下一期财报显示经营趋势重新恶化"


def _what_is_priced_in(row: pd.Series) -> str:
    pe = row.get("current_pe", np.nan)
    pcs = row.get("pcs", np.nan)
    frs = row.get("frs", np.nan)
    if np.isfinite(pcs) and pcs < 60:
        return "市场仍按「低利润 / 低增长 / 行业衰退」定价，尚未反映经营拐点"
    if np.isfinite(frs) and frs >= 70 and np.isfinite(pcs) and pcs >= 70:
        return "市场已开始定价反转，但可能仍按「修复后不再增长」的稳态盈利定价"
    if np.isfinite(pe) and pe > 40:
        return "市场已给出较高估值，需要盈利兑现才能支撑"
    return "市场对反转的持续性存疑，按均值回归而非趋势上修定价"


def _variant_perception(row: pd.Series) -> str:
    consensus = row.get("what_is_priced_in", "")
    frs = row.get("frs", np.nan)
    lis = row.get("lis", np.nan)
    if np.isfinite(frs) and frs >= 70:
        return (f"市场共识：{consensus}；本系统判断：收入/利润/毛利率与领先指标"
                f"（FRS {frs:.0f} / LIS {lis:.0f}）指向经营方向性变化，而非单季波动")
    return f"市场共识：{consensus}；本系统判断：反转尚未确认（FRS {frs:.0f}）"


def _next_verification(row: pd.Series) -> str:
    period = row.get("latest_period", "")
    items = []
    if isinstance(period, str) and len(period) == 8:
        nxt = {"0331": "半年报", "0630": "三季报", "0930": "年报", "1231": "一季报"}.get(period[4:], "下一期财报")
        items.append(f"{nxt}的收入/扣非利润同比是否延续改善")
    if row.get("lis_lead_consistency") is not None and bool(row.get("lis_lead_consistency", False)):
        items.append("合同负债增速是否继续领先收入")
    items.append("毛利率是否站稳 4 季均值上方")
    return "；".join(items)


def to_interface_json(row: pd.Series) -> dict:
    """规格书第 79 节：供总控 Agent 读取的统一接口。"""
    def num(value, digits: int = 2):
        try:
            out = float(value)
        except (TypeError, ValueError):
            return None
        return round(out, digits) if np.isfinite(out) else None

    return {
        "strategy": "fundamental_turnaround",
        "ticker": row.get("ts_code"),
        "name": row.get("name") if isinstance(row.get("name"), str) else None,
        "bfs": num(row.get("bfs")),
        "upside_score": num(row.get("upside_score")),
        "downside_score": num(row.get("downside_score")),
        "channel": row.get("channel"),
        "grade": row.get("grade"),
        "stage": row.get("stage"),
        "ods": num(row.get("ods")),
        "frs": num(row.get("frs")),
        "lis": num(row.get("lis")),
        "pcs": num(row.get("pcs")),
        "rps": num(row.get("rps")),
        "kill_count": int(row.get("kill_count", 0) or 0),
        "action": row.get("action"),
        "confidence": num(row.get("data_confidence")),
        "reversal_type": row.get("reversal_type"),
        "quadrant": row.get("quadrant"),
        "as_of": row.get("as_of") or row.get("trade_date"),
    }


def build_funnel(frame: pd.DataFrame, settings, history: pd.DataFrame | None = None) -> dict:
    """候选生成器漏斗（2026-10 起的主口径）。

    输出四个区块：
      watchlist 观察池 Top100（召回优先）
      focus      重点 Top20（人工深研入口）
      cards      卡片 Top5
      early      早期线索榜（FRS 已确认、PCS 尚未确认；更早但更险，单独成榜）

    排序键来自 configs `funnel.rank_by`（默认 upside=抓鱼分），同分用避雷分做 tie-break；
    连续在榜期数由 history 里的历史观察池计算（需要多日运行才会累积）。
    """
    cfg = settings.path("funnel", {}) or {}
    watch_n = int(cfg.get("watchlist", 100))
    focus_n = int(cfg.get("focus", 20))
    cards_n = int(cfg.get("cards", 5))
    early_cfg = cfg.get("early_channel", {}) or {}
    early_n = int(early_cfg.get("size", 20))
    rank_by = str(cfg.get("rank_by", "upside"))
    downside_floor = float(cfg.get("downside_floor", 0) or 0)

    empty = pd.DataFrame()
    eligible = frame.loc[
        frame["tradeable"].fillna(False).astype(bool)
        & ~frame.get("risk_block", pd.Series(False, index=frame.index)).fillna(False).astype(bool)
        & (frame["grade"].astype(str) != "Reject")
        & ~frame.get("action", pd.Series("", index=frame.index)).astype(str).isin(["EXIT", "REJECT"])
    ].copy()
    if eligible.empty:
        return {"watchlist": empty, "focus": empty, "early": empty, "cards": empty}

    upside = pd.to_numeric(eligible.get("upside_score", np.nan), errors="coerce")
    downside = pd.to_numeric(eligible.get("downside_score", np.nan), errors="coerce")
    bfs = pd.to_numeric(eligible.get("bfs", np.nan), errors="coerce")
    if rank_by == "bfs" or upside.isna().all():
        key = bfs
    elif rank_by == "blend":
        key = (0.5 * upside.fillna(bfs) + 0.5 * bfs.fillna(upside))
    else:
        key = upside
    if downside_floor > 0:
        # 避雷分下限：只过滤掉最危险的尾部（实测 DS 分位可把踩雷率 14.0% → 9.1%）
        key = key.where(downside >= downside_floor, np.nan)
    eligible["_rank_key"] = key
    eligible = eligible.sort_values(["_rank_key", "downside_score"], ascending=[False, False])

    quota_cfg = cfg.get("quota", {}) or {}
    watchlist = _quota_select(eligible, watch_n, quota_cfg.get("watchlist", {}) or {})
    watchlist.insert(0, "pool_rank", range(1, len(watchlist) + 1))
    # 重点榜是观察池的子集，并施加更紧的配额（研究注意力）
    focus = _quota_select(watchlist, focus_n, quota_cfg.get("focus", {}) or {})
    cards = focus.head(cards_n).copy()

    # 早期线索：基本面已确认、价格尚未确认；剔除已在重点里的，避免两榜重复
    early_pool = eligible.loc[
        eligible["channel"].astype(str).eq("EARLY")
        & ~eligible["ts_code"].isin(set(focus["ts_code"]))
    ].sort_values("upside_score", ascending=False)   # 早期榜按抓鱼分排（PCS 在此通道内天然偏低）
    early = early_pool.head(early_n).copy()
    if not early.empty:
        early.insert(0, "early_rank", range(1, len(early) + 1))

    # 连续在榜期数（需要历史观察池；同一天重复运行不会增加）
    if history is not None and not history.empty and "ts_code" in history.columns:
        dated = history.loc[history["as_of"].astype(str) < str(frame["as_of"].iloc[0])]
        counts = dated.groupby("ts_code")["as_of"].nunique()
        for name, table in (("watchlist", watchlist), ("focus", focus), ("early", early)):
            if table.empty:
                continue
            table["streak"] = table["ts_code"].map(counts).fillna(0).astype(int) + 1
    else:
        for table in (watchlist, focus, early):
            if not table.empty:
                table["streak"] = 1
    return {"watchlist": watchlist, "focus": focus, "early": early, "cards": cards}


def _quota_select(ranked: pd.DataFrame, n: int, quota: dict) -> pd.DataFrame:
    """按排名取前 n 只，并限制单一行业/单一反转类型的数量。

    这是**研究注意力约束**（人看得过来），不是风险或收益约束；配额放宽时结果退化为纯取前 n。
    """
    max_ind = int(quota.get("max_per_industry", 0) or 0)
    max_type = int(quota.get("max_per_reversal_type", 0) or 0)
    if n <= 0 or ranked.empty:
        return ranked.head(0).copy()
    if max_ind <= 0 and max_type <= 0:
        out = ranked.head(n).copy()
        return _annotate_sequence(out)
    picked = []
    ind_count: dict[str, int] = {}
    type_count: dict[str, int] = {}
    for row in ranked.to_dict(orient="records"):
        industry = str(row.get("industry") or "UNKNOWN")
        rtype = str(row.get("reversal_type") or "UNKNOWN")
        if max_ind and ind_count.get(industry, 0) >= max_ind:
            continue
        if max_type and type_count.get(rtype, 0) >= max_type:
            continue
        picked.append(row)
        ind_count[industry] = ind_count.get(industry, 0) + 1
        type_count[rtype] = type_count.get(rtype, 0) + 1
        if len(picked) >= n:
            break
    out = pd.DataFrame(picked)
    return _annotate_sequence(out.reset_index(drop=True)) if not out.empty else ranked.head(0).copy()


def _annotate_sequence(frame: pd.DataFrame) -> pd.DataFrame:
    """标注「同行业第 N 只 / 同反转类型第 N 只」，把注意力配额交给使用者判断。"""
    if frame.empty:
        return frame
    out = frame.copy()
    for col, name in (("industry", "ind_seq"), ("reversal_type", "type_seq")):
        if name in out.columns:
            continue
        if col in out.columns:
            out[name] = out.groupby(col).cumcount() + 1
    return out


def select_top(frame: pd.DataFrame, settings, top_n: int | None = None,
               max_per_industry: int | None = None, exclude_reject: bool = True) -> pd.DataFrame:
    """排序 + 行业分散约束（规格书第 56/57 节）。"""
    if frame.empty:
        return frame
    pool = settings.path("action.pool", {}) or {}
    top_n = top_n or int(pool.get("top_n_ranking", 20))
    max_per_industry = max_per_industry or int(pool.get("max_per_industry", 4))
    data = frame.copy()
    if exclude_reject:
        keep = data["grade"].astype(str) != "Reject"
        if "risk_block" in data.columns:
            keep = keep & ~data["risk_block"].fillna(False).astype(bool)
        if "tradeable" in data.columns:
            keep = keep & data["tradeable"].fillna(False).astype(bool)
        # 规格书第 49/50 节要求识别价值陷阱与假反转；但回测（docs/07 第 4 节）显示
        # 这两类在 FRS 前 20% 内反而优于其余样本，硬剔除会损失收益。
        # 因此改为"封顶 B 级 + 披露"（见 assemble 的硬约束降级），不再从候选池剔除。
        data = data.loc[keep]
    data = data.sort_values("bfs", ascending=False)
    picked, counts = [], {}
    for _, row in data.iterrows():
        industry = row.get("industry") or "UNKNOWN"
        if counts.get(industry, 0) >= max_per_industry:
            continue
        counts[industry] = counts.get(industry, 0) + 1
        picked.append(row)
        if len(picked) >= top_n:
            break
    out = pd.DataFrame(picked)
    if out.empty:
        return out
    out.insert(0, "rank", range(1, len(out) + 1))
    return out
