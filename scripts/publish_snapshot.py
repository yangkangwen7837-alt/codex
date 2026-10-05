"""把最新一期快照发布到 GitHub（导出 → 提交 → 推送），供每日自动化调用。

一条命令做完三件事：
    1. python scripts/export_snapshot.py   （本地有跑批产物时；没有就跳过并提示）
    2. git add data/snapshot               （只暂存快照目录，绝不动其它文件）
    3. git commit + git push origin main   （没有变化时直接跳过，不产生空提交）

设计要点：
    * 只碰 ``data/snapshot/`` 这一个目录，不会把 data/raw、data/processed 带进 Git；
    * 自动带上 ``-c safe.directory=<仓库>``：本机沙箱进程创建的 .git 属主可能与当前用户
      不一致，git 会报 "dubious ownership"，这里自愈处理，不写全局配置；
    * 非交互（``GIT_TERMINAL_PROMPT=0``），无人值守时不会卡在账号输入上。

用法：
    python scripts/publish_snapshot.py               # 导出并发布
    python scripts/publish_snapshot.py --no-export   # 跳过导出，直接发布现有快照
    python scripts/publish_snapshot.py --no-push     # 只提交不推送（调试用）

退出码：0 = 成功（含"没有变化"），1 = 失败。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = Path("data") / "snapshot"
SAFE = ["-c", f"safe.directory={ROOT.as_posix()}"]


def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"      # 无人值守：不要停在账号输入
    env["GIT_ASKPASS"] = "echo"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=timeout)


def git(*args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return _run(["git", *SAFE, *args], timeout=timeout)


def step_export() -> int:
    if not list((ROOT / "data" / "processed").glob("*.parquet")):
        print("跳过导出：本地没有 data/processed 产物，沿用仓库里已有的快照。")
        return 0
    proc = _run([sys.executable, str(ROOT / "scripts" / "export_snapshot.py")])
    print((proc.stdout or "").strip()[-1500:])
    if proc.returncode != 0:
        print((proc.stderr or "").strip()[-800:])
        print("导出快照失败。")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="发布最新一期数据快照到 GitHub")
    parser.add_argument("--no-export", action="store_true", help="跳过导出，直接发布现有快照")
    parser.add_argument("--no-push", action="store_true", help="只提交，不推送")
    args = parser.parse_args()

    if not args.no_export and step_export():
        return 1

    if not (ROOT / SNAPSHOT).is_dir():
        print(f"找不到快照目录 {SNAPSHOT}，先跑一次 scripts/export_snapshot.py。")
        return 1

    added = git("add", "--", SNAPSHOT.as_posix())
    if added.returncode != 0:
        print("git add 失败：", (added.stderr or added.stdout).strip()[:400])
        return 1

    staged = git("diff", "--cached", "--name-only", "--", SNAPSHOT.as_posix())
    changed = [line for line in (staged.stdout or "").splitlines() if line.strip()]
    if not changed:
        print("快照没有变化（与上一期一致），无需提交。")
        return 0
    print(f"快照有 {len(changed)} 个文件变化：")
    for name in changed[:20]:
        print("   ", name)

    date = ""
    summary = ROOT / SNAPSHOT / "processed" / "run_summary.json"
    if summary.exists():
        import json  # noqa: PLC0415

        date = str(json.loads(summary.read_text(encoding="utf-8")).get("as_of") or "")
    message = f"Update data snapshot{f' {date}' if date else ''}"
    committed = git("commit", "-m", message, "--", SNAPSHOT.as_posix())
    if committed.returncode != 0:
        print("git commit 失败：", (committed.stderr or committed.stdout).strip()[:400])
        return 1
    print("已提交：", message)

    if args.no_push:
        print("（--no-push：未推送）")
        return 0

    pushed = git("push", "origin", "main", timeout=600)
    if pushed.returncode != 0:
        text = ((pushed.stdout or "") + (pushed.stderr or "")).strip()
        print("git push 失败：", text[:500])
        if "rejected" in text or "non-fast-forward" in text:
            print("提示：远端有新提交，先 git pull --rebase 再推送。")
        return 1
    print("已推送到 origin/main。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
