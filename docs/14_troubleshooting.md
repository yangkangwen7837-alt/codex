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

检查六项，任一异常都会写入 `output/patrol_report.md` 与
`data/processed/patrol_status.json` 并以非零退出码结束：

| 检查项 | 判定方式 | 异常含义 |
|---|---|---|
| 网站存活 | `/_stcore/health` + 随机抽一个前端 JS 资源 | 服务或静态资源异常（会区分"服务挂了"和"页面白屏"） |
| 线上站点 | 同 `check_live_site.py`：首页 / 健康端点 / 静态资源 / 前端握手配置（地址取 `site.public_url`，未配置则跳过） | Streamlit Cloud 上的站点挂了或资源缺失；**只汇报不重启**（云端重启只能在控制台操作） |
| 沙箱环境 | `~/.codex/.sandbox/setup_error.json` 是否新鲜（30 分钟内） | 沙箱初始化失败 → PowerShell / 内置浏览器不可用 |
| 目录 ACL | 沙箱日志里的 `write ACE grant failed on <path>` | 工作区目录权限异常（通常是属主问题），报告里直接给出修复命令 |
| 数据新鲜度 | 本地最新交易日 vs 今天（>10 天才算异常，兼容长假） | 抓数没推进 |
| 最近更新 | `update_status.json` 状态 | 上一次跑批失败 |

单点体检任意地址（本地或云端都一样）：

```bash
python scripts/check_live_site.py https://xxxx.streamlit.app
```

**自动化**：`BigFish 网站巡检`（每天 09:00 / 13:00 / 17:00 / 21:00）执行
`site_patrol.py --restart`，**正常时不发消息**，只在发现异常、自动重启过网站、
线上站点检查不通过、或之前的问题恢复时通知一次。

## 3. 经验教训（写进开发约定）

1. **不要在沙箱内创建"以后要当工作区根目录"的目录**——创建者令牌的默认属主可能是沙箱组，
   会导致后续沙箱无法写 ACE。需要新项目目录时，用系统资源管理器或非沙箱终端创建。
2. **终端全挂 + 浏览器打不开，要优先怀疑环境/沙箱，而不是应用代码**：
   本次网站服务端自始至终是 200，先入为主去查代码会绕远路。
3. 诊断"网页打不开"的最小证据链：进程存活 → 健康检查 → **前端资源是否可取** → 客户端运行时日志。

## 4. 连带影响：git 报 "dubious ownership"（2026-10-05 补充）

同样是属主问题的连带后果：沙箱进程创建的文件/目录，属主会记成沙箱账户
（`CodexSandboxUsers` / `CodexSandboxOnline`），而普通终端是当前用户，于是

```
fatal: detected dubious ownership in repository at 'D:/GPT/基本面反转交易策略'
'.../.git' is owned by: LAPTOP-GJ1K6PPC/CodexSandboxOnline
but the current user is: LAPTOP-GJ1K6PPC/Yang Kangwen
```

**两种处理**：

1. 执行第 1 节的管理员修复命令（属主改回本人）→ 报错自动消失（推荐）；
2. 临时绕过（只影响这一台机器，写进全局 git 配置）：

```bash
git config --global --add safe.directory "D:/GPT/基本面反转交易策略"
```

`scripts/check_deploy.py` 已经内置自愈：检测到这个报错时会自动带上
`-c safe.directory=...` 重试，所以自检本身不受影响。

## 5. 事件：浏览器显示 "Connection error"（2026-10-07）

### 现象

页面能打开 HTML 外壳，但随即报 `Connection error — Is Streamlit still running?`。

### 定位

| 检查 | 命令 | 结果 |
|---|---|---|
| 端口是否有监听 | `netstat -ano -p tcp`（筛 `:8501`） | **没有任何监听** → 站点进程已经死了 |
| 是否有 python 进程 | `tasklist /fi "imagename eq python.exe"` | 只剩一个无关进程 |
| 站点日志 | `output/site.log` | 最后一条只有"由巡检重启"标题、没有启动输出 → 进程**被外部结束**（崩溃会留 traceback） |

