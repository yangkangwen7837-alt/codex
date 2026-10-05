"""抓取全部原始数据（可断点续跑）。

用法：
    python scripts/fetch_data.py              # 全量抓取
    python scripts/fetch_data.py --skip-bulk  # 跳过个股日线批量（调试用）
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bigfish.adapters import TushareAdapter  # noqa: E402
from bigfish.config import load_settings  # noqa: E402
from bigfish.pipeline.fetch import run_fetch  # noqa: E402
from bigfish.storage import ParquetStore  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-bulk", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = load_settings()
    adapter = TushareAdapter(store=ParquetStore(), workers=args.workers)
    manifest = run_fetch(settings, adapter, skip_bulk=args.skip_bulk)
    print(f"完成：{manifest['trade_days']} 个交易日，最新 {manifest['latest_trade_date']}，"
          f"缺失项 {len(manifest['missing'])} 条")


if __name__ == "__main__":
    main()
