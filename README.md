# 基本面反转 Big Fish Agent

> 核心任务：寻找**基本面已由恶化转向改善 + 领先指标可验证 + 市场已开始价格确认 + 估值尚未完成重估**的 A 股。
> 先鱼塘 → 再基本面 → 再领先指标 → 再价格确认 → 再估值 → 最后交易。

本仓库实现规格书第 1~93 节的 Agent。当前进度：**Phase 1~6 已实现并跑通真实数据**
（数据层 / Market Regime / ODS / FRS / LIS / PCS / RPS / Catalyst / Kill / 过滤器 / 生命周期 /
交易动作 / 每日输出 / 7.7 年历史回测），Phase 7（看板）与 Phase 8（总控 Agent 集成）尚未实现，
见 [docs/05_review_and_limitations.md](docs/05_review_and_limitations.md)。

---

## 1. 快速开始

```bash
pip install -r requirements.txt
set TUSHARE_TOKEN=你的token          # PowerShell: $env:TUSHARE_TOKEN="..."（Token 只从环境变量读取）

python scripts/fetch_data.py         # 抓取原始数据（可断点续跑，增量跳过已缓存）
python scripts/run_daily.py          # 全链路跑批 + 生成每日输出
python scripts/verify_sample.py      # 规格书第 84 节：随机抽 20 只独立重算对账

python scripts/fetch_history.py      # Phase 6：历史回补到 2018 年（约 40 分钟，可断点续跑）
python scripts/run_backtest.py       # Phase 6：7.7 年月度调仓回测 + 自动生成报告
python scripts/run_backtest.py --from-panel --name backtest_2019_2026   # 只重出报告
python scripts/watchlist_flicker.py --write   # 观察池闪烁率跟踪（真实每日口径）

python scripts/start_site.py         # 启动网站（含自动更新）→ http://localhost:8501
```

## 1.1 网站（Phase 7）

8 个页面：雷达总览 / 观察池 Top100 / 早期线索榜 / 行业鱼塘 / FRS×PCS 四象限 /
基本面趋势 / Kill 监控 / 数据更新与状态。启动即带**自动更新**：
APScheduler 在工作日 08:30（盘前）与 16:30（收盘）自动执行
「增量抓数 → 全链路跑批 → 观察池跟踪」，跑完后页面检测到数据集变化会自动刷新；
页面上也能手动「立即更新」，并可开关自动更新。细节见 [docs/13_website.md](docs/13_website.md)。

```bash
python scripts/start_site.py --port 8510      # 换端口
python scripts/start_site.py --no-scheduler   # 只起网站
python scripts/run_scheduler.py               # 只跑调度器（无人值守）
python scripts/site_patrol.py --restart       # 巡检（网站掉线自动重启）
```

**巡检**：`site_patrol.py` 检查网站存活（含前端资源）、沙箱环境、工作区目录 ACL、
数据新鲜度与最近更新状态，产出 `output/patrol_report.md`；
自动化 `BigFish 网站巡检`（每天 09/13/17/21 点）在异常时通知。
遇到过"网页打不开但服务端正常"的情况，排查记录见
[docs/14_troubleshooting.md](docs/14_troubleshooting.md)。

**自动化**：已配置心跳式自动化 `BigFish 每日跑批与闪烁率跟踪`（工作日 17:30）——
增量抓数 → 跑当日报表 → 更新观察池历史与闪烁率。样本不足 20 个交易日时保持安静，
只在样本达标、跑批失败或出现结构性变化时通知。

## 1.2 部署（GitHub + Streamlit Community Cloud）

**Main file path: `apps/streamlit_app.py`**（本仓库没有 `app.py` / `main.py`，
Streamlit Cloud 创建 App 时入口必须填这一项）。

### Local Run

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux / macOS:
source .venv/bin/activate

pip install -r requirements.txt
streamlit run apps/streamlit_app.py     # → http://localhost:8501
```

### Environment / Secrets

密钥**只从 Streamlit secrets 或环境变量读取**，代码里没有任何硬编码 token：

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # 然后填入真实值
```

优先级：`st.secrets` → 环境变量 `os.getenv()` → 默认空值（实现见
`src/bigfish/config.py` 的 `get_secret()`）。**`.streamlit/secrets.toml` 已被 `.gitignore`
忽略，禁止提交到 GitHub。**

### Deploy to Streamlit Cloud

1. 把 repository 推到 GitHub（分支 `main`）
2. 登录 Streamlit Community Cloud → **New App**
3. 选择 repository
4. Branch = `main`
5. **Main file path = `apps/streamlit_app.py`**
6. 在 Advanced settings / **Secrets** 里粘贴 `TUSHARE_TOKEN` 等真实值
7. Deploy

