"""到点没跑就补跑：检查最近的更新时点（工作日 08:30 / 16:30）是否已经执行过。

为什么需要它：
    计划任务只能保证"机器醒着时按时触发"。电脑睡眠/关机跨过了 08:30 或 16:30，
    那次更新就**不会被补跑**（StartWhenAvailable 对睡眠场景不可靠），
    结果是一整天用着旧数据。这个脚本由一个每 15 分钟的看护任务调用：
    发现最近一个时点还没跑就立刻唤醒对应的更新任务，跑过了就什么都不做。

用法：
    python scripts/ensure_update.py            # 检查并按需补跑
    python scripts/ensure_update.py --json     # 机器可读
    python scripts/ensure_update.py --dry-run  # 只看判断结果，不触发

退出码：0 = 无需补跑或已成功触发；1 = 触发失败。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bigfish import OUTPUT_DIR  # noqa: E402

LOG_FILE = OUTPUT_DIR / "update_task.log"
SLOTS = (dt.time(8, 30), dt.time(16, 30))        # 工作日两个更新时点
STAMP = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")
TASK_FOR_SLOT = {dt.time(8, 30): "BigFishUpdateAM", dt.time(16, 30): "BigFishUpdatePM"}


def last_finished() -> dt.datetime | None:
    """最近一次成功完成的更新时间（取 update_task.log 里最后一条 state=done）。"""
    if not LOG_FILE.exists():
        return None
    last = None
    for line in LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        if "更新结束" not in line or "state=done" not in line:
            continue
        match = STAMP.match(line.strip())
        if match:
            try:
                last = dt.datetime.fromisoformat(match.group(1))
            except ValueError:
                continue
    return last


def due_slot(now: dt.datetime) -> tuple[dt.datetime, str] | None:
    """最近一个"应该已经跑完"的时点；没有（比如周末）返回 None。"""
    today = now.date()
    if today.weekday() >= 5:                      # 周末不更新
        return None
    for slot in reversed(SLOTS):
        when = dt.datetime.combine(today, slot)
        if when <= now:
            return when, TASK_FOR_SLOT[slot]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="检查更新是否漏跑并按需补跑")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    now = dt.datetime.now()
    due = due_slot(now)
    finished = last_finished()
    result = {"checked_at": now.isoformat(timespec="seconds"),
              "last_finished": finished.isoformat(timespec="seconds") if finished else None,
              "due": due[0].isoformat(timespec="seconds") if due else None,
              "task": due[1] if due else None, "triggered": False}

    if due is None:
        result["reason"] = "非工作日或还没到时点"
    elif finished is not None and finished >= due[0]:
        result["reason"] = "时点已按计划完成"
    else:
        task = due[1]
        result["reason"] = f"{due[0]:%m-%d %H:%M} 的更新没有完成，需要补跑（{task}）"
        if not args.dry_run:
            proc = subprocess.run(["schtasks", "/Run", "/TN", task],
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=60)
            result["triggered"] = proc.returncode == 0
            result["detail"] = ((proc.stdout or "") + (proc.stderr or "")).strip()[:160]

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"{now:%Y-%m-%d %H:%M} 最近时点："
              f"{result['due'] or '（无）'}；上次完成：{result['last_finished'] or '（无记录）'}")
        print(("已触发补跑：" if result["triggered"] else "动作：") + result["reason"])
        if result.get("detail"):
            print("  ", result["detail"])
    return 0 if (result["triggered"] or due is None or result["reason"] == "时点已按计划完成") else 1


if __name__ == "__main__":
    sys.exit(main())
