"""自动更新调度器：APScheduler + 文件锁，网站进程与独立进程共用。"""
from __future__ import annotations

import logging
import threading
import json
import os
import time
from datetime import datetime
from pathlib import Path

from .. import PROCESSED_DIR
from ..config import load_settings
from .service import UpdateService, load_update_settings

log = logging.getLogger(__name__)
_SCHEDULER = None
_LOCK = threading.Lock()
HEARTBEAT_FILE = PROCESSED_DIR / "scheduler_heartbeat.json"
HEARTBEAT_STALE_SECONDS = 900      # 15 分钟没心跳视为调度器已停


def _write_heartbeat(scheduler) -> None:
    """把调度器状态写到文件，供网站（独立进程）展示。"""
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "name": job.name,
            "next_run": job.next_run_time.astimezone().strftime("%Y-%m-%d %H:%M")
            if job.next_run_time else None,
        })
    payload = {"pid": os.getpid(), "updated_at": time.time(),
               "updated_at_text": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
               "jobs": jobs}
    HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def start_scheduler(settings=None, force: bool = False):
    """启动后台调度器（幂等）。返回 APScheduler 实例，失败时返回 None。"""
    global _SCHEDULER  # noqa: PLW0603
    settings = settings or load_settings()
    with _LOCK:
        if _SCHEDULER is not None and not force:
            return _SCHEDULER
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger
        except Exception as exc:  # noqa: BLE001
            log.warning("APScheduler 不可用，自动更新关闭：%s", exc)
            return None

        timezone = str(settings.path("update.timezone", "Asia/Shanghai"))
        scheduler = BackgroundScheduler(timezone=timezone)
        jobs = settings.path("update.jobs", []) or []
        for job in jobs:
            if not (job.get("enabled", True) if hasattr(job, "get") else True):
                continue
            name = str(job.get("name"))
            hour, minute = str(job.get("time", "16:30")).split(":")
            days = str(job.get("days", "mon-fri"))
            day_map = {"mon-fri": "mon,tue,wed,thu,fri", "mon-sat": "mon,tue,wed,thu,fri,sat",
                       "daily": "*"}
            scheduler.add_job(
                _run_job, CronTrigger(day_of_week=day_map.get(days, days), hour=int(hour),
                                      minute=int(minute), timezone=timezone),
                id=f"bigfish-{name}", name=name, replace_existing=True,
                kwargs={"label": name},
            )
        scheduler.start()
        _SCHEDULER = scheduler
        _write_heartbeat(scheduler)
        # 每 5 分钟刷新一次心跳（同时更新下次执行时间）
        scheduler.add_job(lambda: _write_heartbeat(scheduler), "interval", minutes=5,
                          id="bigfish-heartbeat", replace_existing=True)
        log.info("自动更新调度器已启动：%s", [j.get("name") for j in jobs])
        return scheduler


def _run_job(label: str = "scheduled") -> None:
    if not load_update_settings().get("auto_enabled", True):
        log.info("自动更新已关闭，跳过本次 %s", label)
        return
    log.info("自动更新触发：%s", label)
    UpdateService().run(trigger=f"auto:{label}")


def scheduler_status() -> dict:
    """当前调度器状态（供网站展示）。"""
    auto_enabled = bool(load_update_settings().get("auto_enabled", True))
    if _SCHEDULER is None:
        # 可能是独立进程（start_site.py / run_scheduler.py）在跑 → 读心跳
        if HEARTBEAT_FILE.exists():
            try:
                payload = json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                payload = None
            if payload and time.time() - float(payload.get("updated_at", 0)) < HEARTBEAT_STALE_SECONDS:
                return {"running": True, "auto_enabled": auto_enabled, "jobs": payload.get("jobs", []),
                        "now": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                        "pid": payload.get("pid"), "external": True}
        return {"running": False, "auto_enabled": auto_enabled, "jobs": []}
    jobs = []
    for job in _SCHEDULER.get_jobs():
        jobs.append({
            "name": job.name,
            "next_run": job.next_run_time.astimezone().strftime("%Y-%m-%d %H:%M")
            if job.next_run_time else None,
        })
    return {"running": True, "auto_enabled": auto_enabled, "jobs": jobs,
            "now": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")}
