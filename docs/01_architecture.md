# 01 · 系统架构

## 1. 设计原则

1. **先鱼塘再个股**：Step 1~2 先算行业 ODS，个股扫描只在 ODS 高的区域优先（规格书第 53 节），
   同时保留 `SPECIAL SITUATION` 通道避免漏掉公司级独立反转（第 54 节）。
2. **三类证据交叉验证**：基本面反转必须至少两类证据（经营数据 / 前瞻指标 / 价格行为）同时成立，
   否则只能进入 WATCH。
3. **得分与风险双通道**：Kill Signal 独立于 BFS，高分不能掩盖风险（第 44 节）。
4. **参数全部外置**：权重、阈值、窗口都在 `configs/default.yaml`，代码不写死。
5. **缺失即标注**：任何因子缺失 → 按可用分项重新归一化 + 记录覆盖率 + 输出 `DATA INCOMPLETE`。

## 2. 模块职责

| 层 | 模块 | 输入 | 输出 |
|---|---|---|---|
| L1 | `core/market_regime.py` | 指数日线、全市场日线 | Risk-On / Neutral / Risk-Off + 优先级系数 |
| L2 | `core/opportunity_density.py` | 行业指数、成员财报、业绩预告、增减持/回购 | 31 个申万一级行业 ODS + 区域划分 |
| L3 | `core/fundamental_reversal.py` | PIT 单季度财务面板 | FRS 六分项 + 加分理由 + 扣分项 + 反转类型 |
| L4 | `core/leading_indicator.py` | 合同负债、库存、毛利率、行业价格动量、业绩预告 | LIS 八分项 + 数据缺口说明 |
| L5 | `core/price_confirmation.py` | 日线矩阵、行业指数、资金流、公告事件 | PCS 六分项 + 事件反应不对称 |
| L6 | `core/valuation.py` / `core/catalyst.py` | TTM/归一化盈利、行业 PE、回购/增持/预告 | RPS（Bear/Base/Bull）+ Catalyst |
| — | `core/kill_signal.py` | 财务趋势、价格结构、行业 ODS、预告 | Kill 计数 + WARNING/REDUCE/EXIT |
| — | `core/filters.py` | 估值、现金流、应收/存货、ST、流动性 | VALUE_TRAP / FAKE_TURNAROUND / RISK_BLOCK |
| — | `core/lifecycle.py` | FRS/LIS/PCS/BFS/RPS + Kill | 八态生命周期 |
| — | `core/big_fish_score.py` | 以上全部 | BFS、等级、四象限、动作、叙事字段、接口 JSON |
| — | `core/trade_plan.py` | 收盘价、ATR14、MA20、Kill | 入场/止损/目标/盈亏比/动作 |

## 3. 每日运行流程（规格书第 55 节）

```
Step 1 更新市场数据      scripts/fetch_data.py（增量）
Step 2 更新行业 ODS      core/opportunity_density.compute_ods
Step 3 扫描财务反转      core/fundamental_reversal.compute_frs
Step 4 更新前瞻指标      core/leading_indicator.compute_lis
Step 5 计算价格确认      core/price_confirmation.compute_pcs
Step 6 计算估值空间      core/valuation.compute_rps
Step 7 计算 BFS          core/big_fish_score.assemble
Step 8 运行 Kill Signal  core/kill_signal.compute_kill
Step 9 更新生命周期      core/lifecycle.classify_stage
Step 10 输出候选         pipeline/run.py → output/*
```

单次跑批（5561 只可交易标的、666 个交易日行情、14 个报告期三表）约 **75 秒**；
首次全量抓数约 **10 分钟**（1452 次日线接口 + 财务三表分页），之后增量更新。

## 4. 数据流

```
Tushare API ──(adapters/tushare_adapter)──► data/raw/*.parquet（带 meta.json）
                                              │
                       pipeline/fundamentals ─┴─► PIT 单季度面板（ann_date 可见）
                                              │
        core/*（六个评分模块 + Kill + 过滤器）──┴─► data/processed/bigfish_score_latest.parquet
                                              │
                       pipeline/run + report ──┴─► output/*（排名/卡片/简报/接口/数据质量）
```

历史快照累积在 `data/processed/bigfish_history.parquet`，用于每日输出的
NEW ENTRIES / UPGRADE / DOWNGRADE 对比（规格书第 91 节）。