上线前先跑一次自检（只读，不修改任何数据）：

```bash
python scripts/check_deploy.py       # 输出 PASS/WARN/FAIL 与 DEPLOY READY / NOT READY
python scripts/check_pages.py --all  # 页面渲染自检（正常 / 云端只读 / 快照回退 / 空数据）
python scripts/run_tests.py          # 单元测试（带时间戳临时目录，规避本机权限问题）
```

> **云端是只读部署**：`data/raw`、`data/processed` 不进 git（合计约 2.8 GB），
> 抓数与跑批在本地或常驻进程完成后，把结果随代码一起发布；页面上「立即更新」与
> 定时调度器在云端会自动关闭并给出提示，不会报错。Docker / 腾讯云迁移备用见
> `Dockerfile`、`.dockerignore`（不影响 Community Cloud）。

审计与测试记录：[DEPLOYMENT_AUDIT.md](DEPLOYMENT_AUDIT.md)、[TEST_REPORT.md](TEST_REPORT.md)。

### 云端的数据从哪来：`data/snapshot/` 最新一期快照

云端仓库里没有 2.8 GB 的原始与跑批产物，所以随代码发布一份**只含最新一期的小快照**
（约 4.5 MB，19 个文件）：

```bash
python scripts/export_snapshot.py            # 跑批后刷新快照（只复制页面真正读的那几个文件）
python scripts/export_snapshot.py --dry-run  # 先看要复制什么
```

读取规则（`apps/common.py`）：**本地有实时数据就永远读实时数据**；只有实时目录没有
parquet 时才回退到 `data/snapshot/`（云端就是这个情况），页面因此不会空白，
也不会因为快照而在本地显示过期结果。每日更新流程是：

```bash
python scripts/fetch_data.py && python scripts/run_daily.py && python scripts/watchlist_flicker.py --write
python scripts/export_snapshot.py
git add data/snapshot && git commit -m "Update data snapshot" && git push
```

> ⚠️ 快照是**公开仓库**的一部分：它包含最近一期的评分、观察池/重点/早期榜单与每日简报。
> 如不希望这些结果公开，把 GitHub 仓库改为 private（Streamlit Cloud 支持私有仓库授权部署），
> 或删除 `data/snapshot/`（页面会退化为"暂无数据"但不会报错）。

输出目录 `output/`：

| 文件 | 说明 |
|---|---|
| `bigfish_watchlist_<日期>.csv` | **观察池 Top100**（漏斗上游，召回优先） |
| `bigfish_ranking_<日期>.csv/.md` | **重点 Top20**（人工深研入口）+ 早期线索榜 |
| `bigfish_early_<日期>.csv` | **早期线索榜**（FRS 已确认、PCS 尚未确认） |
| `bigfish_cards_<日期>.md` | 规格书第 58~61 节 完整卡片 Top5 |
| `daily_brief_<日期>.md` | 规格书第 91 节每日输出（MARKET / PONDS / TOP10 / KILL / ACTION…） |
| `opportunity_ponds_<日期>.csv` | 规格书第 6/53 节行业 ODS 排名 |
| `interface_<日期>.json` | 规格书第 79 节总控 Agent 接口 |
| `data_quality_<日期>.md` | 数据覆盖率与已知缺口（如实标注） |
| `verification_sample_<日期>.csv/.md` | 随机抽检独立核验结果 |

---

## 2. 本轮真实数据结果（as_of = 2026-09-30）

| 指标 | 数值 |
|---|---|
| 股票池（含退市，规避幸存者偏差） | 5,911 |
| 有当日行情、可入池标的 | 5,561 |
| 完成 FRS/BFS 计算 | 6,528 |
| 行业覆盖率（申万一级） | 100% |
| FRS ≥ 70（CONFIRMED 以上） | 154 |
| FRS≥70 且 LIS≥65 且 PCS≥65 | 7 |
| 通过全部过滤器、进入候选池 | 371（BFS 最高 76.2） |
| 候选池内 Kill 预警 | 7（全市场 4,973，属正常，见数据质量报告） |
| 随机抽检 20 只独立重算对账 | 20/20 通过 |

市场状态：**Risk Off**（评分 22.4，MA20 上方个股 31.6%）。
行业机会密度 TOP5：石油石化 75.0 / 美容护理 74.6 / 煤炭 73.1 / 非银金融 72.2 / 银行 63.4。

