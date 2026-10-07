"""对着任意一个 Streamlit 地址做上线健康检查（本地或 Streamlit Cloud）。

检查内容：
    1. 首页 / 返回 200 且是 HTML
    2. /_stcore/health 返回 200 ok（Streamlit 自己的健康端点）
    3. 首页引用的静态资源（JS/CSS）逐个可取、Content-Type 正确
    4. /_stcore/host-config 可访问（前端握手需要的配置）
    5. 页面标题里能看到 Big Fish 字样（确认部署的是本项目的入口）

用法：
    python scripts/check_live_site.py                       # 默认检查本地 localhost:8501
    python scripts/check_live_site.py https://xxx.streamlit.app
    python scripts/check_live_site.py https://xxx.streamlit.app --json

退出码：0 = 全部通过，1 = 有问题。本脚本只发 GET 请求，不修改任何东西。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import urljoin

UA = {"User-Agent": "bigfish-site-check/1.0"}


def fetch(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), resp.read()


def check(base: str, timeout: int = 20) -> dict:
    base = base.rstrip("/") + "/"
    result: dict = {"base": base, "checks": [], "ok": True}

    def add(name: str, ok: bool, detail: str, required: bool = True) -> None:
        result["checks"].append({"name": name, "ok": ok, "detail": detail,
                                 "required": required})
        if required:
            result["ok"] = result["ok"] and ok

    # 1. 首页
    try:
        status, ctype, body = fetch(base, timeout)
        html = body.decode("utf-8", "replace")
        add("首页", status == 200 and "html" in ctype.lower(), f"HTTP {status} {ctype} {len(body)}B")
    except Exception as exc:  # noqa: BLE001
        add("首页", False, f"{type(exc).__name__}: {str(exc)[:120]}")
        result["html"] = ""
        return result
    result["html"] = html

    # 2. 健康端点
    try:
        status, _, body = fetch(urljoin(base, "_stcore/health"), timeout)
        text = body.decode("utf-8", "replace").strip().lower()
        add("健康端点", status == 200 and text.startswith("ok"), f"HTTP {status} {text[:20]}")
    except Exception as exc:  # noqa: BLE001
        add("健康端点", False, f"{type(exc).__name__}: {str(exc)[:120]}")

    # 3. 静态资源
    assets = re.findall(r'(?:src|href)="([^"]+\.(?:js|css))"', html)
    bad = []
    for asset in assets:
        try:
            status, ctype, data = fetch(urljoin(base, asset), timeout)
            if status != 200 or not ("javascript" in ctype or "css" in ctype):
                bad.append(f"{asset} → {status} {ctype}")
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{asset} → {type(exc).__name__}")
    add("静态资源", bool(assets) and not bad,
        f"{len(assets) - len(bad)}/{len(assets)} 可取" + (f"，异常：{bad[:3]}" if bad else ""))

    # 4. 前端握手配置
    try:
        status, ctype, body = fetch(urljoin(base, "_stcore/host-config"), timeout)
        add("前端握手配置", status == 200 and b"{" in body, f"HTTP {status} {ctype}")
    except Exception as exc:  # noqa: BLE001
        add("前端握手配置", False, f"{type(exc).__name__}: {str(exc)[:120]}")

    # 5. 标题（信息项：首页 HTML 只是外壳，标题由前端渲染后再设置，因此不作为通过条件）
    title = ""
    match = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    if match:
        title = match.group(1).strip()
    add("页面标题", "big fish" in title.lower() or "基本面" in title,
        f"<title>{title or '（无）'}</title>（外壳 HTML，仅供参考）", required=False)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="线上站点健康检查")
    parser.add_argument("url", nargs="?", default=None,
                        help="站点地址，默认读取 configs/default.yaml 的 site.port（当前 8510）")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    url = args.url
    if not url:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
            from bigfish.config import load_settings  # noqa: PLC0415

            url = f"http://localhost:{int(load_settings().path('site.port', 8510))}"
        except Exception:  # noqa: BLE001
            url = "http://localhost:8510"
    result = check(url, args.timeout)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    print(f"检查站点：{result['base']}")
    for item in result["checks"]:
        mark = "OK" if item["ok"] else ("!!" if item.get("required", True) else "i ")
        print(f"  [{mark}] {item['name']}：{item['detail']}")
    print()
    print("结论：全部通过" if result["ok"] else "结论：存在问题（见上面标记 !! 的项）")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
