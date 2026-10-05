"""跑单元测试的包装脚本（解决本机沙箱下的临时目录权限问题）。

背景：pytest 的 ``tmp_path`` 会把基线临时目录设成仅属主可访问（mode 0700）。
本机的 Codex 沙箱进程令牌与普通终端不同，导致**上一次**测试留下的目录
（``tmp/pytest_run``）在下一次运行时既列不出也删不掉，报
``PermissionError: [WinError 5]``。因此这里每次都用一个**全新的、带时间戳的**
基线目录，绕开"删除旧目录"这一步。

用法：
    python scripts/run_tests.py              # 全部用例
    python scripts/run_tests.py -k backtest  # 透传给 pytest 的其它参数
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    basetemp = ROOT / "tmp" / f"pytest_{stamp}"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, "-m", "pytest", "tests", "-q", f"--basetemp={basetemp}", *sys.argv[1:]]
    print("$", " ".join(str(c) for c in cmd[1:]))
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env)
    # 尽力清理；本机权限异常时直接忽略（目录在 tmp/ 下，不进 Git）
    try:
        shutil.rmtree(basetemp)
    except OSError:
        print(f"（临时目录 {basetemp.relative_to(ROOT)} 清理失败，可忽略）")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
