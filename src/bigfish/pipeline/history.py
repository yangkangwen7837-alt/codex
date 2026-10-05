"""回测数据层：按调仓日滑动装配 as-of 数据集（point-in-time）。

设计要点：
* 行情按交易日分片存在 data/raw/daily/<date>.parquet，这里用有界缓存滑动读取，
  任何时候只驻留最近 320 个交易日的分片，避免把 8 年全市场行情一次读进内存；
* 财务面板只构建一次（含全部报告期），每个 as-of 只保留 ann_date ≤ as_of 的公告；
* 前视收益另走一遍独立的价格读取，与信号计算完全分离，避免"顺手用了未来价格"。
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import Settings, load_settings
from ..pipeline.dataset import BigFishDataset
from ..pipeline.fundamentals import build_fundamental_panel
from ..storage import ParquetStore


class _FrameCache:
    """按日期缓存 parquet 分片，超过 capacity 时淘汰最早的。"""

    def __init__(self, store: ParquetStore, group: str, capacity: int = 900) -> None:
        self.store = store
        self.group = group
        self.capacity = capacity
        self._cache: OrderedDict[str, pd.DataFrame] = OrderedDict()
        self.reads = 0

    def get(self, date: str) -> pd.DataFrame | None:
        if date in self._cache:
            self._cache.move_to_end(date)
            return self._cache[date]
        path = self.store.root / self.group / f"{date}.parquet"
        if not path.exists():
            return None
        frame = pd.read_parquet(path)
        self.reads += 1
        self._cache[date] = frame
        while len(self._cache) > self.capacity:
            self._cache.popitem(last=False)
        return frame


class HistoryData:
    """回测用的历史数据仓库。"""

    def __init__(self, settings: Settings | None = None, store: ParquetStore | None = None) -> None:
        self.settings = settings or load_settings()
        self.store = store or ParquetStore()
        s = self.settings
        self.start = str(s.path("backtest.data_start", "20180101"))
        self.end = str(s.path("backtest.end", s.path("data.end_date")))

        # ---- 交易日序列（直接用已落盘的 daily 分片名） ----
        daily_dir = self.store.root / "daily"
        all_dates = sorted(p.stem for p in daily_dir.glob("*.parquet") if len(p.stem) == 8)
        self.daily_dates = [d for d in all_dates if self.start <= d <= self.end]
        if not self.daily_dates:
            raise RuntimeError("没有可用的日线分片，请先运行 scripts/fetch_history.py")

        self.daily_cache = _FrameCache(self.store, "daily")
        self.basic_cache = _FrameCache(self.store, "daily_basic", capacity=12)
        self.moneyflow_cache = _FrameCache(self.store, "moneyflow", capacity=140)

        # ---- 常驻小表 ----
        self.universe = self._load_universe()
        self.membership = self._read("meta", "sw_membership")
        self.industry_list = self._read("meta", "sw_l1_list")
        self.indices = self._stack("index_daily")
        self.industry_daily = self._stack("sw_daily")
        self.forecast = self._stack("forecast")
        self.express = self._stack("express")
        self.holder_trades = self._read("holder_trade", f"{self.start}_{self.end}")
        self.repurchase = self._read("repurchase", f"{self.start}_{self.end}")
        self.fundamentals = self._build_fundamentals()

    # ------------------------------------------------------------------ 基础
    def _read(self, group: str, name: str) -> pd.DataFrame:
        return self.store.read(group, name) if self.store.exists(group, name) else pd.DataFrame()

    def _stack(self, group: str) -> pd.DataFrame:
        folder = self.store.root / group
        if not folder.exists():
            return pd.DataFrame()
        frames = [pd.read_parquet(p) for p in sorted(folder.glob("*.parquet"))]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def _load_universe(self) -> pd.DataFrame:
        frames = [self._read("meta", f"stock_basic_{s}") for s in ("L", "D", "P")]
        frames = [f for f in frames if not f.empty]
        if not frames:
            return pd.DataFrame()
        uni = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["ts_code"])
        from .dataset import _board

        market = uni.get("market", pd.Series(dtype=object))
        uni["board"] = [_board(c, m) for c, m in zip(uni["ts_code"], market)]
        return uni.reset_index(drop=True)

    def _build_fundamentals(self) -> pd.DataFrame:
        income = self._stack("income_vip")
        if income.empty:
            return pd.DataFrame()
        balance = self._stack("balancesheet_vip")
        cashflow = self._stack("cashflow_vip")
        indicator = self._stack("fina_indicator_vip")
        panel = build_fundamental_panel(income, balance, cashflow, indicator)
        if not panel.empty:
            panel = panel.sort_values(["ts_code", "end_date"]).reset_index(drop=True)
        return panel

    # ------------------------------------------------------------ as-of 视图
    def dataset_at(self, as_of: str, window_days: int = 320, moneyflow_days: int = 40) -> BigFishDataset:
        dates = [d for d in self.daily_dates if d <= as_of][-window_days:]
        price_frames, basic_frames = [], []
        for date in dates:
            frame = self.daily_cache.get(date)
            if frame is not None:
                price_frames.append(frame)
        for date in dates[-3:]:
            frame = self.basic_cache.get(date)
            if frame is not None:
                basic_frames.append(frame)
        mf_frames = []
        for date in [d for d in self.daily_dates if d <= as_of][-moneyflow_days:]:
            frame = self.moneyflow_cache.get(date)
            if frame is not None:
                mf_frames.append(frame)

        prices = pd.concat(price_frames, ignore_index=True) if price_frames else pd.DataFrame()
        basic = pd.concat(basic_frames, ignore_index=True) if basic_frames else pd.DataFrame()
        moneyflow = pd.concat(mf_frames, ignore_index=True) if mf_frames else pd.DataFrame()

        fundamentals = self.fundamentals
        if not fundamentals.empty:
            fundamentals = fundamentals.loc[fundamentals["ann_date"].astype(str) <= as_of]

        def clip_ann(frame: pd.DataFrame) -> pd.DataFrame:
            if frame is None or frame.empty or "ann_date" not in frame.columns:
                return pd.DataFrame()
            return frame.loc[frame["ann_date"].astype(str) <= as_of]

        indices = self.indices.loc[self.indices["trade_date"].astype(str) <= as_of] if not self.indices.empty else self.indices
        industry_daily = self.industry_daily.loc[
            self.industry_daily["trade_date"].astype(str) <= as_of
        ] if not self.industry_daily.empty else self.industry_daily

        return BigFishDataset(
            as_of=as_of,
            settings=self.settings,
            trade_dates=dates,
            universe=self.universe,
            prices=prices,
            basic=basic,
            indices=indices,
            industry_daily=industry_daily,
            industry_list=self.industry_list,
            membership=self.membership,
            fundamentals=fundamentals,
            forecast=clip_ann(self.forecast),
            express=clip_ann(self.express),
            moneyflow=moneyflow,
            holder_trades=clip_ann(self.holder_trades),
            repurchase=clip_ann(self.repurchase),
            missing=[],
        )

    # ------------------------------------------------------------ 前视收益
    def forward_returns(self, signals: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
        """按调仓日分批计算前视收益（次日开盘入场，T+h 收盘出场）。

        用 pct_chg 复利口径计算区间收益，避免未复权价格把分红送转记成亏损；
        入场日按 收盘/开盘 折算，实现"次日开盘价入场"。

        注意：**每个持有期独立判断可用性**。早先的实现要求 250 个交易日的完整窗口，
        否则该调仓日的所有持有期一起丢弃，导致最近一年（2025-09 之后）的信号被静默排除；
        现在改为"能算多长算多长"，并用 ``horizon_full`` 标记窗口是否完整。
        """
        out_rows = []
        max_h = max(horizons)
        for as_of, group in signals.groupby("as_of", sort=True):
            as_of = str(as_of)
            future = [d for d in self.daily_dates if d > as_of][: max_h + 1]
            if not future:
                continue
            frames: dict[str, pd.DataFrame] = {}
            for date in [as_of] + future:
                frame = self.daily_cache.get(date)
                if frame is not None and not frame.empty:
                    frames[date] = frame.set_index("ts_code")
            entry_date = future[0]
            if entry_date not in frames:
                continue
            codes = group["ts_code"].tolist()
            entry_frame = frames[entry_date]
            entry_open = pd.to_numeric(entry_frame.get("open"), errors="coerce").reindex(codes)
            entry_close = pd.to_numeric(entry_frame.get("close"), errors="coerce").reindex(codes)
            intraday = entry_close / entry_open.replace(0, np.nan)

            pct = pd.DataFrame(index=codes, columns=future, dtype=float)
            for date in future:
                frame = frames.get(date)
                if frame is None:
                    pct[date] = np.nan
                    continue
                pct[date] = pd.to_numeric(frame.get("pct_chg"), errors="coerce").reindex(codes) / 100.0
            obs = pct.notna().sum(axis=1)
            cum = (1.0 + pct.fillna(0.0)).cumprod(axis=1)
            # 以 entry_date 开盘为基准：rel = cum × (收盘/开盘) ÷ cum[entry]
            rel = cum.mul(intraday, axis=0).div(cum[entry_date], axis=0)

            rec = {"as_of": as_of, "ts_code": codes}
            for h in horizons:
                if h < len(future):
                    rec[f"ret{h}"] = (rel[future[h]] - 1.0).to_numpy()
            rec["mae"] = (rel.min(axis=1) - 1.0).to_numpy()
            rec["mfe"] = (rel.max(axis=1) - 1.0).to_numpy()
            rec["obs_days"] = obs.to_numpy()
            rec["expected_days"] = len(future)
            rec["horizon_full"] = bool(len(future) >= max_h + 1)
            close_t = pd.to_numeric(frames[as_of].get("close"), errors="coerce").reindex(codes) if as_of in frames else pd.Series(np.nan, index=codes)
            rec["entry_gap"] = (entry_open / close_t).to_numpy() - 1.0
            rec["max_abs_move"] = pct.abs().max(axis=1).to_numpy()
            out_rows.append(pd.DataFrame(rec))
        return pd.concat(out_rows, ignore_index=True) if out_rows else pd.DataFrame()
