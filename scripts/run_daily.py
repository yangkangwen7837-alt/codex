"""每日全链路跑批（规格书第 55 节十步）。

用法：
    python scripts/run_daily.py                 # 按 configs/default.yaml 的 end_date
    python scripts/run_daily.py --as-of 20260930
    python scripts/run_daily.py --top 20
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bigfish.config import load_settings  # noqa: E402
from bigfish.pipeline.run import run_all  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--top", type=int, default=None)
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = load_settings(args.config)
    result = run_all(settings, as_of=args.as_of, top_n=args.top)
    summary = result["summary"]
    print("\n=== 结果摘要 ===")
    print(f"as_of={summary['as_of']} regime={summary['regime']} 入池={summary['ranking_rows']}")
    print(f"等级分布: {summary['grades']}")
    print(f"生命周期: {summary['stages']}")
    print(f"Kill 预警（候选池内）: {summary.get('kill_alerts_in_pool')}；"
          f"全市场 Kill 预警: {summary.get('kill_alerts_market_wide')}")
    ranking = result["ranking"]
    if not ranking.empty:
        cols = ["rank", "ts_code", "name", "industry", "bfs", "grade", "stage", "action"]
        print(ranking[[c for c in cols if c in ranking.columns]].head(20).to_string(index=False))


if __name__ == "__main__":
    main()
