# DEPLOYMENT AUDIT · 基本面反转 Big Fish Agent

> 审计日期：2026-10-05 ｜ 范围：**只做部署加固，不改业务逻辑**
> 本文件不包含任何密钥明文。所有密钥只从 `st.secrets` / 环境变量读取。

---

## 1. 当前状态

| 项目 | 结论 |
|---|---|
| 项目根目录 | `D:\GPT\基本面反转交易策略` |
| Streamlit 入口文件 | **`apps/streamlit_app.py`**（仓库内不存在 `app.py` / `main.py`，按规则 1→2→3 回退到 `st.set_page_config()` 定位） |
| 页面 | `apps/app_pages/` 下 8 个页面 + 入口导航，全部经共享数据层 `apps/common.py` 取数 |
| Python 版本 | 本地 3.14.7（`C:\Python314\python.exe`，无 venv）；Cloud 建议/默认为 3.11~3.12 |
| 包管理 | `requirements.txt`（无 `pyproject.toml`） |
| 已安装关键版本 | streamlit 1.64.0 / pandas 3.0.5 / numpy 2.5.2 / pyarrow 25.0.1 / PyYAML 6.0.3 / tushare 1.4.29 / APScheduler 3.11.3 |
| 代码规模 | 业务代码 67 个 `.py`（不含 `data/`、`tmp/`） |
| `.streamlit/config.toml` | 存在；`headless = true` + 主题；**未强绑定 8501 端口**（不影响 Cloud） |
| `.streamlit/secrets.toml` | 不存在（正确）；已提供 `secrets.toml.example` 模板 |
| 本地运行 | `python scripts/start_site.py` → http://localhost:8501，可正常渲染（见 `TEST_REPORT.md`） |
| Docker | 已新增 `Dockerfile` + `.dockerignore`（腾讯云迁移备用，不影响 Community Cloud） |
| 数据体量 | `data/raw` ≈ 2.3 GB、`data/processed` ≈ 0.66 GB → **必须留在本地，不进 Git** |

### 依赖清单核对

对全仓库 `import` / `from ... import` 做静态扫描后，第三方依赖只保留实际用到的：

| 包 | 用途 |
|---|---|
| `streamlit` | 看板框架 |
| `pandas` / `numpy` / `pyarrow` | 数据处理与 parquet 读写（pyarrow 是 pandas parquet 引擎，不直接 import 但必需） |
| `PyYAML` | 读取 `configs/*.yaml` |
| `tushare` | 数据抓取适配器 |
| `APScheduler` | 本地/常驻进程的定时更新（Cloud 上自动关闭） |

**未引入**：plotly / altair / sqlalchemy / psycopg2 / requests（图表全部用 Streamlit 原生
`st.line_chart` / `st.bar_chart` / `st.scatter_chart`，抓数走 tushare SDK）。
版本用 `>=` 而非 `==`：本地是 3.14、Cloud 是 3.11/3.12，硬钉版本会在云端装不上。

---

## 2. 缺失项（本轮已补齐）

| 项 | 处理 |
|---|---|
| `.gitignore` | ✅ 新增，已忽略 secrets / 虚拟环境 / 日志 / `data/raw`、`data/processed` / `tmp/` / `output/*` |
| `.streamlit/secrets.toml.example` | ✅ 新增（TUSHARE_TOKEN + 预留 PushPlus/Telegram/OpenAI + `[database]` 段） |
| `requirements.txt` | ✅ 重写为最小必要集 |
| `requirements-dev.txt` | ✅ 新增（pytest，Cloud 不需要） |
| `Dockerfile` / `.dockerignore` | ✅ 新增（python:3.11-slim、`WORKDIR /app`、healthcheck、`--server.address=0.0.0.0`） |
| `scripts/check_deploy.py` | ✅ 新增部署前自检 |
| `scripts/check_pages.py` | ✅ 新增页面渲染自检（正常 / 云端只读 / 快照回退 / 空数据 四种模式） |
| `scripts/export_snapshot.py` + `data/snapshot/` | ✅ 新增"最新一期"数据快照（19 个文件 / 4.49 MB），让云端页面不至于空白 |
| `scripts/run_tests.py` | ✅ 新增测试包装（时间戳临时目录，规避本机 tmp_path 权限问题） |
| README 部署章节 | ✅ 新增 Main file path、Local Run、Secrets、Deploy to Streamlit Cloud |
| Git 仓库 | ✅ 已 `git init` + `git branch -M main` + 首次 commit |
| GitHub remote | ⏳ **缺 GitHub repository URL**（见第 5 节） |

---

## 3. 风险项与处置

