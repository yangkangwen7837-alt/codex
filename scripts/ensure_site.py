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
import socket
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

# 无人值守运行（计划任务 / pythonw）时不弹出任何控制台窗口
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ps_quote(text: str) -> str:
    """PowerShell 单引号字符串转义（内部的单引号翻倍）。"""
    return "'" + text.replace("'", "''") + "'"


def site_ok(port: int, timeout: int = 5) -> bool:
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/_stcore/health", timeout=timeout) as resp:
            return resp.status == 200 and resp.read().decode(errors="replace").strip().lower().startswith("ok")
    except Exception:  # noqa: BLE001
        return False


def _port_in_use_by_other(port: int, timeout: int = 3) -> bool:
    """端口已被占用但不是我们的站点（例如别的项目/容器抢了同一个端口）。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def start_site(port: int) -> int | None:
    """启动网站，并让它**脱离当前进程树**。

    为什么不能直接用 subprocess.Popen：
        Codex 沙箱、计划任务都可能用「作业对象（Job Object）」管理进程，调用者一退出，
        整棵子进程树会被一起结束 —— 这正是网站反复消失的原因。
        因此这里优先让 **Windows 计划任务**（BigFishSiteRun）来承载站点进程：
        进程由任务计划服务创建，不在看护脚本/终端的进程树里，谁退出都不影响它，
        而且任务本身配置了"失败自动重启 + 允许电池供电"。
    """
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(f"\n=== {datetime.now().astimezone().isoformat(timespec='seconds')} 由看护脚本拉起 ===\n")

    # 首选：交给计划任务启动（进程不在本进程树里，最稳）
    try:
        task = subprocess.run(["schtasks", "/Run", "/TN", "BigFishSiteRun"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60, creationflags=_NO_WINDOW)
        if task.returncode == 0:
            deadline = time.time() + 30
            while time.time() < deadline:
                time.sleep(2)
                if site_ok(port):
                    return _site_pid(port)
            print("（计划任务已触发，但 30 秒内健康检查仍未通过，改用普通方式再试）")
        else:
            print(f"（计划任务启动失败：{((task.stdout or '') + (task.stderr or '')).strip()[:160]}，改用普通方式）")
    except Exception as exc:  # noqa: BLE001
        print(f"（计划任务启动异常：{type(exc).__name__}: {exc}，改用普通方式）")

    # 兜底：普通方式启动（不依赖计划任务是否存在）
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | _NO_WINDOW)
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(f"\n=== {datetime.now().astimezone().isoformat(timespec='seconds')} 由看护脚本拉起（普通方式） ===\n")
        fh.flush()
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "start_site.py"), "--port", str(port), "--log-to-file"],
            cwd=str(ROOT), env=env, stdout=fh, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, close_fds=True, creationflags=flags,
        )
    return proc.pid


def _site_pid(port: int) -> int | None:
    """找出监听指定端口的进程号（仅用于日志展示，失败无妨）。"""
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-Command",
                               f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue).OwningProcess"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=30, creationflags=_NO_WINDOW)
        text = (proc.stdout or "").strip().splitlines()
        return int(text[0]) if text and text[0].strip().isdigit() else None
    except Exception:  # noqa: BLE001
        return None


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
        result["port_maybe_taken"] = _port_in_use_by_other(port)
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
        elif result.get("port_maybe_taken"):
            print(f"网站没起来：端口 {port} 已被**其它程序**占用（健康检查来自别的服务）。"
                  f"请改 configs/default.yaml 的 site.port，或先停掉占用端口的进程。")
        else:
            print(f"网站未恢复：已尝试拉起（pid {result['pid']}），{args.wait} 秒内健康检查仍未通过")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
