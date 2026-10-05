"""Step 1 / 2：数据抓取（规格书第 55 节 Step 1）。

全部数据带缓存，可断点续跑；抓取结束后写出 data/processed/fetch_manifest.json，
记录每个数据集的来源、时间戳、行数与缺失项（规格书第 67 节）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

import pandas as pd

from .. import PROCESSED_DIR
from ..adapters import TushareAdapter
from ..config import Settings, load_settings, resolve_end_date
from ..periods import quarter_list
from ..storage import ParquetStore

log = logging.getLogger(__name__)


def _log(message: str) -> None:
    print(f"[fetch] {message}", flush=True)


def run_fetch(settings: Settings | None = None, adapter: TushareAdapter | None = None,
              skip_bulk: bool = False) -> dict:
    settings = settings or load_settings()
    adapter = adapter or TushareAdapter(store=ParquetStore(), workers=int(settings.path("data.workers", 4)))

    start = str(settings.path("data.start_date"))
    end = resolve_end_date(settings)

    _log("trade calendar")
    trade_dates = adapter.trade_dates(start, end)
    _log(f"交易日 {len(trade_dates)} 天：{trade_dates[0]} ~ {trade_dates[-1]}")

    _log("stock_basic（含退市，规避幸存者偏差）")
    basics = []
    for status in ("L", "D", "P"):
        try:
            basics.append(adapter.stock_basic(status))
        except Exception as exc:  # noqa: BLE001
            log.warning("stock_basic %s 失败: %s", status, exc)
    universe = pd.concat(basics, ignore_index=True) if basics else pd.DataFrame()
    if not universe.empty:
        universe = universe.drop_duplicates(subset=["ts_code"]).reset_index(drop=True)

    if not skip_bulk:
        _log("个股日线 + 每日指标（并行，可断点续跑）")
        moneyflow_days = trade_dates[-int(settings.path("data.moneyflow_days", 120)):]
        adapter.fetch_daily_bulk(trade_dates, with_moneyflow=moneyflow_days, progress=_log)

    _log("指数日线")
    indices = settings.path("data.indices", {}) or {}
    adapter.index_daily(list(indices.keys()), start, end)

    _log("申万一级行业（指数 + 成分）")
    adapter.industry_list()
    adapter.industry_daily(start, end)
    adapter.industry_membership()

    periods = quarter_list(str(settings.path("data.fina_start_period")), str(settings.path("data.fina_end_period")))
    _log(f"财务报表 VIP 接口，共 {len(periods)} 个报告期")
    for endpoint in ("income_vip", "balancesheet_vip", "cashflow_vip", "fina_indicator_vip"):
        for period in periods:
            try:
                adapter.fina_statement(endpoint, period)
            except Exception as exc:  # noqa: BLE001
                adapter.missing.append({"endpoint": endpoint, "period": period, "reason": str(exc)[:200]})
        _log(f"{endpoint} 完成")

    _log("业绩预告 / 业绩快报")
    for period in periods:
        for fn in (adapter.forecast, adapter.express):
            try:
                fn(period)
            except Exception as exc:  # noqa: BLE001
                adapter.missing.append({"endpoint": fn.__name__, "period": period, "reason": str(exc)[:200]})

    _log("股东增减持 / 回购（催化剂与风险）")
    try:
        adapter.holder_trades(start, end)
    except Exception as exc:  # noqa: BLE001
        adapter.missing.append({"endpoint": "stk_holdertrade", "reason": str(exc)[:200]})
    try:
        adapter.repurchase(start, end)
    except Exception as exc:  # noqa: BLE001
        adapter.missing.append({"endpoint": "repurchase", "reason": str(exc)[:200]})

    inventory = adapter.store.inventory()
    manifest = {
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_source": "tushare",
        "start_date": start,
        "end_date": end,
        "trade_days": len(trade_dates),
        "latest_trade_date": trade_dates[-1] if trade_dates else None,
        "periods": periods,
        "datasets": json.loads(inventory.to_json(orient="records", force_ascii=False)),
        "missing": adapter.missing,
        "note": "所有财务数据保留 ann_date（公告日），回测与当日评分一律按公告日可见（point-in-time）",
    }
    out = PROCESSED_DIR / "fetch_manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"manifest 写入 {out}（缺失项 {len(adapter.missing)} 条）")
    return manifest