| # | 风险 | 影响 | 处置 |
|---|---|---|---|
| R1 | `data/raw` + `data/processed` 合计 ≈ 2.9 GB，`data/processed` 里是评分/回测面板（含 `bigfish_score_latest.parquet`） | 若误提交，GitHub 会拒绝或仓库膨胀到不可用 | `.gitignore` 排除，且 `check_deploy.py` 会校验；**未删除任何数据** |
| R2 | 云端文件系统只读 | 「立即更新」按钮、APScheduler 定时任务在 Cloud 上会报错/无效 | 新增 `is_streamlit_cloud()`，Cloud 上关闭调度器；`run_update()` 返回友好提示；页面按钮与开关置灰并给出说明 |
| R3 | 首次部署时 `data/processed` 为空（数据不进 Git） | 页面白屏/空白 | 已解决：`apps/common.py` 在实时目录没有 parquet 时**自动回退读取 `data/snapshot/`**（随仓库发布的最新一期，4.49 MB）。快照回退与全空两种极端情况都已验证（见 `TEST_REPORT.md`） |
| R3b | 快照进入**公开**仓库（用户选择方案 B） | 最近一期的评分与榜单公开可见 | 已如实标注在 README：需要不公开时把仓库改 private，或删除 `data/snapshot/`（页面退化为"暂无数据"，不报错）。快照**只含派生结果**，不含 Tushare 原始行情/财务分片、不含任何密钥 |
| R3c | 本地显示过期数据 | 若误读快照，本地会看到旧结果 | 读取优先级为**实时优先**：本地只要 `data/processed/*.parquet` 存在就绝不读快照 |
| R4 | Tushare token | 泄露后他人可消耗配额 | 代码从不硬编码；统一走 `config.get_secret()`（`st.secrets` → `os.getenv` → 默认值）；`.gitignore` 排除 `secrets.toml`；`check_deploy.py` 扫描硬编码 |
| R5 | 硬编码 Windows 路径（`C:\` / `D:\`） | 云端找不到文件 | 已扫描：业务代码无绝对路径（全部基于 `PROJECT_ROOT = Path(__file__).resolve().parents[2]`）；仅文档注释里有示例路径 |
| R6 | 项目根目录 ACL 所有者异常（`LAPTOP-GJ1K6PPC\CodexSandboxUsers` / `CodexSandboxOnline`） | 影响 Codex 沙箱（PowerShell、内置浏览器）；在普通终端跑 `git` 会报 `detected dubious ownership`。**不影响** Streamlit Cloud 部署 | 记录在 [docs/14_troubleshooting.md](docs/14_troubleshooting.md)，需管理员执行 `takeown` + `icacls` 修复（修完 `dubious ownership` 一并消失）。临时绕过：`git config --global --add safe.directory "D:/GPT/基本面反转交易策略"` |
| R7 | 数据库 | 当前项目**不使用**数据库（无 sqlalchemy/psycopg2）；`secrets.toml.example` 里的 `[database]` 为未来腾讯云预留 | 预留不改代码；将来接入时按规格要求加 timeout + try/except + 明确错误提示 |
| R8 | 外部 API（Tushare） | 超时/限流 | 适配器已有重试与限流处理，失败只写状态文件，页面不会因此崩溃 |

---

## 4. 已执行的修改清单（不改业务逻辑）

1. `src/bigfish/config.py`：新增 `get_secret()`（`st.secrets` → `os.getenv` → 默认值）与 `is_streamlit_cloud()`
2. `src/bigfish/adapters/tushare_adapter.py`：token 改由 `get_secret("TUSHARE_TOKEN")` 读取，报错信息指向 `secrets.toml.example`
3. `apps/streamlit_app.py`：`_boot()` 在云端跳过 APScheduler
4. `apps/common.py`：新增 `is_cloud()`；`run_update()` / `set_update_settings()` 在云端短路为只读提示
5. `apps/app_pages/ops.py`：云端隐藏「立即更新」、置灰自动更新开关并显示说明
6. 新增 `.gitignore` / `.dockerignore` / `.streamlit/secrets.toml.example` / `Dockerfile` / `requirements.txt` / `requirements-dev.txt` / `scripts/check_deploy.py` / `scripts/check_pages.py` / `scripts/export_snapshot.py` / `scripts/run_tests.py` / `data/snapshot/`
7. `README.md`：新增部署章节（Main file path / Local Run / Secrets / Deploy）
8. `apps/common.py`：新增「实时数据优先、云端回退快照」的目录解析（不改任何计算逻辑）

**未改动**：评分公式与权重、因子逻辑、交易动作逻辑、回测引擎、数据内容、历史结果、数据库 schema、依赖树中与业务无关的部分。

---

## 5. 部署就绪度 readiness

| 目标 | 状态 |
|---|---|
| 本地可运行 | ✅ `streamlit run apps/streamlit_app.py` |
| 无密钥泄露 | ✅ 无硬编码，secrets 不入库 |
| 可推 GitHub | ✅ 已 init/commit，**只缺仓库 URL** |
| 可部署 Streamlit Cloud | ✅ 入口、依赖、secrets 模板、只读降级齐备 |
| Docker / 腾讯云迁移 | ✅ 备用镜像已就绪（非阻断） |

**NEXT ACTION**：请创建 GitHub repository，然后把 repository URL 给我：

```bash
git remote add origin https://github.com/USERNAME/REPOSITORY.git
git push -u origin main
```
