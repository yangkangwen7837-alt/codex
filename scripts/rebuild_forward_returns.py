"""在已保存的回测面板上重算前视收益（修复"窗口不完整就整条丢弃"的缺陷）。

不需要重跑 65 分钟的信号计算：面板里已含每个调仓日的全部信号行，
只需要用修正后的 forward_returns 重算收益列并重新做数据卫生过滤。

用法：
    python scripts/rebuild_forward_returns.py --panel backtest_v4 --out backtest_v4b
    python scripts/rebuild_forward_returns.py --panel backtest_v3 --out backtest_v3b
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bigfish import PROCESSED_DIR  # noqa: E402
from bigfish.backtest.engine import apply_hygiene  # noqa: E402
from bigfish.config import load_settings  # noqa: E402
from bigfish.pipeline.history import HistoryData  # noqa: E402

FORWARD_COLS = ["ret20", "ret60", "ret120", "ret250", "mae", "mfe", "obs_days",
                "expected_days", "horizon_full", "entry_gap", "max_abs_move"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    settings = load_settings()
    horizons = list(settings.path("backtest.horizons", [20, 60, 120, 250]))
    out_name = args.out or f"{args.panel}_b"

    panel = pd.read_parquet(PROCESSED_DIR / f"{args.panel}.parquet")
    signals = panel.drop(columns=[c for c in FORWARD_COLS if c in panel.columns])
    print(f"[rebuild] 读入 {len(panel):,} 行 / {panel['as_of'].nunique()} 个调仓日")

    history = HistoryData(settings)
    fwd = history.forward_returns(signals[["as_of", "ts_code"]], horizons)
    merged = signals.merge(fwd, on=["as_of", "ts_code"], how="left")
    before = len(merged)
    merged = apply_hygiene(merged)

    covered = merged["ret20"].notna().sum()
    stats_path = PROCESSED_DIR / f"{args.panel}_stats.json"
    stats = json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.exists() else {}
    stats.update({
        "rows_clean": int(len(merged)),
        "rows_raw": int(before),
        "ret20_available": int(covered),
        "ret250_available": int(merged["ret250"].notna().sum()),
        "horizon_full_rows": int(merged["horizon_full"].fillna(False).sum()),
        "note": "前视收益按持有期独立判断可用性（修复'窗口不完整即整条丢弃'缺陷）",
    })
    merged.to_parquet(PROCESSED_DIR / f"{out_name}.parquet", index=False)
    (PROCESSED_DIR / f"{out_name}_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[rebuild] 写出 {out_name}.parquet：{len(merged):,} 行，"
          f"ret20 可用 {covered:,}（{covered / max(len(merged), 1) * 100:.1f}%），"
          f"完整窗口 {stats['horizon_full_rows']:,}")


if __name__ == "__main__":
    main()
