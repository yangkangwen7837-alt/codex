# TEST REPORT · 基本面反转 Big Fish Agent

> 测试日期：2026-10-05 ｜ 环境：Windows / Python 3.14.7 ｜ 只读验证，**未改动任何业务逻辑或数据**

---

## 1. 测试项与结果

| # | 测试 | 方法 | 结果 |
|---|---|---|---|
| T1 | 语法与导入 | 对新增/修改文件做 `py_compile` | ✅ 通过 |
| T2 | 单元测试 | `python -m pytest tests -q --basetemp=tmp/pytest_run` | ✅ **22 passed** |
| T3 | 页面无头渲染 | `python scripts/check_pages.py`（Streamlit `AppTest` 渲染 8 个页面 + 入口） | ✅ **9 / 9 通过**，无异常 |
| T4 | 云端只读模式 | `python scripts/check_pages.py --cloud` | ✅ **9 / 9 通过**；「立即更新」按钮不渲染；`run_update()` 返回 `blocked` |
| T5 | 冷启动（无数据） | `python scripts/check_pages.py --coldstart` | ✅ **9 / 9 通过**，只显示「暂无数据」提示，无异常 |
| T6 | 密钥扫描 | 全仓库正则扫描 token / api_key / secret / password / access_key / database_url | ✅ 无硬编码密钥（唯一命中为 `secrets.toml.example` 的空占位与 `get_secret()` 调用） |
| T7 | 绝对路径扫描 | 扫描 `C:\` / `D:\` / `/Users/` / `/home/` / `/Desktop/` / `/Documents/` | ✅ 业务代码无绝对路径（仅文档与 Dockerfile 注释含示例路径） |
| T8 | 部署前自检 | `python scripts/check_deploy.py` | 见第 3 节 |

---

## 2. 测试证据（摘要）

**T2 单元测试**

```
......................                                                   [100%]
22 passed in 2.06s
```

> 直接跑 `python -m pytest tests -q` 时，2 个用到 `tmp_path` 的用例会因沙箱无法写入
> 系统临时目录（`%TEMP%\pytest-of-*`，WinError 5）而报错；指定仓库内基线目录
> `--basetemp=tmp/pytest_run` 后 22 项全通过。这是沙箱权限问题，不是代码问题。

**T3 / T4 / T5 页面渲染（AppTest，`scripts/check_pages.py --all`）**

```
T3 正常数据：  radar OK / watchlist OK / early OK / ponds OK / matrix OK /
               fundamental OK / kill OK / ops OK / streamlit_app OK      → 9/9
T4 云端只读：  同上 9/9；ops 页面按钮列表 = []（不渲染「立即更新」）
               C.run_update() → {'state': 'blocked', 'message': 'Streamlit Cloud 为只读部署…'}
T5 冷启动：    同上 9/9（数据目录为空时全部友好降级）
```

各页面渲染到的元素（正常数据下，由 `tmp/test_pages.py` 统计）：

| 页面 | 表格数 | 指标数 |
|---|---:|---:|
| 雷达总览 radar | 3 | 5 |
| 观察池 watchlist | 1 | 0 |
| 早期线索 early | 1 | 3 |
| 行业鱼塘 ponds | 1 | 0 |
| FRS×PCS 矩阵 matrix | 2 | 0 |
| 基本面趋势 fundamental | 1 | 0 |
| Kill 监控 kill | 1 | 3 |
| 运行与维护 ops | 2 | 11 |

---

## 3. 部署前自检（`scripts/check_deploy.py`）

| 检查项 | 结果 |
|---|---|
| 入口文件 `apps/streamlit_app.py` | PASS |
| `requirements.txt` 存在且非空 | PASS |
| `.gitignore` 已忽略 secrets / data / tmp | PASS |
| secrets 未被 Git 跟踪 | PASS |
| 硬编码密钥扫描 | PASS（未发现） |
| Windows 绝对路径 | PASS（未发现） |
| Git 仓库 | PASS |
| 当前分支 | PASS（`main`） |
| GitHub remote origin | 见最终状态（缺仓库 URL 时为 WARN） |

---

## 4. 已知限制与未覆盖项

* **未做真实网络抓数验证**：本轮是部署加固，不触发 Tushare 抓数（避免消耗配额、改写数据）。
* **未做浏览器端交互测试**：使用 Streamlit 官方 `AppTest` 无头渲染，覆盖异常与渲染分支；
  点击级交互（下载按钮、多选联动）未逐一自动化。
* **未在真实 Streamlit Cloud 环境跑过**：需要 GitHub 仓库 URL 才能创建 App；本地已用
  `BIGFISH_FORCE_CLOUD=1` 模拟云端只读行为。
* **Docker 镜像未实际构建**：`Dockerfile` 只做静态审查（云端部署不需要它）。
* **项目根目录 ACL 异常**（所有者非当前用户）影响的是 Codex 沙箱/浏览器，不是部署本身，
  修复方式见 [docs/14_troubleshooting.md](docs/14_troubleshooting.md)。

---

## 5. 复现命令

```bash
python -m pytest tests -q --basetemp=tmp/pytest_run
python scripts/check_pages.py --all   # 正常 / 云端只读 / 空数据冷启动，三模式全跑
python scripts/check_deploy.py        # 部署前自检
```
