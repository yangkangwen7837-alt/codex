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
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = Path("data") / "snapshot"
SAFE = ["-c", f"safe.directory={ROOT.as_posix()}"]


def _git_exe() -> str:
    """定位 git 可执行文件。

    本机没有单独安装 Git，能用的 git 来自 Codex 运行时（只在 Codex 会话的 PATH 里），
    而 Windows 计划任务以普通身份运行、看不到那条 PATH —— 所以这里显式找出绝对路径，
    避免"跑批成功但发布失败"。
    """
    override = os.environ.get("BIGFISH_GIT")
    if override and Path(override).exists():
        return override
    found = shutil.which("git")
    if found:
        return found
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "cmd" / "git.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Git" / "cmd" / "git.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git" / "cmd" / "git.exe",
    ]
    cache = Path(os.environ.get("USERPROFILE", "~")).expanduser() / ".cache" / "codex-runtimes"
    candidates += sorted(cache.glob("*/dependencies/native/git/cmd/git.exe"))
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return "git"


GIT = _git_exe()


def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"      # 无人值守：不要停在账号输入
    env["GIT_ASKPASS"] = "echo"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        return subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env, timeout=timeout)
    except FileNotFoundError as exc:
        # 让调用方拿到一个"可读的失败结果"，而不是直接抛异常
        return subprocess.CompletedProcess(cmd, 127, "", f"找不到可执行文件：{exc.filename or cmd[0]}")


def git(*args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return _run([GIT, *SAFE, *args], timeout=timeout)


def _proxy_url() -> str:
    """本机代理（直连 github 不通时使用）。可用 BIGFISH_GIT_PROXY 覆盖，设为 none 表示不用。"""
    override = os.environ.get("BIGFISH_GIT_PROXY")
    if override:
        return "" if override.lower() in ("none", "off", "0") else override
    for candidate in ("http://127.0.0.1:7890", "http://127.0.0.1:7897", "http://127.0.0.1:10809"):
        host, port = candidate.split("//")[1].split(":")
        try:
            with socket.create_connection((host, int(port)), timeout=1):
                return candidate
        except OSError:
            continue
    return ""


def git_push(timeout: int = 600) -> subprocess.CompletedProcess:
    """推送；直连失败（被墙/DNS/超时）时自动改走本机代理重试一次。"""
    first = git("push", "origin", "main", timeout=timeout)
    if first.returncode == 0:
        return first
    text = ((first.stdout or "") + (first.stderr or "")).lower()
    if not any(key in text for key in ("could not connect", "timed out", "connection was reset",
                                       "failed to connect", "unable to access")):
        return first
    proxy = _proxy_url()
    if not proxy:
        return first
    print(f"直连 github 失败，改走本机代理 {proxy} 重试…")
    return _run([GIT, *SAFE, "-c", f"http.proxy={proxy}", "-c", f"https.proxy={proxy}",
                 "push", "origin", "main"], timeout=timeout)


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

    pushed = git_push()
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
