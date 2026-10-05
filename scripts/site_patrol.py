"""网站与运行环境巡检。

检查五件事，任一异常都会在报告里标红并以非零退出码结束：
  1. 网站存活（HTTP /_stcore/health），掉线可自动重启
  2. 沙箱环境是否正常（应用沙箱初始化失败会让 PowerShell 与内置浏览器一起不可用）
  3. 工作区根目录属主是否正确（属主不是当前用户时，沙箱无法写 ACE → setup refresh 失败）
  4. 数据新鲜度（本地最新交易日 vs 今天）
  5. 最近一次数据更新的状态

用法：
    python scripts/site_patrol.py                 # 巡检并输出报告
    python scripts/site_patrol.py --restart       # 网站掉线时自动重启
    python scripts/site_patrol.py --json          # 只打印 JSON（供自动化判断）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urljoin
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bigfish import OUTPUT_DIR, PROCESSED_DIR  # noqa: E402
from bigfish.config import load_settings  # noqa: E402
from bigfish.storage import latest_local_trade_date  # noqa: E402

STATUS_FILE = PROCESSED_DIR / "patrol_status.json"
REPORT_FILE = OUTPUT_DIR / "patrol_report.md"
SANDBOX_DIR = Path.home() / ".codex" / ".sandbox"


def _now() -> datetime:
    return datetime.now().astimezone()


def check_site(port: int, timeout: int = 6) -> dict:
    url = f"http://localhost:{port}/_stcore/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode(errors="replace").strip()
            ok = response.status == 200 and body.lower().startswith("ok")
            detail = f"HTTP {response.status} {body[:20]}"
            # 再验证一次前端资源，确认不是"服务活着但页面白屏"
            try:
                html = urllib.request.urlopen(f"http://localhost:{port}/", timeout=timeout).read().decode("utf-8", "replace")
                assets = re.findall(r'(?:src|href)="(\./[^"]+\.js)"', html)
                if assets:
                    probe = urljoin(f"http://localhost:{port}/", assets[0])
                    with urllib.request.urlopen(probe, timeout=timeout) as r2:
                        ctype = r2.headers.get("Content-Type", "")
                        if r2.status != 200 or "javascript" not in ctype:
                            return {"ok": False, "detail": f"前端资源异常：{assets[0]} → {r2.status} {ctype}", "url": url}
                        detail += f"；前端资源 {len(assets)} 个可访问"
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "detail": f"{detail}；但前端资源校验失败：{type(exc).__name__}", "url": url}
            return {"ok": ok, "detail": detail, "url": url}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(exc).__name__}: {str(exc)[:80]}", "url": url}


def restart_site(port: int) -> dict:
    """以脱离父进程的方式重启网站（否则父 shell 退出会连带杀掉它）。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    log = OUTPUT_DIR / "site.log"
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"\n=== {_now().isoformat(timespec='seconds')} 由巡检重启 ===\n")
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "start_site.py"), "--port", str(port)],
            cwd=str(ROOT), env=env, stdout=fh, stderr=subprocess.STDOUT, close_fds=True,
            creationflags=flags,
        )
    for _ in range(20):
        time.sleep(1.5)
        if check_site(port).get("ok"):
            return {"restarted": True, "pid": proc.pid, "log": str(log)}
    return {"restarted": False, "pid": proc.pid, "log": str(log), "detail": "重启后仍未通过健康检查"}


