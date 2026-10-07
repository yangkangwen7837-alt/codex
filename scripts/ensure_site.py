"""轻量看护：网站没在跑就把它拉起来（设计给 Windows 计划任务每 5 分钟调用）。

与 site_patrol.py 的区别：
    * 只做一件事——确认端口上的网站活着，不写巡检报告、不检查其它项，几十毫秒级；
    * 健康检查失败才启动 start_site.py，健康时**什么都不做**（不写文件、不重启）；
    * 启动后最多等 40 秒确认恢复，并把结果写进 output/site.log（不覆盖旧日志）。

用法：
    python scripts/ensure_site.py            # 检查并按需拉起
    python scripts/ensure_site.py --json     # 机器可读结果

退出码：0 = 网站正常（含刚被拉起）；1 = 拉起失败。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bigfish import OUTPUT_DIR  # noqa: E402
from bigfish.config import load_settings  # noqa: E402

LOG_FILE = OUTPUT_DIR / "site.log"


def site_ok(port: int, timeout: int = 5) -> bool:
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/_stcore/health", timeout=timeout) as resp:
            return resp.status == 200 and resp.read().decode(errors="replace").strip().lower().startswith("ok")
    except Exception:  # noqa: BLE001
        return False


def start_site(port: int) -> int | None:
    """以独立进程启动网站（不随调用者退出而结束）。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(f"\n=== {datetime.now().astimezone().isoformat(timespec='seconds')} 由看护脚本拉起 ===\n")
        fh.flush()
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "start_site.py"), "--port", str(port)],
            cwd=str(ROOT), env=env, stdout=fh, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, close_fds=True, creationflags=flags,
        )
    return proc.pid


def main() -> int:
    parser = argparse.ArgumentParser(description="网站看护：没运行就拉起来")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--wait", type=int, default=40, help="拉起后最多等待多少秒确认恢复")
    args = parser.parse_args()

    port = int(load_settings().path("site.port", 8501))
    result = {"checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "port": port, "already_running": False, "started": False, "pid": None}

    if site_ok(port):
        result["already_running"] = True
    else:
        result["pid"] = start_site(port)
        deadline = time.time() + args.wait
        while time.time() < deadline:
            time.sleep(2)
            if site_ok(port):
                result["started"] = True
                break

    result["ok"] = result["already_running"] or result["started"]
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        if result["already_running"]:
            print(f"网站正常（端口 {port}）")
        elif result["started"]:
            print(f"网站之前没在运行，已拉起（pid {result['pid']}）")
        else:
            print(f"网站未恢复：已尝试拉起（pid {result['pid']}），{args.wait} 秒内健康检查仍未通过")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
