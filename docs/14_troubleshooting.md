# 14 · 故障排查与巡检（网络/环境）

## 1. 事件：网页打不开（2026-10-05）

### 现象
Codex 内置浏览器打开 `http://localhost:8501` 无法正常显示；同时 PowerShell 命令全部失败
（`Failed to create unified exec process: helper_unknown_error: setup refresh had errors`）。

### 定位过程

| 步骤 | 检查 | 结果 |
|---|---|---|
| 1 | 网站进程与健康检查 | `/_stcore/health` → **200 ok**，服务本身正常 |
| 2 | 前端资源完整性 | `index.html` + 67 个 JS/字体/图标资源**全部 200**，Content-Type 正确 |
| 3 | 数据与跑批 | 16:30 的自动更新成功（78 秒），观察池 100 / 重点 20 / 早期 20 |
| 4 | 沙箱日志 | `~/.codex/.sandbox/sandbox.<date>.log` 与 `setup_error.json` |

第 4 步找到根因：

```
granting write ACE to D:\GPT\基本面反转交易策略 for sandbox group and capability SID
write ACE grant failed on D:\GPT\基本面反转交易策略: SetNamedSecurityInfoW failed: 5
setup refresh completed with errors
```

**错误码 5 = 拒绝访问**：应用沙箱每次启动都要给"写入根目录"补一条 ACE，
而它没有权限修改这个目录的 DACL。于是**每个沙箱进程都初始化失败**——
PowerShell、CUA/node 运行时、内置浏览器一起不可用。网站服务端始终是好的。

### 根因：目录属主被写错了

```
D:\GPT                    属主 = LAPTOP-GJ1K6PPC\Yang Ka      ← 正常
D:\GPT\短线龙头项目         属主 = LAPTOP-GJ1K6PPC\Yang Ka      ← 正常
D:\GPT\基本面反转交易策略    属主 = LAPTOP-GJ1K6PPC\CodexSa…   ← 沙箱组（异常）
```

这个目录是第一轮开发时用**沙箱内的 PowerShell** 创建的，创建进程令牌的默认属主是沙箱组，
于是目录属主被记成了 `CodexSandboxUsers`。后续：
* 沙箱要给它写 ACE → 需要 WRITE_DAC → 属主是组、当前令牌又不含该组 → **拒绝访问**；
* 它的子目录（如 `.codex`）属主是本人，所以沙箱对**子目录**的 ACL 操作是成功的——
  这也解释了为什么只有根目录报错。

### 尝试过的修法（结论：需要管理员权限）

| 方法 | 结果 |
|---|---|
| 重命名目录 → 原路径重建（只需要 DELETE/CREATE） | ✗ `WinError 32`：应用的 node 辅助进程把该目录当作工作目录，句柄释放不掉 |
| 沙箱外（非受限令牌）重命名 | ✗ 同上 |
| `takeown /f …` 夺取属主 | ✗ `The current logged on user does not have ownership privileges`（未提权，Administrators 是 deny-only） |
| 修改父目录 `D:\GPT` 的 ACL 让其继承传播 | 父目录属主是本人（可改），但传播只能同步 ACE，**不能授予子目录的 WRITE_DAC**，因此无效 |

### 修复（一条管理员命令）

以**管理员身份**打开 PowerShell 或 CMD，执行：

```powershell
takeown /f "D:\GPT\基本面反转交易策略" /r /d y
icacls "D:\GPT\基本面反转交易策略" /reset /t /c
```

执行后重启 Codex 应用，沙箱的 `setup refresh` 就会恢复正常，
PowerShell 与内置浏览器随之可用。**只改权限，不动任何数据。**

### 立即绕过（不等修复也能用）

网站服务端本来就是健康的，用系统浏览器打开即可：

**http://localhost:8501**

（内置浏览器不可用是客户端运行环境的连带故障，不是网站故障。）

## 2. 巡检机制

```bash
python scripts/site_patrol.py            # 巡检并输出报告
python scripts/site_patrol.py --restart  # 网站掉线时自动重启
python scripts/site_patrol.py --json     # 机器可读结果
```

检查五项，任一异常都会写入 `output/patrol_report.md` 与
`data/processed/patrol_status.json` 并以非零退出码结束：

| 检查项 | 判定方式 | 异常含义 |
|---|---|---|
| 网站存活 | `/_stcore/health` + 随机抽一个前端 JS 资源 | 服务或静态资源异常（会区分"服务挂了"和"页面白屏"） |
| 沙箱环境 | `~/.codex/.sandbox/setup_error.json` 是否新鲜（30 分钟内） | 沙箱初始化失败 → PowerShell / 内置浏览器不可用 |
| 目录 ACL | 沙箱日志里的 `write ACE grant failed on <path>` | 工作区目录权限异常（通常是属主问题），报告里直接给出修复命令 |
| 数据新鲜度 | 本地最新交易日 vs 今天（>10 天才算异常，兼容长假） | 抓数没推进 |
| 最近更新 | `update_status.json` 状态 | 上一次跑批失败 |

**自动化**：`BigFish 网站巡检`（每天 09:00 / 13:00 / 17:00 / 21:00）执行
`site_patrol.py --restart`，**正常时不发消息**，只在发现异常、自动重启过网站、
或之前的问题恢复时通知一次。

## 3. 经验教训（写进开发约定）

1. **不要在沙箱内创建"以后要当工作区根目录"的目录**——创建者令牌的默认属主可能是沙箱组，
   会导致后续沙箱无法写 ACE。需要新项目目录时，用系统资源管理器或非沙箱终端创建。
2. **终端全挂 + 浏览器打不开，要优先怀疑环境/沙箱，而不是应用代码**：
   本次网站服务端自始至终是 200，先入为主去查代码会绕远路。
3. 诊断"网页打不开"的最小证据链：进程存活 → 健康检查 → **前端资源是否可取** → 客户端运行时日志。
