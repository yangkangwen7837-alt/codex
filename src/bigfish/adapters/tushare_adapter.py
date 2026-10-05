"""Tushare 数据适配器：带 Parquet 缓存、限流重试、断点续跑。

设计约束（规格书第 66/67 节）：
* 所有财务数据保留 ``ann_date``（公告日），供 point-in-time 使用；
* 任何接口不可用都显式返回空表并记录到 missing 清单，不做静默填充。
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from ..storage import ParquetStore

log = logging.getLogger(__name__)


class TushareError(RuntimeError):
    """Tushare 调用失败（含权限不足）。"""


class TushareAdapter:
    """行情 / 财务 / 行业 三类数据的统一入口。"""

    def __init__(self, store: ParquetStore | None = None, workers: int = 4) -> None:
        self.store = store or ParquetStore()
        self.workers = workers
        self._api = None
        self.missing: list[dict] = []

    @property
    def api(self):
        if self._api is None:
            # 敏感配置统一入口：st.secrets → 环境变量（见 bigfish.config.get_secret）
            from ..config import get_secret

            token = get_secret("TUSHARE_TOKEN") or os.environ.get("TS_TOKEN")
            if not token:
                raise TushareError(
                    "缺少 TUSHARE_TOKEN：本地请设置环境变量；"
                    "Streamlit Cloud 请在 App settings → Secrets 里配置"
                    "（模板见 .streamlit/secrets.toml.example）"
                )
            import tushare as ts

            self._api = ts.pro_api(token)
        return self._api

    # ------------------------------------------------------------------ 基础
    def call(self, endpoint: str, *, retries: int = 3, optional: bool = False, **kwargs) -> pd.DataFrame:
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                df = getattr(self.api, endpoint)(**kwargs)
                return df if df is not None else pd.DataFrame()
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                message = str(exc)
                rate_limited = any(
                    token in message for token in ("频率超限", "每分钟", "每小时", "次/分", "次/小时", "最多访问")
                )
                permission_issue = not rate_limited and (
                    "没有接口" in message or "无权限" in message or "权限不足" in message or "没有权限" in message
                )
                if permission_issue:
                    if optional:
                        log.warning("接口无权限，显式跳过: %s %s", endpoint, kwargs)
                        self.missing.append({"endpoint": endpoint, "kwargs": str(kwargs), "reason": "NO_PERMISSION"})
                        return pd.DataFrame()
                    raise TushareError(f"{endpoint} 无权限: {message}") from exc
                if rate_limited:
                    time.sleep(15 * (attempt + 1))
                else:
                    time.sleep(1.5 * (attempt + 1))
        raise TushareError(f"{endpoint} 调用失败: {last_err}")

    # ------------------------------------------------------------ 交易日历
    def trade_dates(self, start_date: str, end_date: str) -> list[str]:
        cache_key = ("meta", f"trade_cal_{start_date}_{end_date}")
        if self.store.exists(*cache_key):
            df = self.store.read(*cache_key)
        else:
            df = self.call("trade_cal", exchange="SSE", start_date=start_date, end_date=end_date, is_open="1")
            df = df[["cal_date", "pretrade_date"]].rename(columns={"cal_date": "trade_date"})
            df["trade_date"] = df["trade_date"].astype(str)
            df = df.sort_values("trade_date").reset_index(drop=True)
            self.store.write(df, *cache_key, endpoint="trade_cal", start_date=start_date, end_date=end_date)
        return df["trade_date"].tolist()

    def calendar_frame(self, start_date: str, end_date: str) -> pd.DataFrame:
        return self.store.read("meta", f"trade_cal_{start_date}_{end_date}")

    # ---------------------------------------------------------------- 股票池
    def stock_basic(self, list_status: str = "L") -> pd.DataFrame:
        cache_key = ("meta", f"stock_basic_{list_status}")
        if self.store.exists(*cache_key):
            return self.store.read(*cache_key)
        df = self.call("stock_basic", exchange="", list_status=list_status)
        keep = ["ts_code", "symbol", "name", "area", "industry", "market", "list_date", "delist_date"]
        df = df[[c for c in keep if c in df.columns]].copy()
        df["list_status"] = list_status
        self.store.write(df, *cache_key, endpoint="stock_basic", list_status=list_status)
        return df

    # ---------------------------------------------------------------- 行情
    def daily(self, trade_date: str) -> pd.DataFrame:
        cache_key = ("daily", str(trade_date))
        if self.store.exists(*cache_key):
            return self.store.read(*cache_key)
        df = self.call("daily", trade_date=trade_date)
        if df is None or df.empty:
            self.missing.append({"endpoint": "daily", "trade_date": trade_date, "reason": "EMPTY"})
            return pd.DataFrame()
        df = df.sort_values("ts_code").reset_index(drop=True)
        self.store.write(df, *cache_key, endpoint="daily", trade_date=trade_date)
        return df

    def daily_basic(self, trade_date: str) -> pd.DataFrame:
        cache_key = ("daily_basic", str(trade_date))
        if self.store.exists(*cache_key):
            return self.store.read(*cache_key)
        df = self.call("daily_basic", trade_date=trade_date, optional=True)
        if df is None or df.empty:
            self.missing.append({"endpoint": "daily_basic", "trade_date": trade_date, "reason": "EMPTY_OR_NO_PERMISSION"})
            return pd.DataFrame()
        keep = ["ts_code", "trade_date", "close", "turnover_rate", "turnover_rate_f", "volume_ratio",
                "pe", "pe_ttm", "pb", "ps_ttm", "dv_ttm", "total_share", "float_share", "free_share",
                "total_mv", "circ_mv"]
        df = df[[c for c in keep if c in df.columns]].copy()
        self.store.write(df, *cache_key, endpoint="daily_basic", trade_date=trade_date)
        return df

    def index_daily(self, codes: list[str], start_date: str, end_date: str) -> pd.DataFrame:
        frames = []
        for code in codes:
            cache_key = ("index_daily", f"{code}_{start_date}_{end_date}")
            if self.store.exists(*cache_key):
                frames.append(self.store.read(*cache_key))
                continue
            df = self.call("index_daily", ts_code=code, start_date=start_date, end_date=end_date, optional=True)
            if df is None or df.empty:
                log.warning("指数 %s 无数据", code)
                continue
            df = df.sort_values("trade_date").reset_index(drop=True)
            self.store.write(df, *cache_key, endpoint="index_daily", ts_code=code)
            frames.append(df)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def moneyflow(self, trade_date: str) -> pd.DataFrame:
        cache_key = ("moneyflow", str(trade_date))
        if self.store.exists(*cache_key):
            return self.store.read(*cache_key)
        df = self.call("moneyflow", trade_date=trade_date, optional=True)
        if df is None or df.empty:
            self.missing.append({"endpoint": "moneyflow", "trade_date": trade_date, "reason": "EMPTY"})
            return pd.DataFrame()
        keep = ["ts_code", "trade_date", "buy_lg_amount", "sell_lg_amount", "buy_elg_amount",
                "sell_elg_amount", "net_mf_amount"]
        df = df[[c for c in keep if c in df.columns]].copy()
        self.store.write(df, *cache_key, endpoint="moneyflow", trade_date=trade_date)
        return df

    # ---------------------------------------------------------------- 行业
    def industry_list(self) -> pd.DataFrame:
        cache_key = ("meta", "sw_l1_list")
        if self.store.exists(*cache_key):
            return self.store.read(*cache_key)
        df = self.call("index_classify", level="L1", src="SW2021")
        df = df[["index_code", "industry_name"]].rename(columns={"index_code": "industry_code"})
        df = df.sort_values("industry_code").reset_index(drop=True)
        self.store.write(df, *cache_key, endpoint="index_classify", src="SW2021")
        return df

    def industry_daily(self, start_date: str, end_date: str) -> pd.DataFrame:
        industries = self.industry_list()
        frames = []
        for code in industries["industry_code"]:
            cache_key = ("sw_daily", f"{code}_{start_date}_{end_date}")
            if self.store.exists(*cache_key):
                frames.append(self.store.read(*cache_key))
                continue
            df = self.call("sw_daily", ts_code=code, start_date=start_date, end_date=end_date, optional=True)
            if df is None or df.empty:
                log.warning("申万行业 %s 无数据", code)
                continue
            df = df.rename(columns={"pct_change": "pct_chg"})
            keep = ["ts_code", "trade_date", "name", "open", "high", "low", "close",
                    "pct_chg", "vol", "amount", "pe", "pb", "float_mv", "total_mv"]
            df = df[[c for c in keep if c in df.columns]].copy()
            df = df.sort_values("trade_date").reset_index(drop=True)
            self.store.write(df, *cache_key, endpoint="sw_daily", ts_code=code)
            frames.append(df)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def industry_membership(self) -> pd.DataFrame:
        cache_key = ("meta", "sw_membership")
        if self.store.exists(*cache_key):
            return self.store.read(*cache_key)
        df = self.call("index_member_all")
        keep = ["l1_code", "l1_name", "l2_code", "l2_name", "l3_code", "l3_name",
                "ts_code", "name", "in_date", "out_date", "is_new"]
        df = df[[c for c in keep if c in df.columns]]
        df = df.rename(columns={"l1_code": "industry_code", "l1_name": "industry_name"})
        df = df.sort_values(["ts_code", "in_date"]).drop_duplicates(subset=["ts_code"], keep="last")
        df = df.reset_index(drop=True)
        self.store.write(df, *cache_key, endpoint="index_member_all")
        return df

    # ------------------------------------------------------------ 财务报表
    _PAGE = 5000

    def fina_statement(self, endpoint: str, period: str) -> pd.DataFrame:
        """VIP 报表接口（income_vip / balancesheet_vip / cashflow_vip / fina_indicator_vip）。

        Tushare 单次返回有行数上限，这里用 offset 分页取全。
        """
        cache_key = (endpoint, str(period))
        if self.store.exists(*cache_key):
            return self.store.read(*cache_key)
        frames = []
        offset = 0
        while True:
            df = self.call(endpoint, period=period, limit=self._PAGE, offset=offset, optional=True)
            if df is None or df.empty:
                break
            frames.append(df)
            if len(df) < self._PAGE:
                break
            offset += len(df)
            if offset > 60000:
                log.warning("%s@%s 分页超过上限，提前结束", endpoint, period)
                break
        out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if out.empty:
            self.missing.append({"endpoint": endpoint, "period": period, "reason": "EMPTY"})
            return out
        if "report_type" in out.columns:
            out = out.loc[out["report_type"].astype(str) == "1"]
        out = out.drop_duplicates(subset=["ts_code", "end_date", "ann_date"], keep="last")
        out = out.sort_values(["ts_code", "end_date"]).reset_index(drop=True)
        self.store.write(out, *cache_key, endpoint=endpoint, period=period)
        return out

    def forecast(self, period: str) -> pd.DataFrame:
        cache_key = ("forecast", str(period))
        if self.store.exists(*cache_key):
            return self.store.read(*cache_key)
        df = self.call("forecast_vip", period=period, optional=True)
        if df is None or df.empty:
            self.missing.append({"endpoint": "forecast_vip", "period": period, "reason": "EMPTY"})
            return pd.DataFrame()
        df = df.sort_values(["ts_code", "ann_date"]).reset_index(drop=True)
        self.store.write(df, *cache_key, endpoint="forecast_vip", period=period)
        return df

    def express(self, period: str) -> pd.DataFrame:
        cache_key = ("express", str(period))
        if self.store.exists(*cache_key):
            return self.store.read(*cache_key)
        df = self.call("express_vip", period=period, optional=True)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.sort_values(["ts_code", "ann_date"]).reset_index(drop=True)
        self.store.write(df, *cache_key, endpoint="express_vip", period=period)
        return df

    # ---------------------------------------------------------------- 事件
    def holder_trades(self, start_date: str, end_date: str, chunk_days: int = 30) -> pd.DataFrame:
        cache_key = ("holder_trade", f"{start_date}_{end_date}")
        if self.store.exists(*cache_key):
            return self.store.read(*cache_key)
        frames: list[pd.DataFrame] = []
        cursor = pd.to_datetime(start_date, format="%Y%m%d")
        end = pd.to_datetime(end_date, format="%Y%m%d")
        while cursor <= end:
            chunk_end = min(cursor + pd.Timedelta(days=chunk_days - 1), end)
            df = self.call("stk_holdertrade", start_date=cursor.strftime("%Y%m%d"),
                           end_date=chunk_end.strftime("%Y%m%d"), optional=True)
            if df is not None and not df.empty:
                frames.append(df)
            cursor = chunk_end + pd.Timedelta(days=1)
        out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if out.empty:
            return out
        keep = ["ts_code", "ann_date", "holder_name", "holder_type", "in_de", "change_vol",
                "change_ratio", "avg_price"]
        out = out[[c for c in keep if c in out.columns]]
        out = out.drop_duplicates(subset=["ts_code", "ann_date", "holder_name", "in_de"]).reset_index(drop=True)
        self.store.write(out, *cache_key, endpoint="stk_holdertrade")
        return out

    def repurchase(self, start_date: str, end_date: str) -> pd.DataFrame:
        cache_key = ("repurchase", f"{start_date}_{end_date}")
        if self.store.exists(*cache_key):
            return self.store.read(*cache_key)
        df = self.call("repurchase", start_date=start_date, end_date=end_date, optional=True)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.sort_values(["ts_code", "ann_date"]).reset_index(drop=True)
        self.store.write(df, *cache_key, endpoint="repurchase")
        return df

    # ------------------------------------------------------------ 批量抓取
    def fetch_daily_bulk(self, trade_dates: list[str], with_moneyflow: list[str] | None = None,
                         progress=None) -> None:
        """按交易日并行抓 daily / daily_basic（可选 moneyflow）。"""
        tasks = []
        for date in trade_dates:
            if not self.store.exists("daily", date):
                tasks.append(("daily", date))
            if not self.store.exists("daily_basic", date):
                tasks.append(("daily_basic", date))
        for date in with_moneyflow or []:
            if not self.store.exists("moneyflow", date):
                tasks.append(("moneyflow", date))
        if not tasks:
            log.info("daily/daily_basic 已全部缓存，跳过")
            return
        done = 0
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {
                pool.submit(getattr(self, kind), date): (kind, date)
                for kind, date in tasks
            }
            for future in as_completed(futures):
                kind, date = futures[future]
                done += 1
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001
                    self.missing.append({"endpoint": kind, "trade_date": date, "reason": str(exc)[:200]})
                if progress and done % 20 == 0:
                    progress(f"{kind} {date} ({done}/{len(tasks)})")
