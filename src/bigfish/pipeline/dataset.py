"""数据集加载：把 data/raw 下的原始表装配成统一对象（含 point-in-time 截面）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import Settings, load_settings, resolve_end_date
from ..storage import ParquetStore, latest_local_trade_date


def _board(ts_code: str, market: str | None = None) -> str:
    code = ts_code.split(".")[0]
    if code.startswith("688"):
        return "科创板"
    if code.startswith(("300", "301")):
        return "创业板"
    if code.startswith(("43", "83", "87", "88", "92")) or (market or "") == "北交所":
        return "北交所"
    return "主板"


@dataclass
class BigFishDataset:
    as_of: str
    settings: Settings
    trade_dates: list[str] = field(default_factory=list)
    universe: pd.DataFrame = field(default_factory=pd.DataFrame)
    prices: pd.DataFrame = field(default_factory=pd.DataFrame)
    basic: pd.DataFrame = field(default_factory=pd.DataFrame)
    indices: pd.DataFrame = field(default_factory=pd.DataFrame)
    industry_daily: pd.DataFrame = field(default_factory=pd.DataFrame)
    industry_list: pd.DataFrame = field(default_factory=pd.DataFrame)
    membership: pd.DataFrame = field(default_factory=pd.DataFrame)
    fundamentals: pd.DataFrame = field(default_factory=pd.DataFrame)
    forecast: pd.DataFrame = field(default_factory=pd.DataFrame)
    express: pd.DataFrame = field(default_factory=pd.DataFrame)
    moneyflow: pd.DataFrame = field(default_factory=pd.DataFrame)
    holder_trades: pd.DataFrame = field(default_factory=pd.DataFrame)
    repurchase: pd.DataFrame = field(default_factory=pd.DataFrame)
    missing: list[dict] = field(default_factory=list)
    #: 供各评分模块复用的缓存（同一份价格矩阵不被反复透视）
    cache: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    def industry_of(self, ts_code: str) -> tuple[str, str]:
        if self.membership.empty:
            return "", ""
        row = self.membership.loc[self.membership["ts_code"] == ts_code]
        if row.empty:
            return "", ""
        return str(row.iloc[0].get("industry_code", "")), str(row.iloc[0].get("industry_name", ""))

    def latest_price_date(self) -> str:
        return str(self.prices["trade_date"].max()) if not self.prices.empty else self.as_of


def load_dataset(settings: Settings | None = None, as_of: str | None = None,
                 store: ParquetStore | None = None, price_start: str | None = None) -> BigFishDataset:
    settings = settings or load_settings()
    store = store or ParquetStore()
    as_of = as_of or latest_local_trade_date(resolve_end_date(settings)) or resolve_end_date(settings)
    start = price_start or str(settings.path("data.start_date"))

    def read_or_empty(group: str, name: str) -> pd.DataFrame:
        return store.read(group, name) if store.exists(group, name) else pd.DataFrame()

    # 交易日序列直接用本地已落盘的分片名（不依赖日历缓存键，换区间也不会失效）
    price_dir = store.root / "daily"
    trade_dates = sorted(p.stem for p in price_dir.glob("*.parquet")
                         if len(p.stem) == 8 and start <= p.stem <= as_of)

    # ---- 股票池（含退市，规避幸存者偏差）----
    basics = [read_or_empty("meta", f"stock_basic_{s}") for s in ("L", "D", "P")]
    basics = [b for b in basics if not b.empty]
    universe = pd.concat(basics, ignore_index=True).drop_duplicates(subset=["ts_code"]) if basics else pd.DataFrame()
    if not universe.empty:
        universe["board"] = [ _board(c, m) for c, m in zip(universe["ts_code"], universe.get("market", pd.Series(dtype=object)))]

    # ---- 行情（逐日分片拼装，限制在 as_of 之前）----
    price_frames, basic_frames = [], []
    for date in trade_dates:
        if date > as_of:
            continue
        if store.exists("daily", date):
            price_frames.append(store.read("daily", date))
        if store.exists("daily_basic", date):
            basic_frames.append(store.read("daily_basic", date))
    prices = pd.concat(price_frames, ignore_index=True) if price_frames else pd.DataFrame()
    if not prices.empty:
        prices["trade_date"] = prices["trade_date"].astype(str)
        prices = prices.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    basic = pd.concat(basic_frames, ignore_index=True) if basic_frames else pd.DataFrame()
    if not basic.empty:
        basic["trade_date"] = basic["trade_date"].astype(str)

    # ---- 指数 / 行业 ----
    indices = read_or_empty("index_daily", "")  # placeholder replaced below
    indices_frames = []
    for code in (settings.path("data.indices", {}) or {}):
        name = f"{code}_{start}_{as_of}"
        if store.exists("index_daily", name):
            indices_frames.append(store.read("index_daily", name))
    indices = pd.concat(indices_frames, ignore_index=True) if indices_frames else pd.DataFrame()

    industry_list = read_or_empty("meta", "sw_l1_list")
    membership = read_or_empty("meta", "sw_membership")
    ind_frames = []
    if not industry_list.empty:
        for code in industry_list["industry_code"]:
            name = f"{code}_{start}_{as_of}"
            if store.exists("sw_daily", name):
                ind_frames.append(store.read("sw_daily", name))
    industry_daily = pd.concat(ind_frames, ignore_index=True) if ind_frames else pd.DataFrame()

    # ---- 财务面板（point-in-time：只看 ann_date <= as_of 的公告）----
    def stack(group: str) -> pd.DataFrame:
        folder = store.root / group
        if not folder.exists():
            return pd.DataFrame()
        frames = []
        for path in sorted(folder.glob("*.parquet")):
            frames.append(pd.read_parquet(path))
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    income = stack("income_vip")
    balance = stack("balancesheet_vip")
    cashflow = stack("cashflow_vip")
    indicator = stack("fina_indicator_vip")
    fundamentals = pd.DataFrame()
    if not income.empty:
        from .fundamentals import build_fundamental_panel

        fundamentals = build_fundamental_panel(income, balance, cashflow, indicator)
        if not fundamentals.empty:
            fundamentals = fundamentals.loc[fundamentals["ann_date"].astype(str) <= as_of].reset_index(drop=True)

    forecast = stack("forecast")
    if not forecast.empty:
        forecast = forecast.loc[forecast["ann_date"].astype(str) <= as_of].reset_index(drop=True)
    express = stack("express")
    if not express.empty:
        express = express.loc[express["ann_date"].astype(str) <= as_of].reset_index(drop=True)

    mf_frames = []
    for date in trade_dates[-int(settings.path("data.moneyflow_days", 120)):]:
        if date <= as_of and store.exists("moneyflow", date):
            mf_frames.append(store.read("moneyflow", date))
    moneyflow = pd.concat(mf_frames, ignore_index=True) if mf_frames else pd.DataFrame()

    holder = read_or_empty("holder_trade", f"{start}_{as_of}")
    repurchase = read_or_empty("repurchase", f"{start}_{as_of}")

    missing: list[dict] = []
    if prices.empty:
        missing.append({"dataset": "prices", "reason": "EMPTY"})
    if fundamentals.empty:
        missing.append({"dataset": "fundamentals", "reason": "EMPTY"})
    if moneyflow.empty:
        missing.append({"dataset": "moneyflow", "reason": "EMPTY"})

    return BigFishDataset(
        as_of=as_of,
        settings=settings,
        trade_dates=[d for d in trade_dates if d <= as_of],
        universe=universe,
        prices=prices,
        basic=basic,
        indices=indices,
        industry_daily=industry_daily,
        industry_list=industry_list,
        membership=membership,
        fundamentals=fundamentals,
        forecast=forecast,
        express=express,
        moneyflow=moneyflow,
        holder_trades=holder,
        repurchase=repurchase,
        missing=missing,
    )
