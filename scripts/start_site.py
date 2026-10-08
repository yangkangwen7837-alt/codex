"""启动网站（看板 + 进程内自动更新调度器）。

用法：
    python scripts/start_site.py                 # 端口取 configs/default.yaml 的 site.port（8510）
    python scripts/start_site.py --port 8510
    python scripts/start_site.py --no-scheduler  # 只起网站，不自动更新
    python scripts/start_site.py --log-to-file   # 输出追加到 output/site.log（由看护脚本使用）
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bigfish.config import load_settings  # noqa: E402
from bigfish import OUTPUT_DIR  # noqa: E402
from bigfish.update import scheduler_status, start_scheduler  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-scheduler", action="store_true")
    parser.add_argument("--log-to-file", action="store_true")
    args = parser.parse_args()

    if args.log_to_file:
        # 无人值守场景下没有终端，把输出接到 output/site.log（子进程会继承这个句柄）
        from datetime import datetime  # noqa: PLC0415

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        handle = open(OUTPUT_DIR / "site.log", "a", encoding="utf-8", buffering=1)
        handle.write(f"\n=== {datetime.now().astimezone().isoformat(timespec='seconds')} 启动 ===\n")
        sys.stdout = handle
        sys.stderr = handle

    settings = load_settings()
    port = args.port or int(settings.path("site.port", 8510))
    env = dict(os.environ)
    # 调度器在启动进程里跑：这样不开浏览器也会按时更新；网站进程内不再重复起
    env["BIGFISH_DISABLE_SCHEDULER"] = "1"
    env.setdefault("PYTHONIOENCODING", "utf-8")

    app = ROOT / "apps" / "streamlit_app.py"
    cmd = [sys.executable, "-m", "streamlit", "run", str(app),
           "--server.port", str(port), "--server.address", "localhost"]
    print(f"启动网站：http://localhost:{port}")
    if args.no_scheduler:
        print("自动更新：已关闭")
    else:
        scheduler = start_scheduler()
        status = scheduler_status()
        if scheduler is None:
            print("自动更新：启动失败（APScheduler 不可用）")
        else:
            print("自动更新：已开启")
            for job in status.get("jobs", []):
                print(f"  - {job['name']}：下次执行 {job['next_run']}")
    subprocess.run(cmd, cwd=str(ROOT), env=env)


if __name__ == "__main__":
    main()