结论：`Connection error` 是**服务端进程已死**的正确提示，不是前端故障；
浏览器标签仍停在旧会话上，因此必须重新加载页面。

### 根因：站点是被"沙箱进程"拉起来的

网站此前由 `site_patrol.py`（在 Codex 沙箱内）用 `subprocess.Popen` 启动，属于沙箱进程的后代。
**沙箱会话一结束，整棵进程树被一起结束**，网站跟着消失，只能等下一次巡检（每天 4 次）拉回来 ——
这就是 10-06、10-07 两次掉线的来源，中间存在打不开的空窗期。

### 修复：改由 Windows 计划任务常驻（不再依赖任何会话）

```powershell
schtasks /Create /TN BigFishSite /TR "\"C:\Python314\python.exe\" \"D:\GPT\基本面反转交易策略\scripts\ensure_site.py\"" /SC MINUTE /MO 5 /RL LIMITED /F
schtasks /Create /TN BigFishUpdateAM /TR "\"C:\Python314\python.exe\" \"D:\GPT\基本面反转交易策略\scripts\run_update_once.py\"" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 08:30 /RL LIMITED /F
schtasks /Create /TN BigFishUpdatePM /TR "\"C:\Python314\python.exe\" \"D:\GPT\基本面反转交易策略\scripts\run_update_once.py\" --publish" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 16:30 /RL LIMITED /F
```

* `BigFishSite`：每 5 分钟健康检查，**只有掉线才拉起**（健康时不写文件、不重启），
  空窗期从"最多 6 小时"缩到"最多 5 分钟"；
* `BigFishUpdateAM` / `BigFishUpdatePM`：替代站点内置调度器做 08:30 / 16:30 更新，
  16:30 那次顺带把快照发布到 GitHub（`--publish`）。

### 附带发现：某些包只存在于沙箱层

修复时站点内置 APScheduler 报 `No module named 'apscheduler'`。排查确认：**同一个路径
`C:\Users\12363\AppData\Roaming\Python\Python314\site-packages`，沙箱内外看到的内容不同**。

| 上下文 | streamlit | apscheduler |
|---|---|---|
| Codex 沙箱内（会话执行命令） | 1.64.0 | **有**（3.11.3） |
| 沙箱外（Windows 计划任务，普通身份） | 1.65.0 | **没有** |

因此在沙箱内 `pip install apscheduler` 会一直报 "already satisfied"，而普通身份的进程依然 import 失败。
**结论：不要依赖"仅沙箱内可用"的包做常驻服务**；定时逻辑交给 Windows 计划任务（本轮已改），
或由用户在普通终端自行安装。

### 立即恢复办法

站点已在跑时，浏览器按 **Ctrl + Shift + R** 强制重新加载即可；
若确认端口没有监听，执行 `python scripts/ensure_site.py`（或等 5 分钟计划任务）。

### 经验教训（补充）

4. **"网页打不开"先看端口有没有监听**：`netstat` 一秒定性，比从代码查起快得多；
   进程被外部结束时日志里不会有 traceback，可用来区分"被杀"与"崩溃"。
5. **常驻服务不要从沙箱/临时会话里启动**：会话结束会带走整棵进程树。

## 6. 事件：站点起不来，因为 8501 被其它项目占用（2026-10-07）

### 现象

重启后仍然"无法运行"，但健康检查返回 200 —— 看起来正常却打不开本项目的页面。

### 定位

```bash
netstat -ano -p tcp            # 8501 → LISTENING，PID 19852
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"ProcessId=19852\" | % {$_.Name; $_.CommandLine}"
# → com.docker.backend.exe
docker ps
# → fund-sales-workbench:1.0.0  ...  0.0.0.0:8501->8501/tcp  fund-sales-app-1
```

