"""看板数据层：所有页面共用的加载、缓存与格式化。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bigfish import OUTPUT_DIR as LIVE_OUTPUT_DIR  # noqa: E402
from bigfish import PROCESSED_DIR as LIVE_PROCESSED_DIR  # noqa: E402
from bigfish.config import is_streamlit_cloud, load_settings  # noqa: E402
from bigfish.update import (UpdateService, load_update_settings,  # noqa: E402
                            save_update_settings, scheduler_status)

VERSION_FILES = ("bigfish_score_latest.parquet", "bigfish_history.parquet",
                 "run_summary.json", "flicker_status.json", "update_status.json")

# 部署说明：data/raw 与 data/processed 约 2.9 GB，不进 Git，只留在本地。
# 仓库里随代码发布的是「最新一期」小快照（data/snapshot，约 4.5 MB，由
# scripts/export_snapshot.py 导出）。本地有实时数据时永远读实时数据；
# 云端（仓库里没有 processed 产物）自动回退到快照，页面因此不会空白。
SNAPSHOT_DIR = ROOT / "data" / "snapshot"


def _pick_dir(live: Path, snapshot: Path, pattern: str) -> Path:
    if any(live.glob(pattern)):
        return live
    return snapshot if any(snapshot.glob(pattern)) else live


PROCESSED_DIR = _pick_dir(LIVE_PROCESSED_DIR, SNAPSHOT_DIR / "processed", "*.parquet")
OUTPUT_DIR = _pick_dir(LIVE_OUTPUT_DIR, SNAPSHOT_DIR / "output", "*.md")
USING_SNAPSHOT = PROCESSED_DIR != LIVE_PROCESSED_DIR


# ---------------------------------------------------------------- 基础读取
@st.cache_data(ttl=300, show_spinner=False)
def _read_parquet(path_str: str, mtime: int) -> pd.DataFrame:
    """mtime 参与缓存键：文件一更新，缓存自动失效。"""
    return pd.read_parquet(path_str)


@st.cache_data(ttl=60, show_spinner=False)
def _read_json(path_str: str, mtime: int) -> dict | list:
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


def _parquet(path: Path) -> pd.DataFrame:
    return _read_parquet(str(path), int(path.stat().st_mtime)) if path.exists() else pd.DataFrame()


def _json(path: Path, default):
    return _read_json(str(path), int(path.stat().st_mtime)) if path.exists() else default


def settings():
    return load_settings()


def is_cloud() -> bool:
    """是否运行在 Streamlit Community Cloud（只读部署，禁写数据）。"""
    return is_streamlit_cloud()


def data_version() -> str:
    parts = []
    for name in VERSION_FILES:
        path = PROCESSED_DIR / name
        parts.append(f"{name}:{int(path.stat().st_mtime)}" if path.exists() else f"{name}:0")
    return "|".join(parts)


# ---------------------------------------------------------------- 数据集
def scores() -> pd.DataFrame:
    """当日全市场评分截面。"""
    return _parquet(PROCESSED_DIR / "bigfish_score_latest.parquet")


def industry_ods() -> pd.DataFrame:
    files = sorted(PROCESSED_DIR.glob("industry_ods_*.parquet"))
    return _parquet(files[-1]) if files else pd.DataFrame()


def history() -> pd.DataFrame:
    """逐日观察池历史（用于连续在榜、名单变化）。"""
    return _parquet(PROCESSED_DIR / "bigfish_history.parquet")


def trend() -> pd.DataFrame:
    """候选股票的季度财务趋势。"""
    return _parquet(PROCESSED_DIR / "fundamental_trend_latest.parquet")


def run_summary() -> dict:
    return _json(PROCESSED_DIR / "run_summary.json", {})


def flicker_status() -> dict:
    return _json(PROCESSED_DIR / "flicker_status.json", {})


def update_status() -> dict:
    """最近一次更新状态。本地读实时目录，云端回退到快照目录。"""
    return _json(PROCESSED_DIR / "update_status.json",
                 {"state": "idle", "steps": [], "message": "尚未运行过更新"})


def update_history() -> list[dict]:
    return _json(PROCESSED_DIR / "update_history.json", [])


def update_settings() -> dict:
    return load_update_settings()


def set_update_settings(**kwargs) -> dict:
    if is_cloud():
        return update_settings()
    result = save_update_settings(**kwargs)
    _read_json.clear()
    return result


def schedule_status() -> dict:
    return scheduler_status()


def run_update(trigger: str = "manual") -> dict:
    if is_cloud():
        # 云端文件系统只读：不触发抓数/跑批，返回可读提示，避免页面报错。
        return {"state": "blocked", "trigger": trigger, "steps": [],
                "message": "Streamlit Cloud 为只读部署：数据由本地或常驻进程更新后再发布，"
                           "本页面不执行抓数与跑批。"}
    status = UpdateService().run(trigger=trigger)
    st.cache_data.clear()
    return status


def daily_brief_text() -> str:
    files = sorted(OUTPUT_DIR.glob("daily_brief_*.md"))
    return files[-1].read_text(encoding="utf-8") if files else ""


def cards_text() -> str:
    files = sorted(OUTPUT_DIR.glob("bigfish_cards_*.md"))
    return files[-1].read_text(encoding="utf-8") if files else ""


# ---------------------------------------------------------------- 视图工具
def pool(frame: pd.DataFrame, grade_min: str = "C") -> pd.DataFrame:
    """可入池集合：可交易、非 Risk Block、评级不低于 grade_min、动作非 EXIT/REJECT。"""
    if frame.empty:
        return frame
    order = ["S", "A+", "A", "B", "C", "Reject"]
    # order 是从好到差排列的，所以"不低于 grade_min"是取前缀
    allowed = order[: order.index(grade_min) + 1] if grade_min in order else order[:-1]
    return frame.loc[
        frame["tradeable"].fillna(False).astype(bool)
        & ~frame.get("risk_block", False).fillna(False).astype(bool)
        & frame["grade"].astype(str).isin(allowed)
        & ~frame.get("action", "").astype(str).isin(["EXIT", "REJECT"])
    ].copy()


def pct(value, digits: int = 1) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "—"
    return "—" if pd.isna(num) else f"{num * 100:.{digits}f}%"


def num(value, digits: int = 1) -> str:
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "—"
    return "—" if pd.isna(val) else f"{val:.{digits}f}"


def latest_trade_date() -> str:
    summary = run_summary()
    return str(summary.get("as_of") or summary.get("latest_trade_date") or "—")