> 本轮**没有 S / A+ 级候选**：S 级需要同时满足 FRS≥75、LIS≥70、PCS≥70（规格书第 34 节硬门槛），
> 当前得分最高的候选 PCS 为 69.1，差 0.9 分被规则拦下并降级为 A。这是硬门槛生效的表现，
> 不是计算失败；若长期无 S 级，应回头检查 PCS 阈值，而不是放宽 S 级门槛。

> 另有 27 只标的（主要是银行/保险）因 LIS 可用分项覆盖率不足 45%（合同负债、存货、应收等
> 字段对金融企业无意义）被判为 `DATA INCOMPLETE`，LIS 不参与其 BFS 计算并被如实标注。
> 这也直接说明：**金融行业需要专用的 FRS/LIS 模板**，见
> [docs/05_review_and_limitations.md](docs/05_review_and_limitations.md)。

---

## 2.1 Phase 6 · 历史回测结论（2019-01-31 ~ 2026-09-30，93 次月度调仓）

方法：信号日收盘后用当时可见的数据打分（财务按公告日），**次日开盘入场**，持有 20/60/120/250
个交易日后收盘出场，双边成本 0.16%，股票池含已退市股票，清洗后 397,120 个样本
（93 次月度调仓）。完整表格见 [docs/06_backtest_report.md](docs/06_backtest_report.md)，
滚动窗口与参数扫描见 [docs/08_pool_rule_scan.md](docs/08_pool_rule_scan.md)。

> ⚠️ **2026-10-05 口径修正**：早先的前视收益函数要求 250 个交易日的完整窗口，否则
> **该调仓日的所有持有期一起丢弃**，导致 2025-09 之后的信号被静默排除（约 13 次调仓）。
> 已改为「每个持有期独立判断可用性」并用 `scripts/rebuild_forward_returns.py` 重建面板，
> 下表是修正后的数字。修正后**样本外多空差从 +1.41pp 升到 +2.25pp**——
> 也就是说，之前"样本外衰减"的结论有一部分是数据缺口造成的假象。

| 分层（FRS，60 日净收益） | 全样本 | 样本内 2019-2023 | 样本外 2024-2026 |
|---|---:|---:|---:|
| TOP 10% | **+4.63%** | +3.57% | +6.30% |
| TOP 10-20% | +4.03% | +3.08% | +5.53% |
| MIDDLE 20-80% | +3.27% | +2.38% | +4.67% |
| BOTTOM 20% | +2.38% | +1.25% | +4.05% |
| **多空差** | **+2.25pp** | +2.24pp | **+2.25pp** |

滚动窗口（单年）：**FRS 多空差 8/8 个年度为正**，平均 +2.37pp、标准差 1.24pp；
唯一的负值是 2022 年的 BFS 多空差（−0.53pp）。

BFS 五分位（月频调仓 × 20 交易日持有，非重叠近似，扣费后年化）：

| 组合 | 全样本 | 样本内 | 样本外 |
|---|---:|---:|---:|
| Q5（最高） | 见 [回测报告](docs/06_backtest_report.md) 第 4.1 节 | | |
| 默认候选池（非 Reject，Top20） | **+23.8%，Sharpe 0.95，最大回撤 −29.0%** | | |
| Q1（最低） | 见 [回测报告](docs/06_backtest_report.md) 第 4.1 节 | | |

> 这些绝对水平仍被市场 beta 主导（样本外是普涨行情）；有信息量的是 Q5 与 Q1 的差。

**怎么读这些数字（三句话）：**

1. **方向是对的，幅度很薄。** 分层单调（FRS：TOP10% > TOP20% > MIDDLE > BOTTOM），
   FRS×PCS 二维矩阵里「高 FRS + 高 PCS」也确实优于「只有高 FRS」，规格书第 71 节的假设**弱确认**；
   但 20 日多空差只有 +0.5pp 量级，不足以单独构成交易理由。
2. **样本内外方向一致，没有典型过拟合特征**，但样本外（2024-2026）是普涨行情，
   所有分组都赚钱、多空差被压缩 —— 可信度主要在多空差，不在绝对收益。
3. **S 级无法验证。** BFS≥80 全样本只有 3 个样本，统计上不成立（规格书第 74 节的 S 级测试
   需要先解决评分分布过于集中在 B/C 段的问题）。可验证的是 BFS 前 1%/5%/10% 头部组合，
   60 日净收益 +5.4%/+5.0%/+4.8%，略优于全样本中位数。

### 2.2 结构改进迭代（2026-10，回测驱动）

