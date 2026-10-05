"""Phase 6 · 运行历史回测并生成报告。

用法：
    python scripts/run_backtest.py                       # 全窗口月度调仓
    python scripts/run_backtest.py --start 20200101
    python scripts/run_backtest.py --limit 6             # 只跑前 6 个调仓日（自检用）
    python scripts/run_backtest.py --freq Q
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bigfish import OUTPUT_DIR, PROCESSED_DIR, PROJECT_ROOT  # noqa: E402
from bigfish.backtest.analysis import (ROUND_TRIP_COST, false_positive_library,  # noqa: E402
                                       group_stats, matrix_stats, portfolio_stats)
from bigfish.backtest.engine import build_panel, save_panel  # noqa: E402
from bigfish.backtest.report import render_report  # noqa: E402
from bigfish.config import load_settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--freq", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--name", default="backtest_panel")
    parser.add_argument("--from-panel", action="store_true",
                        help="跳过信号重算，直接用 data/processed/<name>.parquet 重新生成报告")
    args = parser.parse_args()

    settings = load_settings()
    start = args.start or str(settings.path("backtest.start", "20190101"))
    end = args.end or str(settings.path("backtest.end", settings.path("data.end_date")))
    freq = args.freq or str(settings.path("backtest.freq", "M"))
    horizons = tuple(settings.path("backtest.horizons", [20, 60, 120, 250]))

    if args.from_panel:
        import json

        panel = pd.read_parquet(PROCESSED_DIR / f"{args.name}.parquet")
        stats_path = PROCESSED_DIR / f"{args.name}_stats.json"
        stats = json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.exists() else {}
        stats["rows_clean"] = len(panel)
        print(f"[bt] 复用已有面板 {len(panel):,} 行", flush=True)
    else:
        panel, stats = build_panel(
            settings, start=start, end=end, freq=freq, horizons=horizons,
            limit=args.limit, progress=lambda msg: print(f"[bt] {msg}", flush=True),
        )
    if panel.empty:
        print("没有生成任何样本，请检查数据是否回补完成")
        return

    if not args.from_panel:
        stats["dropped_reasons"] = panel.attrs.get("dropped_reasons", {})
        stats["dropped_reasons"] = stats["dropped_reasons"] if "dropped_reasons" in stats else {}
        panel.attrs["dropped_reasons"] = stats["dropped_reasons"]
        save_panel(panel, stats, args.name)

    split = str(settings.path("backtest.in_sample_end", "20231231"))
    markdown = render_report(panel, stats, settings, split_date=split)
    out_md = OUTPUT_DIR / f"{args.name}_report.md"
    out_md.write_text(markdown, encoding="utf-8")
    docs_md = PROJECT_ROOT / "docs" / "06_backtest_report.md"
    docs_md.write_text(markdown, encoding="utf-8")

    # 控制台摘要
    print("\n=== FRS 分组（全样本，平均净收益）===")
    summary = group_stats(panel, "frs", [20, 60])
    print(summary.round(4).to_string(index=False))
    print("\n=== BFS 五分位（60 日，等权组合）===")
    for q, label in [(0.8, "Q5"), (0.4, "Q3")]:
        res = portfolio_stats(panel, "bfs", "ret60", q)
        print(f"  {label}: " + ", ".join(f"{k}={v:.4f}" for k, v in res.items() if not k.startswith('_')))
    print(f"\n报告：{out_md}\n      {docs_md}")


if __name__ == "__main__":
    main()
