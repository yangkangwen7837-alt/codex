"""数据更新服务：抓数 → 跑批 → 观察池跟踪，带文件锁与状态记录。

网站按钮、调度器、命令行三处共用同一个 `UpdateService`，行为与状态完全一致；
文件锁保证同一时刻只有一个更新在跑（重复触发会直接返回"已在运行"）。
"""
from __future__ import annotations

import json
import os
import time
import traceback
from datetime import datetime
from pathlib import Path

from .. import OUTPUT_DIR, PROCESSED_DIR
from ..config import load_settings

STATUS_FILE = PROCESSED_DIR / "update_status.json"
HISTORY_FILE = PROCESSED_DIR / "update_history.json"
SETTINGS_FILE = PROCESSED_DIR / "update_settings.json"
LOCK_FILE = PROCESSED_DIR / "update.lock"
LOCK_TTL_SECONDS = 3600          # 超过 1 小时的锁视为僵尸锁


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_status() -> dict:
    return _read_json(STATUS_FILE, {"state": "idle", "steps": [], "message": "尚未运行过更新"})


def load_history(limit: int = 30) -> list[dict]:
    rows = _read_json(HISTORY_FILE, [])
    return rows[-limit:][::-1]


def load_update_settings() -> dict:
    settings = load_settings()
    default = {
        "auto_enabled": bool(settings.path("update.auto_enabled", True)),
        "steps": list(settings.path("update.steps", ["fetch", "daily", "flicker"])),
    }
    stored = _read_json(SETTINGS_FILE, {})
    default.update({k: v for k, v in stored.items() if k in default})
    return default


def save_update_settings(**kwargs) -> dict:
    current = load_update_settings()
    current.update({k: v for k, v in kwargs.items() if v is not None})
    _write_json(SETTINGS_FILE, current)
    return current


def _lock_active() -> bool:
    if not LOCK_FILE.exists():
        return False
    age = time.time() - LOCK_FILE.stat().st_mtime
    if age > LOCK_TTL_SECONDS:
        LOCK_FILE.unlink(missing_ok=True)
        return False
    return True


class UpdateService:
    """一次更新 = 若干步骤顺序执行；每一步都记录耗时与结果。"""

    def __init__(self, settings=None) -> None:
        self.settings = settings or load_settings()

    # ------------------------------------------------------------------
    def _step_fetch(self) -> str:
        from ..adapters import TushareAdapter
        from ..pipeline.fetch import run_fetch
        from ..storage import ParquetStore

        adapter = TushareAdapter(store=ParquetStore(),
                                 workers=int(self.settings.path("data.workers", 4)))
        manifest = run_fetch(self.settings, adapter)
        return (f"交易日 {manifest['trade_days']} 天，最新 {manifest['latest_trade_date']}，"
                f"缺失项 {len(manifest['missing'])} 条")

    def _step_daily(self) -> str:
        from ..pipeline.run import run_all

        result = run_all(self.settings)
        summary = result["summary"]
        return (f"市场 {summary['regime']}；观察池 {summary.get('watchlist_rows')} 只 / "
                f"重点 {summary.get('ranking_rows')} 只 / 早期线索 {summary.get('early_rows')} 只")

    def _step_flicker(self) -> str:
        from .. import PROJECT_ROOT

        # 直接调用脚本里的逻辑，避免起子进程
        import importlib.util

        script = PROJECT_ROOT / "scripts" / "watchlist_flicker.py"
        spec = importlib.util.spec_from_file_location("watchlist_flicker", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        status = _read_json(PROCESSED_DIR / "flicker_status.json", {})
        days = status.get("days", 0)
        return f"观察池历史 {days} 个交易日；闪烁率 {status.get('flicker', float('nan')):.0%}" \
            if days else "观察池历史为空"

    _STEPS = {"fetch": ("增量抓数", "_step_fetch"),
              "daily": ("全链路跑批", "_step_daily"),
              "flicker": ("观察池闪烁率跟踪", "_step_flicker")}

    # ------------------------------------------------------------------
    def run(self, steps: list[str] | None = None, trigger: str = "manual") -> dict:
        """执行一次更新。返回状态 dict（同时写入 update_status.json）。"""
        if _lock_active():
            status = load_status()
            status.update({"state": "running", "message": "已有更新在运行中，本次忽略"})
            return status

        steps = steps or load_update_settings()["steps"]
        steps = [s for s in steps if s in self._STEPS]
        LOCK_FILE.write_text(f"{os.getpid()} {_now()}", encoding="utf-8")
        started = time.time()
        status = {
            "state": "running",
            "trigger": trigger,
            "started_at": _now(),
            "finished_at": None,
            "steps": [{"key": s, "label": self._STEPS[s][0], "state": "pending"} for s in steps],
            "message": "更新进行中",
        }
        _write_json(STATUS_FILE, status)
        try:
            for i, key in enumerate(steps):
                label, method = self._STEPS[key]
                status["steps"][i]["state"] = "running"
                status["message"] = f"正在执行：{label}"
                _write_json(STATUS_FILE, status)
                t0 = time.time()
                detail = getattr(self, method)()
                status["steps"][i].update({"state": "done", "detail": detail,
                                           "seconds": round(time.time() - t0, 1)})
                _write_json(STATUS_FILE, status)
            status.update({"state": "done", "message": "更新完成",
                           "seconds": round(time.time() - started, 1), "finished_at": _now()})
        except Exception as exc:  # noqa: BLE001
            failed = next((s for s in status["steps"] if s["state"] == "running"), None)
            if failed is not None:
                failed.update({"state": "failed", "detail": str(exc)[:400],
                               "traceback": traceback.format_exc()[-1500:]})
            status.update({"state": "failed", "message": f"更新失败：{exc}",
                           "finished_at": _now(), "seconds": round(time.time() - started, 1)})
        finally:
            LOCK_FILE.unlink(missing_ok=True)
        _write_json(STATUS_FILE, status)

        history = _read_json(HISTORY_FILE, [])
        history.append({k: status[k] for k in ("state", "trigger", "started_at", "finished_at",
                                               "seconds", "message")})
        keep = int(self.settings.path("update.keep_history", 30))
        _write_json(HISTORY_FILE, history[-keep:])
        return status

    # ------------------------------------------------------------------
    def data_snapshot(self) -> dict:
        """当前数据集的时间戳与行数（网站用于判断是否需要重绘）。"""
        snapshot: dict[str, dict] = {}
        for name in ("bigfish_score_latest.parquet", "bigfish_history.parquet",
                     "flicker_status.json", "run_summary.json"):
            path = PROCESSED_DIR / name
            if path.exists():
                snapshot[name] = {"mtime": int(path.stat().st_mtime),
                                  "size": int(path.stat().st_size)}
        for path in sorted(OUTPUT_DIR.glob("daily_brief_*.md"))[-1:]:
            snapshot[path.name] = {"mtime": int(path.stat().st_mtime)}
        return snapshot