第一轮回测（v1）暴露问题后按"改结构 → 用同一套数据重测"的流程迭代了一轮，
详细对比见 [docs/07_structural_change_review.md](docs/07_structural_change_review.md)：

| 改动 | 内容 | 验证结果 | 处置 |
|---|---|---|---|
| FRS 幅度化 | 拆成 结构分 65% + 幅度分 35%（截面分位） | 样本外多空差 +1.06 → **+1.46pp** | ✅ 保留 |
| RPS 估值硬约束 | PE>行业×1.5 或上行<15% → 封顶 B；×2.0 → 封顶 C | 命中组比未命中组差 **−0.61pp**（改进前 −0.56pp，两个面板一致） | ✅ 保留 |
| PCS 突破确认 | 区分"突破确认"（+5）与"突破失败"（−5），失败接入 Kill | BFS 多空差 +1.71 → **+1.93pp** | ✅ 保留 |
| LIS 去重复计分 | 订单/需求 合同负债 70% + 收入加速度 30% | 计入整体改善 | ✅ 保留 |
| 低基数降级 | 曾按失败案例库给低基数打 85 折 | **被验证否定**：低基数组反而高 +1.36pp | ❌ 已撤销 |

第二轮（2026-10-05，同样做闭环验证）：

| 改动 | 内容 | 验证结果 | 处置 |
|---|---|---|---|
| 金融行业专用模板 | 银行/非银改用 TTM 口径 + 净资产 + ROE/ROA + 投资收益占利润比重 | 金融股内部多空差 −0.61 → **−0.14pp**（仍为负） | ⚠️ 模板保留，但**封顶 B 级、禁止试探仓** |
| 一次性收益过滤器收窄 | 改为"归母在涨、扣非在跌或显著落后" | 命中组反而**高 +0.78pp** | ❌ 硬排除撤销 → 封顶 B + 披露 |
| 价值陷阱过滤器复核 | —（同口径检验） | 命中组**高 +0.77~0.95pp** | ❌ 硬排除撤销 → 封顶 B + 披露 |

> 第 2、3 条与规格书第 49/50 节的原始意图存在**真实冲突**：在反转策略里，
> "便宜 + 当期仍差 + 反转信号强"正是最早的候选，硬排除等于排除最好的样本。
> 处理方式是**保留识别与披露、取消硬排除**，并把冲突写进文档而不是悄悄忽略。
> 同面板 what-if：候选池扩大 22.6%，60 日平均净收益 +4.63% → +4.66%。

### 2.3 参数扫描的结论是"改不动"

在同一份面板上扫了 16 种候选池规则（门槛等级、估值倍数、是否剔除 Kill/价值陷阱、
是否叠加 ODS 过滤、持仓数量 5~100 只），并对关键对比做了**配对检验**：
**六组关键对比的逐期收益差全部不显著（|t| < 2）**——扫描表里 0.95 与 1.07 的 Sharpe 差异
是噪声。所以本轮**不修改任何默认参数**，这本身就是结论。

唯一稳健的结构性发现是**组合宽度**：持仓 5 只时最大回撤 −54%、Sharpe 0.48；
20 只 −29%、0.95；50 只 −19.9%、1.06。均值的差异同样不显著，
但方差的下降是分散化的机械结果。因此**不建议按规格书第 92 节的「每天压到 3~5 只」构建组合**，
候选池应保持 20 只以上，下单前再按流动性与相关性收敛到 10~20 只。
详见 [docs/08_pool_rule_scan.md](docs/08_pool_rule_scan.md)。

### 2.4 候选生成器口径（2026-10-05 落地）

回测表明**这套评分不是可用的选股策略，但是有价值的候选生成器**：
抓大鱼的概率只比随机高 20%（19.1% vs 16.0%），但踩雷率更低（10.8% vs 11.6%，
而"追过去 20 日涨幅"的因子是 33.2%）；且召回率低的主因是名单太小——
Top20→Top100 精度只降 1.5pp、召回 ×3.6。因此输出改成漏斗，并拆出两个分数：

| 分数 | 组成 | 用途 | 实测 |
|---|---|---|---|
| **抓鱼分** | FRS 40% + LIS 25% + RPS 20% + ODS 10% + Catalyst 5% | 找上行弹性 | 踩雷率 Q1→Q5：13.5%→9.8% |
| **避雷分** | PCS 35% + Kill 25% + 估值约束 20% + 数据完整度 20% | 下限保护 | **踩雷率 Q1→Q5：14.0%→9.1%，大鱼率不动（16.1%→15.9%）** |

