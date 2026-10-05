"""独立调度进程（无人值守部署用；网站进程内也已自带调度器）。

用法：python scripts/run_scheduler.py
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bigfish.update import start_scheduler  # noqa: E402


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    scheduler = start_scheduler()
    if scheduler is None:
        print("调度器启动失败（缺少 APScheduler？）")
        return
    print("调度器已启动，按 Ctrl+C 退出。")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        scheduler.shutdown(wait=False)
        print("已停止。")


if __name__ == "__main__":
    main()