**8501 被另一个项目的 Docker 容器（`fund-sales-workbench`）发布并占用**，
Docker 的用户态代理接管了 `0.0.0.0:8501`。于是：

* 浏览器打开 `localhost:8501` 看到的是**别人的应用**（同样是 Streamlit，所以健康检查也返回 200，极易误判）；
* 我们的 `start_site.py` 绑定 8501 失败 → 起不来，看护脚本每 5 分钟拉一次都失败。

### 修复：给本项目一个专用端口

`configs/default.yaml` 的 `site.port` 从 `8501` 改为 **`8510`**。
启动脚本、巡检（`site_patrol.py`）、看护（`ensure_site.py`）、
`check_live_site.py` 全部从配置读端口，改一处即可，无需改任务。

```bash
python scripts/ensure_site.py          # 拉起（现在监听 127.0.0.1:8510）
python scripts/check_live_site.py      # 默认检查配置里的端口
```

### 经验教训（补充）

6. **健康检查返回 200 不等于"你的应用在跑"**：Streamlit 的 `/_stcore/health` 对任何
   Streamlit 应用都返回 `ok`。判断"是不是我的应用"要看**监听进程**（`netstat` → PID → 进程名/命令行），
   这也是 `ensure_site.py` 现在会额外报告"端口被其它程序占用"的原因。
7. **固定端口会被抢**：本机同时跑多个项目/容器时，给每个项目分配独立端口，
   并把端口写进配置而不是散落在命令和脚本里。

## 7. 事件：换成计划任务后仍然打不开（2026-10-08 早晨）

### 现象

昨晚 22:39 站点还正常（`http://localhost:8510`），今早打开却连不上；看护任务
`BigFishSite` 在 08:00 跑过，但**失败**。

### 三个叠加原因

| # | 原因 | 证据 |
|---|---|---|
| 1 | 站点仍是**从 Codex 会话里启动的**（我用 `ensure_site.py` 手动拉起过），会话/回合一结束就被回收 | `output/site.log` 最后写入时间 23:16（正是上一轮结束的时刻），此后再无输出 |
| 2 | 计划任务**默认不允许电池供电时运行**，笔记本拔电/省电时任务被直接拒绝 | `schtasks /Query /V` → `Last Result: -2147020576` = "操作员或系统管理员拒绝了请求" |
| 3 | 即使由任务启动，子进程仍可能随调用者的作业对象（Job Object）被回收 | 进程树里站点挂在调用者之下 |

### 修复：让任务自己承载站点 + 每 1 分钟看护

| 任务 | 触发 | 作用 |
|---|---|---|
| `BigFishSiteRun` | 每天 00:05 + 按需 `schtasks /Run` | **站点载体**：任务进程即网站（`start_site.py --log-to-file`），`RestartOnFailure` 自动重启，`MultipleInstances=IgnoreNew` |
| `BigFishSite` | 每 1 分钟 | `ensure_site.py`：健康检查不过就用 `schtasks /Run` 唤醒载体任务 |

四个任务统一改成：`AllowStartIfOnBatteries = true`、`DontStopIfGoingOnBatteries = true`、
`ExecutionTimeLimit = PT0S`（不限时）、`StartWhenAvailable = true`、`MultipleInstances = IgnoreNew`。

设置方式（schtasks 命令行**没有**电池/重启这些开关，必须用 PowerShell）：

```powershell
$t = Get-ScheduledTask -TaskName BigFishSiteRun
$s = $t.Settings
$s.AllowStartIfOnBatteries = $true; $s.DontStopIfGoingOnBatteries = $true
$s.ExecutionTimeLimit = 'PT0S'; $s.StartWhenAvailable = $true; $s.MultipleInstances = 'IgnoreNew'
Set-ScheduledTask -TaskName BigFishSiteRun -Settings $s
```

