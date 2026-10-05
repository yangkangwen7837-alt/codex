"""页面渲染自检（无头，不写数据）：四种模式下逐一渲染 8 个页面 + 入口。

用法：
    python scripts/check_pages.py              # 正常数据（本地）
    python scripts/check_pages.py --cloud      # 模拟 Streamlit Cloud 只读模式
    python scripts/check_pages.py --snapshot   # 模拟云端仓库：无实时数据 → 回退读 data/snapshot
    python scripts/check_pages.py --coldstart  # 数据全空（连快照也没有）
    python scripts/check_pages.py --all        # 四种模式全跑（每个模式独立进程，互不污染）

退出码：0 = 全部通过，1 = 有页面报错。本脚本只读，不修改业务数据。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = ["radar", "watchlist", "early", "ponds", "matrix", "fundamental", "kill", "ops"]
MODES = ("normal", "cloud", "snapshot", "coldstart")
CHILD_FLAG = "BIGFISH_CHECK_CHILD"


def _prepare(mode: str) -> str:
    """返回该模式的说明文字。"""
    sys.path.insert(0, str(ROOT / "apps"))
    sys.path.insert(0, str(ROOT / "src"))
    if mode == "cloud":
        os.environ["BIGFISH_FORCE_CLOUD"] = "1"
        return "云端只读模式（BIGFISH_FORCE_CLOUD=1）"
    if mode in ("snapshot", "coldstart"):
        empty = Path(tempfile.mkdtemp(prefix="bigfish_empty_"))
        import bigfish  # noqa: PLC0415

        # 必须在 import common 之前改：common 在导入时决定读实时目录还是快照
        bigfish.PROCESSED_DIR = empty / "processed"
        bigfish.OUTPUT_DIR = empty / "output"
        import common as C                       # noqa: PLC0415
        from bigfish import update as U           # noqa: PLC0415
        from bigfish.update import scheduler as S  # noqa: PLC0415

        U.service.PROCESSED_DIR = empty
        U.service.STATUS_FILE = empty / "update_status.json"
        U.service.HISTORY_FILE = empty / "update_history.json"
        U.service.SETTINGS_FILE = empty / "update_settings.json"
        U.service.LOCK_FILE = empty / "update.lock"
        S.HEARTBEAT_FILE = empty / "scheduler_heartbeat.json"

        if mode == "coldstart":
            # 连快照也不给：模拟"什么都没有"的最差情况
            C.SNAPSHOT_DIR = empty / "snapshot"
            C.PROCESSED_DIR = empty / "processed"
            C.OUTPUT_DIR = empty / "output"
            C.USING_SNAPSHOT = False
            return "空数据冷启动（无实时数据、也无快照）"
        if not C.USING_SNAPSHOT:
            return "云端仓库模式（未回退到快照，请检查 data/snapshot）"
        return f"云端仓库模式（实时目录为空 → 回退快照 {C.PROCESSED_DIR.relative_to(ROOT)}）"
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

    if mode == "snapshot":
        import common as C  # noqa: PLC0415

        rows = len(C.scores())
        if C.USING_SNAPSHOT and rows:
            print(f"  [OK]   快照回退生效：读到的评分 {rows} 行（{C.PROCESSED_DIR}）")
        else:
            failed.append("snapshot")
            print(f"  [FAIL] 快照回退未生效：USING_SNAPSHOT={C.USING_SNAPSHOT} 行数={rows}")

    print(f"  通过 {len(targets) - len(failed)} / {len(targets)}\n")
    return len(failed)


def run_child(mode: str) -> int:
    """子进程入口：只跑单一模式。"""
    return 1 if run_mode(mode) else 0


def run_modes_in_subprocess(modes: list[str]) -> int:
    """每个模式独立进程，避免 sys.modules 与缓存互相污染。"""
    failures = 0
    for mode in modes:
        env = dict(os.environ)
        env[CHILD_FLAG] = mode
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), f"--{mode}"],
                              cwd=str(ROOT), env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        for line in (proc.stdout or "").splitlines():
            if "ScriptRunContext" in line or "MemoryCacheStorageManager" in line:
                continue
            print(line)
        if proc.returncode != 0:
            failures += 1
            print(f"  （{mode} 模式退出码 {proc.returncode}）")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="页面渲染自检")
    parser.add_argument("--normal", action="store_true", help="正常数据（本地）")
    parser.add_argument("--cloud", action="store_true")
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--coldstart", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    child = os.environ.get(CHILD_FLAG)
    if child in MODES:
        return run_child(child)

    if args.all:
        modes = list(MODES)
    elif args.cloud:
        modes = ["cloud"]
    elif args.snapshot:
        modes = ["snapshot"]
    elif args.coldstart:
        modes = ["coldstart"]
    else:
        modes = ["normal"]

    failures = run_modes_in_subprocess(modes)
    print("全部模式通过" if failures == 0 else f"存在 {failures} 个失败项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
