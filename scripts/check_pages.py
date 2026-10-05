"""页面渲染自检（无头，不写数据）：三种模式下逐一渲染 8 个页面 + 入口。

用法：
    python scripts/check_pages.py              # 正常数据（本地）
    python scripts/check_pages.py --cloud      # 模拟 Streamlit Cloud 只读模式
    python scripts/check_pages.py --coldstart  # 数据目录为空（首次部署）
    python scripts/check_pages.py --all        # 三种模式全跑

退出码：0 = 全部通过，1 = 有页面报错。本脚本只读，不修改业务数据。
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = ["radar", "watchlist", "early", "ponds", "matrix", "fundamental", "kill", "ops"]


def _prepare(mode: str) -> str:
    """返回该模式的说明文字。"""
    sys.path.insert(0, str(ROOT / "apps"))
    sys.path.insert(0, str(ROOT / "src"))
    if mode == "cloud":
        os.environ["BIGFISH_FORCE_CLOUD"] = "1"
        return "云端只读模式（BIGFISH_FORCE_CLOUD=1）"
    if mode == "coldstart":
        empty = Path(tempfile.mkdtemp(prefix="bigfish_empty_"))
        import common as C                       # noqa: PLC0415
        from bigfish import update as U           # noqa: PLC0415
        from bigfish.update import scheduler as S  # noqa: PLC0415

        C.PROCESSED_DIR = empty
        C.OUTPUT_DIR = empty
        U.service.PROCESSED_DIR = empty
        U.service.STATUS_FILE = empty / "update_status.json"
        U.service.HISTORY_FILE = empty / "update_history.json"
        U.service.SETTINGS_FILE = empty / "update_settings.json"
        U.service.LOCK_FILE = empty / "update.lock"
        S.HEARTBEAT_FILE = empty / "scheduler_heartbeat.json"
        return f"空数据冷启动（数据目录 = {empty}）"
    return "正常数据（本地）"


def run_mode(mode: str) -> int:
    from streamlit.testing.v1 import AppTest  # noqa: PLC0415

    print(f"== 模式：{_prepare(mode)}")
    targets = [(name, ROOT / "apps" / "app_pages" / f"{name}.py") for name in PAGES]
    targets.append(("streamlit_app", ROOT / "apps" / "streamlit_app.py"))

    failed = []
    for name, path in targets:
        at = AppTest.from_file(str(path), default_timeout=120).run()
        errors = [e.value for e in at.exception]
        if errors:
            failed.append(name)
            print(f"  [FAIL] {name}: {errors[0]}")
        else:
            print(f"  [OK]   {name}")

    if mode == "cloud":
        import common as C  # noqa: PLC0415

        result = C.run_update(trigger="cloud-check")
        if result.get("state") == "blocked":
            print("  [OK]   云端已拦截手动更新（只读）")
        else:
            failed.append("run_update")
            print(f"  [FAIL] 云端未拦截手动更新：{result.get('state')}")

    print(f"  通过 {len(targets) - len(failed)} / {len(targets)}\n")
    return len(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description="页面渲染自检")
    parser.add_argument("--cloud", action="store_true")
    parser.add_argument("--coldstart", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    modes = ["normal"]
    if args.all:
        modes = ["normal", "cloud", "coldstart"]
    else:
        if args.cloud:
            modes = ["cloud"]
        elif args.coldstart:
            modes = ["coldstart"]

    failures = 0
    for mode in modes:
        failures += run_mode(mode)
    print("全部模式通过" if failures == 0 else f"存在 {failures} 个失败项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
