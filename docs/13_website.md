# 13 · 网站封装与自动更新（Phase 7）

## 1. 启动

```bash
python scripts/start_site.py              # 默认 http://localhost:8510（端口见 configs/default.yaml）
python scripts/start_site.py --port 8520  # 临时换端口
python scripts/start_site.py --no-scheduler   # 只起网站，不自动更新
python scripts/run_scheduler.py           # 只跑调度器（无人值守部署）
```

启动脚本会在**启动进程内**拉起调度器（这样不开浏览器也会按时更新），网站进程内不再重复启动
（通过 `BIGFISH_DISABLE_SCHEDULER=1` 传递）。调度器与网站之间用 `data/processed/scheduler_heartbeat.json`
心跳文件通信，所以「运行与维护」页能看到**另一个进程**里的下次执行时间。

## 2. 页面

| 分区 | 页面 | 内容 |
|---|---|---|
| 查看 | 雷达总览 | 市场状态 KPI、重点 Top20、观察池行业分布、行业机会密度、早期线索榜、今日卡片 |
| 查看 | 观察池 Top100 | 行业/通道/评级/最低避雷分筛选、连续在榜期数、CSV 下载 |
| 查看 | 早期线索榜 | FRS≥70 且 PCS<60 的提前量清单（附实测风险提示） |
| 查看 | 行业鱼塘 | ODS 排名与六个分项拆解、多行业分项对比 |
| 分析 | FRS × PCS 四象限 | 散点（大小=RPS，颜色=象限），BUY ZONE 明细 |
| 分析 | 基本面趋势 | 任选候选股票，看 12 个季度收入/归母/毛利率/现金流趋势与同比指标 |
| 分析 | Kill 监控 | 候选池内与全市场的 Kill 分级、类型分布 |
| 运行与维护 | 数据更新与状态 | 一键更新、自动更新开关、调度器状态、运行历史、闪烁率跟踪、数据版本 |

页面实现遵循 Streamlit 官方技能的建议：`st.navigation` + `app_pages/` 多页结构、
原生组件优先（`st.container(border=True)` 卡片、`st.metric`、`st.dataframe` 列配置）、
Vega 系图表（`st.bar_chart` / `st.scatter_chart` / `st.line_chart`）、
`@st.cache_data` 按文件 mtime 失效、`@st.fragment(run_every=…)` 做数据集版本轮询。

## 3. 自动更新

```
APScheduler（进程内，Asia/Shanghai）
   ├── 盘前更新  08:30  周一~周五
   └── 收盘更新  16:30  周一~周五
        ↓
UpdateService（文件锁，同一时刻只允许一个更新）
   ├── fetch    增量抓数（已存在分片自动跳过）
   ├── daily    全链路跑批 → 观察池/重点/早期/卡片/简报
   └── flicker  观察池历史与闪烁率跟踪
        ↓
data/processed/update_status.json + update_history.json
```

* **手动更新**：「运行与维护」页的「立即更新」按钮，与调度器共用同一个 `UpdateService`，
  行为与状态完全一致；重复触发会被文件锁挡住并提示"已有更新在运行"。
* **自动更新开关**：页面上的开关写入 `data/processed/update_settings.json`，调度器每次触发前读取，
  关闭后即使到点也不会跑。
* **页面自动刷新**：数据集文件（评分/观察池/摘要等）的 mtime 变化会被 `@st.fragment` 轮询到，
  自动清缓存并整页刷新——跑批完成后不需要手动 F5。
* **僵尸锁保护**：锁文件超过 1 小时视为失效并自动清除，避免异常退出后永久锁死。

## 4. 无人值守部署要点

1. 常驻运行 `python scripts/start_site.py`（网站+调度器同进程），或拆成
   `python scripts/run_scheduler.py` + `streamlit run apps/streamlit_app.py`（调度器独立存活）。
2. 需要环境变量 `TUSHARE_TOKEN`；权限不足的接口会显式记录到 `fetch_manifest.json` 的 `missing`。
3. 数据与产出都在项目内（`data/`、`output/`），整目录拷贝即可迁移。
4. 只读部署（不允许写数据）时用 `--no-scheduler`，页面会显示"调度器未启动"。

## 5. 已知边界

* 页面读的是**跑批产物**（parquet + md），不在页面上重算评分——这样页面秒开，
  也保证"看到的数字"和回测/脚本口径完全一致。
* 基本面趋势页只保存候选名单（观察池+早期）的季度数据，不落全市场面板。
* 网站没有做多用户与权限（本地研究工具）；如需暴露到公网，请自行加认证与 HTTPS。
