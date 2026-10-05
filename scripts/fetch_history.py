"""历史数据回补（Phase 6 回测用）。

把行情回补到 2018-01-02、财务回补到 2017Q1，使 2019-01 起每个调仓日都有
250 个交易日的价格窗口与 6 个季度以上的财务历史。

用法：
    python scripts/fetch_history.py                 # 全量回补（可断点续跑）
    python scripts/fetch_history.py --skip-prices   # 只补财务
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bigfish.adapters import TushareAdapter  # noqa: E402
from bigfish.config import load_settings  # noqa: E402
from bigfish.periods import quarter_list  # noqa: E402
from bigfish.storage import ParquetStore  # noqa: E402

HISTORY_START = "20180102"
FINA_START_PERIOD = "20170331"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-prices", action="store_true")
    parser.add_argument("--skip-financials", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = load_settings()
    end = str(settings.path("data.end_date"))
    adapter = TushareAdapter(store=ParquetStore(), workers=args.workers)

    print(f"[history] 交易日历 {HISTORY_START} ~ {end}", flush=True)
    trade_dates = adapter.trade_dates(HISTORY_START, end)
    print(f"[history] 共 {len(trade_dates)} 个交易日", flush=True)

    if not args.skip_prices:
        print("[history] 个股日线 + 每日指标 + 资金流（并行、断点续跑）", flush=True)
        adapter.fetch_daily_bulk(
            trade_dates,
            with_moneyflow=trade_dates,
            progress=lambda msg: print(f"[history] {msg}", flush=True),
        )
        print("[history] 行情与资金流完成", flush=True)

    print("[history] 指数日线（2017 起，覆盖 250 日预热）", flush=True)
    indices = list((settings.path("data.indices", {}) or {}).keys())
    adapter.index_daily(indices, "20170101", end)

    print("[history] 申万一级行业指数 + 成分", flush=True)
    adapter.industry_list()
    adapter.industry_daily("20170101", end)
    adapter.industry_membership()

    if not args.skip_financials:
        periods = quarter_list(FINA_START_PERIOD, str(settings.path("data.fina_end_period")))
        print(f"[history] 财务报表 {len(periods)} 个报告期（{periods[0]} ~ {periods[-1]}）", flush=True)
        for endpoint in ("income_vip", "balancesheet_vip", "cashflow_vip", "fina_indicator_vip"):
            for period in periods:
                try:
                    adapter.fina_statement(endpoint, period)
                except Exception as exc:  # noqa: BLE001
                    adapter.missing.append({"endpoint": endpoint, "period": period, "reason": str(exc)[:200]})
            print(f"[history] {endpoint} 完成", flush=True)
        print("[history] 业绩预告 / 快报", flush=True)
        for period in periods:
            for fn in (adapter.forecast, adapter.express):
                try:
                    fn(period)
                except Exception as exc:  # noqa: BLE001
                    adapter.missing.append({"endpoint": fn.__name__, "period": period, "reason": str(exc)[:200]})

    print("[history] 股东增减持 / 回购", flush=True)
    for fn, label in ((adapter.holder_trades, "股东增减持"), (adapter.repurchase, "回购")):
        try:
            fn(HISTORY_START, end)
        except Exception as exc:  # noqa: BLE001
            adapter.missing.append({"endpoint": label, "reason": str(exc)[:200]})

    inventory = adapter.store.inventory()
    print(f"[history] 完成：数据集 {len(inventory)} 个分片，缺失 {len(adapter.missing)} 条", flush=True)
    for item in adapter.missing[:10]:
        print(f"   缺失：{item}", flush=True)


if __name__ == "__main__":
    main()
