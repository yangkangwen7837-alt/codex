"""用**真实每日观察池**跟踪闪烁率（而不是回测的月度口径）。

数据来源：data/processed/bigfish_history.parquet（每次跑批写入当日 Top100 观察池）。
样本太少时不能下结论，脚本会明确提示需要多少个交易日。

用法：
    python scripts/watchlist_flicker.py            # 查看当前水平
    python scripts/watchlist_flicker.py --write    # 同时写入 docs/12_flicker_tracking.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bigfish import PROCESSED_DIR, PROJECT_ROOT  # noqa: E402

MIN_DAYS = 20          # 少于 20 个交易日不下结论
BASELINE_FLICKER = 0.84   # 回测月度口径的基准（见 docs/11）


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    path = PROCESSED_DIR / "bigfish_history.parquet"
    if not path.exists():
        print("还没有观察池历史（data/processed/bigfish_history.parquet），先跑一次 run_daily.py")
        return
    history = pd.read_parquet(path)
    dates = sorted(history["as_of"].astype(str).unique())
    sets = {d: set(g["ts_code"]) for d, g in history.groupby("as_of")}
    n_days = len(dates)

    # 逐日重叠率
    overlaps = []
    for prev, cur in zip(dates, dates[1:]):
        a, b = sets[prev], sets[cur]
        if b:
            overlaps.append(len(a & b) / len(b))

    # 单次停留时长（run length）
    codes = {c for s in sets.values() for c in s}
    pos = {d: i for i, d in enumerate(dates)}
    runs: list[int] = []
    for code in codes:
        present = [d for d in dates if code in sets[d]]
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
    runs_arr = np.array(runs) if runs else np.array([0])
    flicker = float((runs_arr <= 2).mean())
    avg_run = float(runs_arr.mean())
    overlap = float(np.mean(overlaps)) if overlaps else float("nan")

    print(f"观察池历史：{n_days} 个交易日（{dates[0]} ~ {dates[-1]}）")
    print(f"每日平均重叠率：{overlap * 100:.0f}%" if np.isfinite(overlap) else "每日平均重叠率：样本不足")
    print(f"单次停留 ≤2 期就消失的比例：{flicker * 100:.0f}%（回测月度基准 {BASELINE_FLICKER * 100:.0f}%）")
    print(f"平均停留期数：{avg_run:.1f}")
    print()
    if n_days < MIN_DAYS:
        print(f"[样本不足] 只有 {n_days} 个交易日，尚不足以判断闪烁率是否下降；"
              f"至少需要 {MIN_DAYS} 个交易日。请继续每日跑批。")
    else:
        delta = flicker - BASELINE_FLICKER
        verdict = "下降" if delta < -0.05 else ("上升" if delta > 0.05 else "与基准相当")
        print(f"[可以判断] 已有 {n_days} 个交易日：闪烁率相对回测基准为 {verdict}"
              f"（{flicker * 100:.0f}% vs {BASELINE_FLICKER * 100:.0f}%）")

    if args.write:
        lines = ["# 12 · 观察池闪烁率跟踪（真实每日口径）", ""]
        lines.append(f"数据源：`bigfish_history.parquet`，共 {n_days} 个交易日"
                     f"（{dates[0]} ~ {dates[-1]}）。")
        lines.append("")
        lines.append("| 指标 | 当前 | 回测月度基准（docs/11） |")
        lines.append("|---|---|---|")
        lines.append(f"| 相邻两日重叠率 | {overlap * 100:.0f}% | 40% |" if np.isfinite(overlap)
                     else "| 相邻两日重叠率 | 样本不足 | 40% |")
        lines.append(f"| 单次停留 ≤2 期占比 | {flicker * 100:.0f}% | {BASELINE_FLICKER * 100:.0f}% |")
        lines.append(f"| 平均停留期数 | {avg_run:.1f} | 1.6 |")
        lines.append("")
        if n_days < MIN_DAYS:
            lines.append(f"> 样本 {n_days} 个交易日 < {MIN_DAYS}，**尚不能下结论**。")
        else:
            lines.append(f"> 样本 {n_days} 个交易日 ≥ {MIN_DAYS}，"
                         f"闪烁率相对基准{'下降' if flicker < BASELINE_FLICKER - 0.05 else '未明显下降'}。")
        (PROJECT_ROOT / "docs" / "12_flicker_tracking.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
        (PROCESSED_DIR / "flicker_status.json").write_text(json.dumps({
            "days": n_days, "last_date": dates[-1], "overlap": overlap,
            "flicker": flicker, "avg_run": avg_run,
            "conclusive": n_days >= MIN_DAYS,
            "baseline": BASELINE_FLICKER,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print("已写入 docs/12_flicker_tracking.md 与 data/processed/flicker_status.json")


if __name__ == "__main__":
    main()
