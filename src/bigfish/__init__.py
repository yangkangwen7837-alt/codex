"""基本面反转 Big Fish Agent（规格书 v1）。

分层：LEVEL1 Market Regime → LEVEL2 ODS → LEVEL3 FRS → LEVEL4 LIS
→ LEVEL5 PCS → LEVEL6 RPS + Catalyst → BFS → S/A+/A/B/C → 动作。
"""
from __future__ import annotations

from pathlib import Path

__version__ = "0.5.0"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "configs"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
OUTPUT_DIR = PROJECT_ROOT / "output"

for _p in (RAW_DIR, PROCESSED_DIR, OUTPUT_DIR):
    _p.mkdir(parents=True, exist_ok=True)

__all__ = [
    "__version__",
    "PROJECT_ROOT",
    "CONFIG_DIR",
    "RAW_DIR",
    "PROCESSED_DIR",
    "OUTPUT_DIR",
]
