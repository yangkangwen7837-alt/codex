"""跑一次数据更新（抓数 → 跑批 → 观察池跟踪），供 Windows 计划任务调用。

为什么需要它：
    网站自带 APScheduler 的定时更新需要 apscheduler 包，而这个包只存在于 Codex 沙箱层，
    由 Windows 计划任务以普通身份启动的站点看不到它。于是把"定时"交给 Windows 计划任务，
    由本脚本执行**一次性**更新——不依赖 apscheduler，站点是否在前台运行都不影响。

用法：
    python scripts/run_update_once.py                 # 按配置步骤更新
    python scripts/run_update_once.py --steps flicker # 只跑某几步（调试用）
    python scripts/run_update_once.py --publish       # 更新完再把快照发布到 GitHub

输出会追加到 output/update_task.log，退出码 0 = 成功、1 = 失败（计划任务据此判定）。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bigfish import OUTPUT_DIR  # noqa: E402
from bigfish.update import UpdateService  # noqa: E402

LOG_FILE = OUTPUT_DIR / "update_task.log"


def log(message: str) -> None:
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    line = f"[{stamp}] {message}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="一次性数据更新")
    parser.add_argument("--steps", default=None, help="逗号分隔：fetch,daily,flicker")
    parser.add_argument("--publish", action="store_true", help="更新后发布快照到 GitHub")
    args = parser.parse_args()

    steps = [s.strip() for s in args.steps.split(",") if s.strip()] if args.steps else None
    log(f"开始更新（trigger=windows-task，steps={steps or '按配置'}）")
    status = UpdateService().run(steps=steps, trigger="windows-task")
    for step in status.get("steps", []):
        log(f"  {step['label']}：{step['state']} — {step.get('detail', '')}")
    log(f"更新结束：state={status['state']} message={status.get('message')}")
    if status["state"] != "done":
        return 1

    if args.publish:
        proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "publish_snapshot.py")],
                              cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=900)
        tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-4:]
        log("快照发布：" + ("成功" if proc.returncode == 0 else f"失败（rc={proc.returncode}）")
            + " | " + " / ".join(tail))
        return 0 if proc.returncode == 0 else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
