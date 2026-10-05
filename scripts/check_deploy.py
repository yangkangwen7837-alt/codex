"""部署前自检：一条命令确认项目是否已准备好推送 GitHub / Streamlit Cloud。

用法：
    python scripts/check_deploy.py            # 人类可读
    python scripts/check_deploy.py --json     # 机器可读（CI 用）

检查项（第 17 节工作清单）：
    1. 入口文件存在（apps/streamlit_app.py）
    2. requirements.txt 存在且非空
    3. .gitignore 存在且忽略 secrets / data / tmp
    4. secrets.toml 未被 Git 跟踪
    5. 仓库内没有硬编码 token / 密码
    6. 代码里没有 Windows 绝对路径（C:\\ / D:\\）
    7. Git 仓库是否已初始化
    8. remote origin 是否存在
    9. 当前分支是否 main

退出码：0 = DEPLOY READY，1 = NOT READY。
本脚本只读，不修改任何业务逻辑、数据或配置。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = Path("apps") / "streamlit_app.py"

# 扫描时跳过的目录：数据/中间产物/依赖与缓存不参与“硬编码密钥”判定
SKIP_DIRS = {".git", ".venv", "venv", "env", "data", "tmp", "output",
             "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
             "node_modules", ".codex"}
SCAN_SUFFIX = {".py", ".toml", ".txt", ".md", ".yml", ".yaml", ".cfg", ".ini",
               ".json", ".bat", ".ps1", ".sh", ".example"}

# 形如 xxx = "真实的 32+ 位串" 才判为硬编码；占位空串 / 环境变量读取不算
SECRET_ASSIGN = re.compile(
    r"""(?ix)
    \b(token|api_?key|secret|secret_?key|password|passwd|pwd|access_?key|
       db_?pass|database_?url)\b
    \s*[:=]\s*
    ["'](?!\s*["'])([^"'\n]{12,})["']
    """)
SECRET_PLACEHOLDER = re.compile(r"(?i)(\$\{?[a-z_]+\}?|<[^>]+>|xxx+|your[_-]?|example|placeholder|changeme)")
WIN_PATH = re.compile(r"[A-Za-z]:[\\/](?:GPT|Users|Windows|Program|Python)")
ALLOW_WIN_PATH = ("check_deploy.py", "docs\\", "docs/", ".md")


def _run_git(*args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        return proc.returncode, (proc.stdout or proc.stderr).strip()
    except FileNotFoundError:
        return 127, "git 未安装"


def _iter_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts[:-1]):
            continue
        if path.name in SKIP_DIRS:
            continue
        if path.suffix.lower() in SCAN_SUFFIX or path.name in (".gitignore", ".dockerignore", "Dockerfile"):
            yield path


def scan_secrets() -> list[str]:
    hits = []
    for path in _iter_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for no, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            m = SECRET_ASSIGN.search(line)
            if not m:
                continue
            if SECRET_PLACEHOLDER.search(m.group(2)) or "getenv" in line or "get_secret" in line:
                continue
            hits.append(f"{path.relative_to(ROOT)}:{no} 疑似硬编码 {m.group(1)}")
    return hits


def scan_win_paths() -> list[str]:
    hits = []
    for path in _iter_files():
        rel = str(path.relative_to(ROOT))
        if any(tok in rel for tok in ALLOW_WIN_PATH):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for no, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue          # 注释不影响运行，不算部署风险
            if WIN_PATH.search(line):
                hits.append(f"{rel}:{no} Windows 绝对路径：{line.strip()[:90]}")
    return hits


def build_checks() -> list[dict]:
    checks: list[dict] = []

    def add(key, level, detail):
        checks.append({"key": key, "level": level, "detail": detail})

    # 1. 入口
    if (ROOT / ENTRY).is_file():
        add("entry", "PASS", f"入口文件存在：{ENTRY}")
    else:
        add("entry", "FAIL", f"缺少入口文件：{ENTRY}")

    # 2. requirements
    req = ROOT / "requirements.txt"
    if req.is_file() and req.read_text(encoding="utf-8", errors="replace").strip():
        add("requirements", "PASS", "requirements.txt 存在且非空")
    else:
        add("requirements", "FAIL", "requirements.txt 缺失或为空")

    # 3. .gitignore 关键规则
    gi = ROOT / ".gitignore"
    if not gi.is_file():
        add("gitignore", "FAIL", ".gitignore 缺失")
    else:
        text = gi.read_text(encoding="utf-8", errors="replace")
        missing = [k for k in (".streamlit/secrets.toml", "data/raw/", "tmp/") if k not in text]
        if missing:
            add("gitignore", "WARN", f".gitignore 存在，但缺少关键规则：{', '.join(missing)}")
        else:
            add("gitignore", "PASS", ".gitignore 已忽略 secrets / data / tmp")

    # 4. secrets 是否被 Git 跟踪
    rc, _ = _run_git("rev-parse", "--is-inside-work-tree")
    is_repo = rc == 0
    if is_repo:
        rc, tracked = _run_git("ls-files", "--", ".streamlit/secrets.toml", ".env")
        if rc == 0 and tracked.strip():
            add("secrets_tracked", "FAIL", f"敏感文件已被 Git 跟踪：{tracked.strip()}")
        else:
            add("secrets_tracked", "PASS", "secrets.toml / .env 未被 Git 跟踪")
    else:
        add("secrets_tracked", "WARN", "尚未初始化 Git，暂无法校验（初始化后再运行本检查）")

    # 5 / 6. 硬编码密钥、Windows 绝对路径
    secrets = scan_secrets()
    if secrets:
        add("hardcoded_secret", "FAIL", "疑似硬编码密钥：" + "；".join(secrets[:5]))
    else:
        add("hardcoded_secret", "PASS", "未发现硬编码 token / 密码")

    winpaths = scan_win_paths()
    if winpaths:
        add("win_path", "WARN", "存在 Windows 绝对路径（云端/容器会失效）：" + "；".join(winpaths[:5]))
    else:
        add("win_path", "PASS", "未发现会失效的 Windows 绝对路径")

    # 7 / 8 / 9. Git 状态
    if is_repo:
        add("git_repo", "PASS", "已是 Git 仓库")
        rc, branch = _run_git("rev-parse", "--abbrev-ref", "HEAD")
        if rc == 0 and branch == "main":
            add("branch", "PASS", "当前分支为 main")
        else:
            add("branch", "WARN", f"当前分支为 {branch or '未知'}，Streamlit Cloud 默认 main")
        rc, remote = _run_git("remote", "get-url", "origin")
        if rc == 0 and remote:
            add("remote", "PASS", f"remote origin：{remote}")
        else:
            add("remote", "WARN", "尚未配置 remote origin（需要 GitHub 仓库 URL）")
    else:
        add("git_repo", "FAIL", "尚未初始化 Git 仓库")
        add("branch", "WARN", "未初始化 Git，跳过分支检查")
        add("remote", "WARN", "未初始化 Git，跳过 remote 检查")

    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="部署前自检")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    checks = build_checks()
    fails = [c for c in checks if c["level"] == "FAIL"]
    warns = [c for c in checks if c["level"] == "WARN"]
    ready = not fails

    if args.json:
        print(json.dumps({"ready": ready, "checks": checks}, ensure_ascii=False, indent=2))
        return 0 if ready else 1

    labels = {
        "entry": "入口文件", "requirements": "依赖清单", "gitignore": ".gitignore",
        "secrets_tracked": "secrets 是否入库", "hardcoded_secret": "硬编码密钥扫描",
        "win_path": "Windows 绝对路径", "git_repo": "Git 仓库", "branch": "当前分支",
        "remote": "GitHub remote",
    }
    print("=" * 62)
    print("部署前自检 · check_deploy")
    print("=" * 62)
    for c in checks:
        print(f"[{c['level']:<4}] {labels.get(c['key'], c['key'])}：{c['detail']}")
    print("-" * 62)
    if fails:
        print(f"结论：NOT READY（{len(fails)} 项阻断，{len(warns)} 项提醒）")
    elif warns:
        print(f"结论：DEPLOY READY（{len(warns)} 项提醒，非阻断）")
    else:
        print("结论：DEPLOY READY")
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