> 注意：`ONLOGON` 触发需要管理员权限（`schtasks /Create ... /SC ONLOGON` → Access is denied），
> 所以载体任务用"每天 00:05 + 按需唤醒"的组合，1 分钟看护会覆盖登录后的场景。

### 验证（本次实测）

```
停止旧站点（pid 2328）→ 健康检查 False
schtasks /Run /TN BigFishSiteRun → 健康检查 True，监听 pid 16820
进程链：python.exe(16820) ← python.exe(1140, start_site.py) ← svchost.exe(1560, 任务计划服务) ← services.exe
```

进程挂在服务进程下，**不再属于任何终端/Codex 会话**，也不会随其退出而消失。

### 经验教训（补充）

8. **看护脚本自己也会被回收**：判断"常驻是否真的常驻"，要看**进程链的父进程是谁**
   （`Get-CimInstance Win32_Process` 的 `ParentProcessId`），挂在 `svchost.exe` 下才算脱钩。
9. **计划任务的默认设置会坑人**：电池条件、运行时限、实例策略三项默认值都可能让任务"静默失败"，
   排查时先看 `Last Result`（`-2147020576` = 被拒绝、`267011` = 从未运行）。

## 8. 事件：桌面每分钟闪出一个黑色终端窗口（2026-10-08 上午）

### 现象

`BigFishSite` 每 1 分钟触发一次，而它的动作是**用带控制台的 `python.exe` 直接跑脚本**，
计划任务的登录方式又是"只使用交互方式"。于是每次触发都会新建一个可见的控制台窗口
（本机默认终端是 Windows Terminal），显示完一行结果后关闭 —— 桌面上看起来就是每分钟闪一次。

### 实测证据（窗口级监控，3 分钟）

```
09:20:01 WIN-OPEN title="Terminal"             class=CASCADIA_HOSTING_WINDOW_CLASS
09:21:01 WIN-OPEN title="C:\Python314\python.exe"  class=CASCADIA_HOSTING_WINDOW_CLASS
09:22:01 WIN-OPEN title="C:\Python314\python.exe"  class=CASCADIA_HOSTING_WINDOW_CLASS
同时刻 PROC python.exe ... "scripts\ensure_site.py"  parent=svchost.exe(任务计划服务)
```

### 修复：所有任务动作都走"隐藏窗口"启动器

新增 `scripts\run_hidden.vbs`：用 `WScript.Shell.Run(cmd, 0, True)` 以**隐藏窗口 + 等待**
的方式启动真实命令。控制台仍然存在（所以子进程继承的是这个隐藏控制台，不会再自己弹窗），
只是永远不显示；`True` 表示等待，计划任务的实例策略（`IgnoreNew`）因此仍然有效。

四个任务的动作统一改成：

```
wscript.exe //nologo "D:\GPT\基本面反转交易策略\scripts\run_hidden.vbs" "C:\Python314\python.exe" "<脚本>" [参数]
```

另外给 `ensure_site.py` 的兜底拉起（`DETACHED_PROCESS`）补上 `CREATE_NO_WINDOW`，
否则那条路径（父进程没有控制台）会自己弹一个窗口出来。重建任务的脚本 `tmp\setup_tasks.py`
同步改成隐藏启动，避免以后重新注册任务时又变回"每分钟闪一次"。

> 注意：PowerShell 5.1 读取 `.ps1` 时按 ANSI 解码，脚本里写中文路径会变成乱码。
> 本次踩过这个坑（任务动作里的路径被写成乱码），所以改任务的脚本改成
> **只用 ASCII**，中文项目目录通过 `Get-ChildItem` 动态定位。

### 验证

```
改前：3 分钟内窗口事件 4 个（其中 3 个是任务触发的闪窗）
改后：同样 3 分钟，窗口事件 0 个；BigFishSite 上次结果 = 0（看护仍正常工作）
     BigFishSiteRun 重启后站点 3 秒内恢复：/_stcore/health = 200 ok，控制台 visible=False
```
