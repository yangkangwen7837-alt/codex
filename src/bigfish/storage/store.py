"""本地 Parquet 缓存（断点续跑）。

每个数据集落成 data/raw/<group>/<name>.parquet，并附带 <name>.meta.json，
记录 endpoint / 拉取时间 / 行数 / 数据源，满足规格书第 67 节的可追溯要求。
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .. import RAW_DIR


class ParquetStore:
    """按 (group, name) 缓存的 Parquet 数据集。"""

    def __init__(self, root: str | Path = RAW_DIR) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _paths(self, group: str, name: str) -> tuple[Path, Path]:
        folder = self.root / group
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{name}.parquet", folder / f"{name}.meta.json"

    def exists(self, group: str, name: str) -> bool:
        path, _ = self._paths(group, name)
        return path.exists()

    def read(self, group: str, name: str) -> pd.DataFrame:
        path, _ = self._paths(group, name)
        return pd.read_parquet(path)

    def meta(self, group: str, name: str) -> dict:
        _, meta_path = self._paths(group, name)
        if not meta_path.exists():
            return {}
        return json.loads(meta_path.read_text(encoding="utf-8"))

    def write(self, df: pd.DataFrame, group: str, name: str, **meta) -> Path:
        path, meta_path = self._paths(group, name)
        tmp_fd, tmp_name = tempfile.mkstemp(suffix=".parquet", dir=str(path.parent))
        os.close(tmp_fd)
        try:
            df.to_parquet(tmp_name, index=False)
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
        payload = {
            "group": group,
            "name": name,
            "rows": int(len(df)),
            "columns": list(df.columns),
            "written_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "data_source": "tushare",
            **meta,
        }
        meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def inventory(self) -> pd.DataFrame:
        rows = []
        for meta_file in sorted(self.root.rglob("*.meta.json")):
            payload = json.loads(meta_file.read_text(encoding="utf-8"))
            rows.append(
                {
                    "group": payload.get("group"),
                    "name": payload.get("name"),
                    "rows": payload.get("rows"),
                    "written_at": payload.get("written_at"),
                    "endpoint": payload.get("endpoint"),
                    "trade_date": payload.get("trade_date"),
                    "period": payload.get("period"),
                    "ts_code": payload.get("ts_code"),
                }
            )
        return pd.DataFrame(rows)


def latest_local_trade_date(before: str | None = None, root: str | Path = RAW_DIR) -> str:
    """本地已落盘的最新交易日（只用本地文件，不依赖交易日历缓存键）。"""
    folder = Path(root) / "daily"
    if not folder.exists():
        return ""
    dates = sorted(p.stem for p in folder.glob("*.parquet") if len(p.stem) == 8)
    if before:
        dates = [d for d in dates if d <= before]
    return dates[-1] if dates else ""