def check_sandbox() -> dict:
    """沙箱初始化是否正常（失败会让 PowerShell 与内置浏览器一起不可用）。"""
    error_file = SANDBOX_DIR / "setup_error.json"
    if not error_file.exists():
        return {"ok": True, "detail": "没有 setup 错误记录"}
    age = time.time() - error_file.stat().st_mtime
    try:
        payload = json.loads(error_file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        payload = {}
    fresh = age < 1800
    return {
        "ok": not fresh,
        "detail": (f"{payload.get('message', 'setup 错误')}（{age / 60:.0f} 分钟前）"
                   + ("；沙箱启动会失败，内置浏览器/PowerShell 不可用" if fresh else "（已过期，不影响）")),
        "error_file": str(error_file),
        "age_minutes": round(age / 60, 1),
    }


def check_workspace_acl() -> dict:
    """工作区目录的 ACL 是否完好（沙箱写 ACE 失败 → 属主很可能不是当前用户）。

    直接从沙箱日志里解析 "write ACE grant failed on <path>"，
    这样不必依赖 dir /q 那种会被截断的属主显示。
    """
    try:
        logs = sorted(SANDBOX_DIR.glob("sandbox.*.log"))
        if not logs:
            return {"ok": True, "detail": "没有沙箱日志（跳过）"}
        text = logs[-1].read_text(encoding="utf-8", errors="replace")
        key = "write ACE grant failed on "
        idx = text.rfind(key)
        if idx < 0:
            return {"ok": True, "detail": "沙箱日志里没有 ACE 写入失败记录"}
        line = text[idx + len(key):].splitlines()[0]
        path = line.split(": SetNamedSecurityInfoW")[0].strip()
        broken = os.path.normcase(path) == os.path.normcase(str(ROOT))
        if not broken:
            return {"ok": True, "detail": f"ACE 写入失败发生在其它路径：{path}"}
        return {
            "ok": False,
            "path": path,
            "detail": (f"{path} 的 ACL 无法被沙箱修改（SetNamedSecurityInfoW 失败 5）——"
                       "通常是该目录属主不是当前用户。修复（需管理员 PowerShell）："
                       f'takeown /f "{path}" /r /d y && icacls "{path}" /reset /t /c'),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": True, "detail": f"ACL 检查失败（跳过）：{type(exc).__name__}"}


def check_freshness(settings) -> dict:
    latest = latest_local_trade_date()
    today = _now().strftime("%Y%m%d")
    if not latest:
        return {"ok": False, "detail": "本地没有任何行情分片", "latest": None}
    gap = (datetime.strptime(today, "%Y%m%d") - datetime.strptime(latest, "%Y%m%d")).days
    # 允许长假（国庆/春节）带来的 10 天间隔
    return {"ok": gap <= 10, "latest": latest, "gap_days": gap,
            "detail": f"本地最新交易日 {latest}（距今天 {gap} 天）"}


def check_last_update() -> dict:
    path = PROCESSED_DIR / "update_status.json"
    if not path.exists():
        return {"ok": False, "detail": "还没有任何更新记录"}
    payload = json.loads(path.read_text(encoding="utf-8"))
    state = payload.get("state")
    return {
        "ok": state != "failed",
        "state": state,
        "detail": f"{payload.get('message', '')}（{payload.get('finished_at') or payload.get('started_at')}）",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--restart", action="store_true", help="网站掉线时自动重启")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    args = parser.parse_args()

    settings = load_settings()
    port = int(settings.path("site.port", 8501))
    checks = {
        "site": check_site(port),
        "sandbox": check_sandbox(),
        "workspace_acl": check_workspace_acl(),
        "data_freshness": check_freshness(settings),
        "last_update": check_last_update(),
    }
    restart = {}
    if not checks["site"]["ok"] and args.restart:
        restart = restart_site(port)
        checks["site_restart"] = restart
        checks["site"] = check_site(port)

    problems = [k for k, v in checks.items() if isinstance(v, dict) and v.get("ok") is False]
    status = {
        "checked_at": _now().isoformat(timespec="seconds"),
        "ok": not problems,
        "problems": problems,
        "checks": checks,
    }
    STATUS_FILE.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"# 巡检报告 · {status['checked_at']}", "",
             f"结论：{'全部正常' if status['ok'] else '发现 ' + str(len(problems)) + ' 个问题：' + '、'.join(problems)}", ""]
    lines.append("| 检查项 | 结果 | 说明 |")
    lines.append("|---|---|---|")
    labels = {"site": "网站存活", "sandbox": "沙箱环境", "workspace_acl": "目录 ACL",
              "data_freshness": "数据新鲜度", "last_update": "最近更新", "site_restart": "网站重启"}
    for key, value in checks.items():
        mark = "OK" if value.get("ok", True) else "**异常**"
        lines.append(f"| {labels.get(key, key)} | {mark} | {value.get('detail', '')} |")
    lines.append("")
    lines.append("> 巡检项含义：沙箱环境异常会让 PowerShell 与内置浏览器一起不可用；"
                 "目录属主不是当前用户时，沙箱写入 ACE 会失败并触发上面的问题。")
    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(status, ensure_ascii=False))
    else:
        print(f"巡检完成：{'正常' if status['ok'] else '发现问题 → ' + '、'.join(problems)}")
        for key, value in checks.items():
            print(f"  [{'OK' if value.get('ok', True) else '!!'}] {labels.get(key, key)}: {value.get('detail', '')}")
        print(f"报告：{REPORT_FILE}")
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
