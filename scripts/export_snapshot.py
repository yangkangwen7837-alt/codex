"""把「最新一期」的结果导出成一个很小的快照，随仓库发布给 Streamlit Cloud 用。

为什么需要它：
    原始数据与跑批产物（``data/raw``、``data/processed``）合计约 2.9 GB，必须留在本地不进 Git；
    但云端仓库里没有这些文件，页面就会是空的。于是把**页面真正读取的那几个文件**复制到
    ``data/snapshot/``（约 4.5 MB，只保留最新一期），云端页面就会自动回退读取快照。

用法：
    python scripts/export_snapshot.py            # 导出 / 刷新快照
    python scripts/export_snapshot.py --dry-run  # 只打印将要复制的文件

本脚本只读 ``data/processed`` 与 ``output``（不改动、不删除原件），只写 ``data/snapshot/``。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bigfish import OUTPUT_DIR, PROCESSED_DIR  # noqa: E402

SNAPSHOT = ROOT / "data" / "snapshot"
SNAP_PROCESSED = SNAPSHOT / "processed"
SNAP_OUTPUT = SNAPSHOT / "output"

# 页面直接读取的文件（见 apps/common.py 的读取函数）
PROCESSED_FIXED = [
    "bigfish_score_latest.parquet",        # 全市场评分 → 观察池/重点/早期/矩阵/Kill
    "bigfish_history.parquet",             # 观察池在榜历史 → streak
    "fundamental_trend_latest.parquet",    # 基本面趋势
    "run_summary.json",                    # 市场状态 / 各榜单条数
    "flicker_status.json",                 # 闪烁率跟踪
    "update_status.json",                  # 最近一次更新状态
    "update_history.json",                 # 更新历史
]
PROCESSED_GLOB = ["industry_ods_*.parquet"]      # 行业 ODS，只取最新一期

# output 下按交易日结尾的交付物，只保留最新一期
OUTPUT_PATTERNS = [
    "daily_brief_{date}.md",
    "bigfish_cards_{date}.md",
    "bigfish_watchlist_{date}.csv",
    "bigfish_ranking_{date}.csv",
    "bigfish_ranking_{date}.md",
    "bigfish_early_{date}.csv",
    "data_quality_{date}.md",
    "opportunity_ponds_{date}.csv",
    "interface_{date}.json",
    "verification_sample_{date}.csv",
    "verification_sample_{date}.md",
]


def latest_trade_date() -> str:
    path = PROCESSED_DIR / "run_summary.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        as_of = str(payload.get("as_of") or "")
        if as_of:
            return as_of
    dates = sorted({p.stem.split("_")[-1] for p in OUTPUT_DIR.glob("daily_brief_*.md")})
    return dates[-1] if dates else ""


def collect(date: str) -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    for name in PROCESSED_FIXED:
        src = PROCESSED_DIR / name
        if src.exists():
            pairs.append((src, SNAP_PROCESSED / name))
    for pattern in PROCESSED_GLOB:
        files = sorted(PROCESSED_DIR.glob(pattern))
        if files:
            pairs.append((files[-1], SNAP_PROCESSED / files[-1].name))
    if date:
        for pattern in OUTPUT_PATTERNS:
            src = OUTPUT_DIR / pattern.format(date=date)
            if src.exists():
                pairs.append((src, SNAP_OUTPUT / src.name))
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description="导出最新一期数据快照")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    date = latest_trade_date()
    if not date:
        print("找不到最新交易日（data/processed/run_summary.json 缺失）——请先跑 scripts/run_daily.py")
        return 1

    pairs = collect(date)
    if not pairs:
        print("没有可导出的文件。")
        return 1

    total = sum(src.stat().st_size for src, _ in pairs) / 1e6
    print(f"最新交易日：{date}    待导出 {len(pairs)} 个文件，合计 {total:.2f} MB")
    for src, dst in pairs:
        print(f"  {src.relative_to(ROOT)}  →  {dst.relative_to(ROOT)}")
    if args.dry_run:
        print("\n（--dry-run：未写入）")
        return 0

    keep = {dst for _, dst in pairs}
    for target_dir in (SNAP_PROCESSED, SNAP_OUTPUT):
        if target_dir.exists():
            for old in target_dir.iterdir():
                if old.is_file() and old not in keep:
                    old.unlink()          # 只清理快照目录里的上一期文件
                    print(f"  清理旧快照：{old.relative_to(ROOT)}")
    for src, dst in pairs:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    written = sum(dst.stat().st_size for dst in keep)
    print(f"\n快照已更新：{SNAPSHOT.relative_to(ROOT)}  共 {len(keep)} 个文件 / {written / 1e6:.2f} MB")
    print("下一步：git add data/snapshot && git commit && git push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