日报四区块：**① 观察池 Top100 → ② 重点 Top20 → ③ 早期线索榜 → ④ 卡片 Top5**。
观察池排序键实测对比过 BFS / 抓鱼分 / 两者均值（大鱼率 17.6%~17.9%，差异在噪声内），
按"召回优先、风险次之"取 BFS。连续在榜期数（`streak`）随多日运行累积。

详见 [docs/11_generator_diagnostics.md](docs/11_generator_diagnostics.md)。

过程中还发现并修正了一个**方法论错误**：第一版报告用「失败案例库」的类别平均亏损
（条件概率）来决定改哪个模块，但那个排序没有区分度——任何类别只要进了失败样本，
条件平均亏损必然是大负数。改用**无条件均值**（命中组 vs 未命中组）后，
估值约束依然成立、低基数结论直接反转。这条教训已写进
[docs/05_review_and_limitations.md](docs/05_review_and_limitations.md) 第 6 节。

---

## 3. 六层架构

```
LEVEL 1 Market Regime        市场状态（只影响优先级，不删除基本面候选）
        ↓
LEVEL 2 Opportunity Density  行业 ODS：30% 景气 + 20% 资金 + 20% 盈利预期 + 15% 价格/库存 + 10% 估值 + 5% 催化
        ↓
LEVEL 3 Fundamental Reversal FRS：收入 20 + 利润 25 + 毛利率 15 + 现金流 15 + 经营质量 10 + 特殊信号 15
        ↓
LEVEL 4 Leading Indicator    LIS：订单/需求 25 + 产品价格 20 + 库存周期 15 + 产能 10 + 合同负债 10 + 高频 10 + 指引 5
        ↓
LEVEL 5 Price Confirmation   PCS：价格结构 25 + 均线 15 + 相对强弱 20 + 量能 15 + 聪明钱 15 + 消息不对称 10
        ↓
LEVEL 6 Valuation + Catalyst RPS（Bear/Base/Bull 情景）+ Catalyst（概率×影响×时点）
        ↓
Big Fish Score = 0.30·FRS + 0.20·LIS + 0.20·PCS + 0.10·ODS + 0.15·RPS + 0.05·CS
        ↓
S / A+ / A / B / C / Reject → 四象限 → 生命周期 → 动作
```

目录结构：

```
configs/            default.yaml（全部权重/阈值）+ 行业前瞻指标模板
src/bigfish/
  adapters/         Tushare 适配器（缓存 / 限流重试 / 断点续跑）
  storage/          Parquet 缓存 + 元数据（数据源、时间戳、行数）
  pipeline/         fetch / fetch_history / fundamentals（PIT 单季度面板）/
                    dataset / history（回测数据窗口）/ run（全链路）
  backtest/         engine（信号重算 + 前视收益 + 数据卫生）/ analysis / report
  core/             market_regime / opportunity_density / fundamental_reversal /
                    leading_indicator / price_confirmation / valuation / catalyst /
                    kill_signal / filters / lifecycle / trade_plan / big_fish_score
  report/           cards（Big Fish Card）/ daily_brief（每日输出）
scripts/            fetch_data / fetch_history / run_daily / run_backtest / verify_sample
docs/               架构 / 公式 / 数据与 PIT / 阶段验收 / 局限与复盘 / 回测报告
tests/              pytest（17 项：口径、PIT、权重守恒、回测统计、输出不变量）
data/raw|processed  原始数据与中间产物；output/ 交付物
```

---

## 4. 合规与边界

* 本系统输出的是**研究结论与风控条件**，不是投资建议；不输出「强烈买入」这类措辞，
  动作集合限定为 `RESEARCH / WATCH / WAIT_TRIGGER / STARTER_POSITION / ADD_ON_CONFIRMATION /
  HOLD / REDUCE / EXIT / REJECT / WAIT_PULLBACK`。
* 所有财务数据使用**公告日**（point-in-time），不使用报告期结束日；同一报告期多次公告只取最早一次。
* 缺失因子一律显式标注 `DATA INCOMPLETE`，按可用分项重新归一化权重，不做主观填充。
* 估值情景（Bear/Base/Bull）是情景推演，**不是目标价承诺**。

细节见 [docs/01_architecture.md](docs/01_architecture.md)、[docs/02_formulas.md](docs/02_formulas.md)、
[docs/03_data_and_pit.md](docs/03_data_and_pit.md)、[docs/04_phase_acceptance.md](docs/04_phase_acceptance.md)、
[docs/05_review_and_limitations.md](docs/05_review_and_limitations.md)。
