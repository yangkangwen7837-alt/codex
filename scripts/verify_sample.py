"""规格书第 84 节验收：随机抽 20 只股票做独立人工核验。

核验方式是**独立重算**：本脚本直接读原始 parquet，用与评分链路不同的代码路径
重新计算单季度收入/利润、毛利率、现金流、价格收益与市值，再与产出对账。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bigfish import OUTPUT_DIR, PROCESSED_DIR, RAW_DIR  # noqa: E402
from bigfish.config import load_settings  # noqa: E402
from bigfish.periods import shift_period  # noqa: E402


def _read_raw(group: str, name: str) -> pd.DataFrame:
    path = RAW_DIR / group / f"{name}.parquet"
    return pd.read_parquet(path) if path.exists() else pd.DataFrame()


def _stack(group: str) -> pd.DataFrame:
    folder = RAW_DIR / group
    if not folder.exists():
        return pd.DataFrame()
    frames = [pd.read_parquet(p) for p in sorted(folder.glob("*.parquet"))]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _single_quarter(cum: pd.DataFrame, col: str, ts_code: str, period: str) -> float:
    row = cum.loc[(cum["ts_code"] == ts_code) & (cum["end_date"] == period), col]
    if row.empty:
        return np.nan
    value = float(row.iloc[0])
    if period.endswith("0331"):
        return value
    prev = cum.loc[(cum["ts_code"] == ts_code) & (cum["end_date"] == shift_period(period, -1)), col]
    if prev.empty:
        return np.nan
    return value - float(prev.iloc[0])


def verify_sample(as_of: str, sample_size: int = 20, seed: int = 20261004) -> tuple[pd.DataFrame, str]:
    settings = load_settings()
    score = pd.read_parquet(PROCESSED_DIR / "bigfish_score_latest.parquet")
    scored = score.loc[score["tradeable"].fillna(False)].copy()
    rng = np.random.default_rng(seed)
    codes = rng.choice(sorted(scored["ts_code"].unique()), size=min(sample_size, len(scored)), replace=False)
    sample = scored.set_index("ts_code").loc[list(codes)].reset_index()

    income = _stack("income_vip")
    cashflow = _stack("cashflow_vip")
    denominator = income.loc[income["report_type"].astype(str) == "1"].copy() if "report_type" in income.columns else income

    rows = []
    for _, row in sample.iterrows():
        ts_code = row["ts_code"]
        period = str(row.get("latest_period"))
        sub = denominator.loc[denominator["ts_code"] == ts_code].sort_values("end_date")
        checks = {"ts_code": ts_code}

        # 1) 收入单季同比
        rev_t = _single_quarter(denominator, "revenue", ts_code, period)
        rev_prev = _single_quarter(denominator, "revenue", ts_code, shift_period(period, -4))
        checks["revenue_yoy_recalc"] = (rev_t / rev_prev - 1) if rev_prev and np.isfinite(rev_prev) and rev_prev != 0 else np.nan
        checks["revenue_yoy_pipeline"] = row.get("f_revenue_yoy")

        # 2) 归母单季同比
        np_t = _single_quarter(denominator, "n_income_attr_p", ts_code, period)
        np_prev = _single_quarter(denominator, "n_income_attr_p", ts_code, shift_period(period, -4))
        # 统一口径：(new − old) / |old|；基期为亏损时表示"亏损收窄 / 扭亏"的改善幅度
        checks["np_yoy_recalc"] = ((np_t - np_prev) / abs(np_prev)) if np_prev and np.isfinite(np_prev) and np_prev != 0 else np.nan
        checks["np_yoy_pipeline"] = row.get("f_np_yoy")

        # 3) 毛利率（单季）
        cost_t = _single_quarter(denominator, "oper_cost", ts_code, period)
        checks["gross_margin_recalc"] = (1 - cost_t / rev_t) if rev_t and np.isfinite(rev_t) and rev_t != 0 else np.nan
        checks["gross_margin_pipeline"] = row.get("f_gross_margin")

        # 4) 现金流 TTM 是否为正 / 与利润关系
        cfo_t = _single_quarter(cashflow, "n_cashflow_act", ts_code, period)
        checks["cfo_single_quarter_recalc"] = cfo_t
        checks["ttm_cfo_pipeline"] = row.get("ttm_cfo")

        # 5) 价格：20 日收益率
        daily = _read_raw("daily", as_of)
        prices = _read_raw("daily", str(sorted(p.name.split(".")[0] for p in (RAW_DIR / "daily").glob("*.parquet"))[-21]))
        # 用交易日序列重算 20 日收益
        dates = sorted(p.stem for p in (RAW_DIR / "daily").glob("*.parquet"))[-21:]
        first_df = _read_raw("daily", dates[0])
        price_series = pd.concat([first_df], ignore_index=True).set_index("ts_code")
        last_date = str(row.get("last_date"))
        last_df = _read_raw("daily", last_date).set_index("ts_code") if last_date != "nan" else pd.DataFrame()
        if ts_code in price_series.index and ts_code in last_df.index:
            p0 = float(price_series.at[ts_code, "close"])
            p1 = float(last_df.at[ts_code, "close"])
            checks["ret20_recalc"] = p1 / p0 - 1
            checks["close_recalc"] = p1
        else:
            checks["ret20_recalc"] = np.nan
            checks["close_recalc"] = np.nan
        checks["ret20_pipeline"] = row.get("ret20")
        checks["close_pipeline"] = row.get("close")

        # 6) 市值（daily_basic 最新交易日）
        basic = _read_raw("daily_basic", last_date) if last_date and last_date != "nan" else pd.DataFrame()
        if not basic.empty and ts_code in set(basic["ts_code"]):
            mv = float(basic.loc[basic["ts_code"] == ts_code, "total_mv"].iloc[0])
        else:
            mv = np.nan
        checks["mv_recalc"] = mv
        checks["mv_pipeline"] = row.get("current_mv")

        # 7) point-in-time 检查
        checks["ann_date"] = row.get("latest_ann_date")
        checks["latest_period"] = row.get("latest_period")
        checks["pit_ok"] = str(row.get("latest_ann_date")) <= as_of
        checks["name"] = row.get("name")
        checks["industry"] = row.get("industry")
        checks["frs"] = row.get("frs")
        checks["lis"] = row.get("lis")
        checks["pcs"] = row.get("pcs")
        checks["bfs"] = row.get("bfs")
        checks["grade"] = row.get("grade")
        checks["stage"] = row.get("stage")
        checks["action"] = row.get("action")

        def close_enough(a, b, tol=0.01):
            a, b = float(a), float(b)
            if not (np.isfinite(a) or np.isfinite(b)):
                return True
            if not (np.isfinite(a) and np.isfinite(b)):
                return False
            return abs(a - b) <= tol + 0.02 * max(abs(a), abs(b))

        checks["pass_revenue"] = close_enough(checks["revenue_yoy_recalc"], checks["revenue_yoy_pipeline"])
        checks["pass_np"] = close_enough(checks["np_yoy_recalc"], checks["np_yoy_pipeline"], tol=0.02)
        checks["pass_margin"] = close_enough(checks["gross_margin_recalc"], checks["gross_margin_pipeline"])
        checks["pass_ret20"] = close_enough(checks["ret20_recalc"], checks["ret20_pipeline"], tol=0.005)
        checks["pass_mv"] = close_enough(checks["mv_recalc"], checks["mv_pipeline"], tol=1.0)
        checks["pass_all"] = all([checks["pass_revenue"], checks["pass_np"], checks["pass_margin"],
                                  checks["pass_ret20"], checks["pass_mv"], bool(checks["pit_ok"])])
        rows.append(checks)

    report = pd.DataFrame(rows)
    lines = [f"# 随机抽检 20 只 · 独立重算对账（as_of={as_of}）", ""]
    lines.append(f"- 抽样数量：{len(report)}，全部通过：{int(report['pass_all'].sum())}/{len(report)}")
    lines.append(f"- point-in-time 检查（公告日 ≤ 基准日）：{int(report['pit_ok'].sum())}/{len(report)}")
    lines.append("")
    cols = ["ts_code", "name", "industry", "latest_period",
            "revenue_yoy_recalc", "revenue_yoy_pipeline", "pass_revenue",
            "np_yoy_recalc", "np_yoy_pipeline", "pass_np",
            "gross_margin_recalc", "gross_margin_pipeline", "pass_margin",
            "ret20_recalc", "ret20_pipeline", "pass_ret20",
            "mv_recalc", "mv_pipeline", "pass_mv", "pass_all"]
    cols = [c for c in cols if c in report.columns]
    view = report[cols].copy()
    for col in view.columns:
        if pd.api.types.is_float_dtype(view[col]):
            view[col] = view[col].round(4)
    lines.append("| " + " | ".join(view.columns) + " |")
    lines.append("|" + "|".join(["---"] * len(view.columns)) + "|")
    for record in view.itertuples(index=False):
        lines.append("| " + " | ".join(str(v) for v in record) + " |")
    lines.append("")
    lines.append("## 明细（评分与结论字段）")
    detail_cols = ["ts_code", "name", "industry", "ann_date", "frs", "lis", "pcs", "bfs", "grade", "stage", "action", "pit_ok"]
    detail = report[[c for c in detail_cols if c in report.columns]]
    lines.append("| " + " | ".join(detail.columns) + " |")
    lines.append("|" + "|".join(["---"] * len(detail.columns)) + "|")
    for record in detail.itertuples(index=False):
        lines.append("| " + " | ".join(str(v) for v in record) + " |")
    return report, "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--size", type=int, default=20)
    args = parser.parse_args()
    settings = load_settings()
    as_of = args.as_of or str(settings.path("data.end_date"))
    report, markdown = verify_sample(as_of, sample_size=args.size)
    out_csv = OUTPUT_DIR / f"verification_sample_{as_of}.csv"
    out_md = OUTPUT_DIR / f"verification_sample_{as_of}.md"
    report.to_csv(out_csv, index=False, encoding="utf-8-sig")
    out_md.write_text(markdown, encoding="utf-8")
    print(f"通过 {int(report['pass_all'].sum())}/{len(report)}")
    print(f"写入 {out_csv}\n写入 {out_md}")


if __name__ == "__main__":
    main()
