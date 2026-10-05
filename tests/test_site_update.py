"""网站与自动更新模块测试（不需要网络）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bigfish.update import service as S  # noqa: E402
from bigfish.update.scheduler import scheduler_status  # noqa: E402


def test_update_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "SETTINGS_FILE", tmp_path / "update_settings.json")
    assert S.load_update_settings()["auto_enabled"] is True
    S.save_update_settings(auto_enabled=False)
    assert S.load_update_settings()["auto_enabled"] is False
    S.save_update_settings(auto_enabled=True)
    assert S.load_update_settings()["auto_enabled"] is True


def test_update_status_default_shape():
    status = S.load_status()
    assert "state" in status and "message" in status


def test_lock_prevents_concurrent_run(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "LOCK_FILE", tmp_path / "update.lock")
    monkeypatch.setattr(S, "STATUS_FILE", tmp_path / "status.json")
    S.LOCK_FILE.write_text("99999 stale-owner", encoding="utf-8")
    status = S.UpdateService().run(steps=["flicker"], trigger="test")
    assert status["state"] == "running" and "已有更新在运行中" in status["message"]


def test_scheduler_status_shape():
    status = scheduler_status()
    assert set(status) >= {"running", "auto_enabled", "jobs"}
    assert isinstance(status["jobs"], list)


def test_pool_filter_excludes_untradeable():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps"))
    import common as C  # noqa: E402

    frame = pd.DataFrame({
        "ts_code": ["A", "B", "C"],
        "grade": ["A", "Reject", "B"],
        "tradeable": [True, True, False],
        "risk_block": [False, False, False],
        "action": ["WAIT_TRIGGER", "WATCH", "WAIT_TRIGGER"],
    })
    out = C.pool(frame)
    assert list(out["ts_code"]) == ["A"]
