"""安装/修复 BigFish 的 Windows 计划任务（幂等，可重复运行）。

四个任务：
    BigFishSiteRun   站点载体（每天 00:05 + 按需唤醒）——任务进程本身就是网站
    BigFishSite      看护（每 1 分钟）——健康检查不过就唤醒上面的载体
    BigFishUpdateAM  工作日 08:30 抓数 + 跑批
    BigFishUpdatePM  工作日 16:30 抓数 + 跑批 + 发布快照到 GitHub

为什么需要这个脚本：
    `schtasks /Create` 建出来的任务**默认不允许电池供电运行**，笔记本一旦用电池，
    任务会被直接拒绝（Last Result = -2147020576）。而这些电源/实例/时限设置
    在每次重建任务时都会被重置，所以统一放在这里，重建后跑一次即可恢复。

用法：
    python scripts/install_tasks.py              # 只修设置（任务已存在时不动动作）
    python scripts/install_tasks.py --recreate   # 重建四个任务（动作 + 设置都重来）
    python scripts/install_tasks.py --status     # 只看现状
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
LAUNCHER = ROOT / "scripts" / "run_hidden.vbs"
TASKS = ("BigFishSiteRun", "BigFishSite", "BigFishUpdateAM", "BigFishUpdatePM")


def run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def task_action(script: str, extra: str = "") -> str:
    """统一用隐藏窗口启动器，避免计划任务每次触发都闪一个黑窗。"""
    tail = f" {extra}" if extra else ""
    return (f'wscript.exe //nologo "{LAUNCHER}" "{PYTHON}" '
            f'"{ROOT / "scripts" / script}"{tail}')


def definitions() -> list[tuple[str, str, list[str]]]:
    """(任务名, 动作命令行, 触发器 XML)。"""
    daily = ('<CalendarTrigger><StartBoundary>2026-01-01T00:05:00</StartBoundary><Enabled>true</Enabled>'
             '<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>')
    every_minute = ('<TimeTrigger><StartBoundary>2026-01-01T00:00:00</StartBoundary><Enabled>true</Enabled>'
                    '<Repetition><Interval>PT1M</Interval><StopAtDurationEnd>false</StopAtDurationEnd>'
                    '</Repetition></TimeTrigger>')
    weekdays = ('<CalendarTrigger><StartBoundary>2026-01-01T{time}:00</StartBoundary><Enabled>true</Enabled>'
                '<ScheduleByWeek><DaysOfWeek><Monday/><Tuesday/><Wednesday/><Thursday/><Friday/>'
                '</DaysOfWeek><WeeksInterval>1</WeeksInterval></ScheduleByWeek></CalendarTrigger>')
    return [
        ("BigFishSiteRun", task_action("start_site.py", "--port 8510 --log-to-file"), daily),
        ("BigFishSite", task_action("ensure_site.py"), every_minute),
        ("BigFishUpdateAM", task_action("run_update_once.py"), weekdays.format(time="08:30")),
        ("BigFishUpdatePM", task_action("run_update_once.py", "--publish"), weekdays.format(time="16:30")),
    ]


XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>BigFish 基本信息反转 Agent：网站常驻 / 看护 / 数据更新（由 scripts/install_tasks.py 生成）</Description>
    <Author>BigFish</Author>
  </RegistrationInfo>
  <Triggers>{triggers}</Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    {restart}
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>wscript.exe</Command>
      <Arguments>{arguments}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def register(name: str, action: str, triggers: str) -> tuple[bool, str]:
    """用 XML 注册任务：这样才能可靠地写死"允许电池供电、不限时长"等设置。"""
    arguments = action[len("wscript.exe "):]          # XML 里 Command/Arguments 分开写
    restart = ("<RestartOnFailure><Interval>PT1M</Interval><Count>5</Count></RestartOnFailure>"
               if name == "BigFishSiteRun" else "")
    xml = XML_TEMPLATE.format(triggers=triggers, arguments=arguments, restart=restart)
    tmp = ROOT / "tmp" / f"task_{name}.xml"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(xml, encoding="utf-16")            # schtasks 需要 UTF-16 的 XML
    proc = run(["schtasks", "/Create", "/TN", name, "/XML", str(tmp), "/F"])
    detail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    return proc.returncode == 0, (detail[0] if detail else "")


def recreate() -> int:
    rc = 0
    for name, action, triggers in definitions():
        ok, detail = register(name, action, triggers)
        rc |= 0 if ok else 1
        print(f"  {'OK ' if ok else 'FAIL'} 注册 {name}：{detail[:90]}")
    return rc


def status() -> None:
    for name in TASKS:
        proc = run(["schtasks", "/Query", "/TN", name, "/XML"])
        xml = proc.stdout or ""
        disallow = re.search(r"<DisallowStartIfOnBatteries>([^<]*)</DisallowStartIfOnBatteries>", xml)
        stop = re.search(r"<StopIfGoingOnBatteries>([^<]*)</StopIfGoingOnBatteries>", xml)
        limit = re.search(r"<ExecutionTimeLimit>([^<]*)</ExecutionTimeLimit>", xml)
        query = run(["schtasks", "/Query", "/TN", name, "/FO", "LIST", "/V"])
        last = next((ln.strip() for ln in (query.stdout or "").splitlines()
                     if ln.strip().startswith("Last Result")), "")
        allowed = (disallow and disallow.group(1).lower() == "false") and (stop and stop.group(1).lower() == "false")
        print(f"  {name:<18} 允许电池={'是' if allowed else '否/未设置'} "
              f"时限={limit.group(1) if limit else '—'}  {last[:52]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="安装/修复 BigFish 计划任务")
    parser.add_argument("--recreate", action="store_true", help="重建任务（动作+触发器）")
    parser.add_argument("--status", action="store_true", help="只看现状")
    args = parser.parse_args()

    if not LAUNCHER.exists():
        print(f"缺少隐藏启动器 {LAUNCHER}，先恢复该文件再运行本脚本。")
        return 1

    if args.status:
        print("当前任务状态：")
        status()
        return 0

    rc = 0
    print("注册任务（含电源 / 时限 / 实例策略设置）：")
    rc |= recreate()
    print("最终状态：")
    status()
    return rc


if __name__ == "__main__":
    sys.exit(main())
