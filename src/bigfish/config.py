"""配置加载：所有阈值/权重来自 configs/*.yaml，代码不写死参数。"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from . import CONFIG_DIR, PROJECT_ROOT


class Settings:
    """只读配置树，支持 settings["a"]["b"] 与 settings.path("a.b")。"""

    def __init__(self, data: dict[str, Any], source: Path | None = None) -> None:
        self._data = data
        self.source = source

    def __getitem__(self, key: str) -> Any:
        value = self._data[key]
        return Settings(value, self.source) if isinstance(value, dict) else value

    def get(self, key: str, default: Any = None) -> Any:
        value = self._data.get(key, default)
        return Settings(value, self.source) if isinstance(value, dict) else value

    def path(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return Settings(node, self.source) if isinstance(node, dict) else node

    def as_dict(self) -> dict[str, Any]:
        return self._data

    def keys(self):
        return self._data.keys()

    def items(self):
        return self._data.items()

    def values(self):
        return self._data.values()

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key) -> bool:
        return key in self._data

    def to_yaml(self) -> str:
        return yaml.safe_dump(self._data, allow_unicode=True, sort_keys=False)


_CACHE: dict[str, Settings] = {}
_TEMPLATE_CACHE: dict[str, Settings] = {}


def load_settings(path: str | Path | None = None) -> Settings:
    if path is None:
        path = os.environ.get("BIGFISH_CONFIG", str(CONFIG_DIR / "default.yaml"))
    key = str(Path(path).resolve())
    if key not in _CACHE:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        _CACHE[key] = Settings(data, Path(path))
    return _CACHE[key]


def load_indicator_templates(settings: Settings | None = None) -> Settings:
    """行业前瞻指标模板（规格书第 16 节）。"""
    settings = settings or load_settings()
    rel = str(settings.path("lis.templates_file", "configs/industry_leading_indicators.yaml"))
    path = Path(rel)
    if not path.is_absolute():
        path = PROJECT_ROOT / rel
    key = str(path.resolve())
    if key not in _TEMPLATE_CACHE:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        _TEMPLATE_CACHE[key] = Settings(data, path)
    return _TEMPLATE_CACHE[key]


def resolve_end_date(settings: Settings | None = None, explicit: str | None = None) -> str:
    """数据区间右端：``data.end_date`` 为 ``auto`` 时取今天。

    自动更新的前提是区间会随日期推进；写死 end_date 会让跑批永远重算同一天。
    """
    settings = settings or load_settings()
    value = explicit or str(settings.path("data.end_date", "auto"))
    return datetime.now().strftime("%Y%m%d") if value.lower() in ("auto", "today", "") else value


# ---------------------------------------------------------------------------
# 敏感配置统一入口：st.secrets → 环境变量 → 默认值
# 所有 token / 数据库口令都必须通过这里读取，禁止硬编码。
# ---------------------------------------------------------------------------
def get_secret(name: str, default: str | None = None, *, section: str | None = None) -> str | None:
    """按优先级读取敏感配置：Streamlit secrets → 环境变量 → 默认值。

    Streamlit Community Cloud 用 secrets.toml（在 App settings → Secrets 里填写）；
    本地 CLI / 调度器用环境变量。两者共用同一份代码。
    """
    try:
        import streamlit as st

        if section:
            node = st.secrets.get(section)
            if node is not None and name in node:
                return str(node[name])
        elif name in st.secrets:
            return str(st.secrets[name])
    except Exception:  # noqa: BLE001 - 非 Streamlit 环境（CLI/调度器）降级到环境变量
        pass
    value = os.getenv(name)
    return value if value not in (None, "") else default


def is_streamlit_cloud() -> bool:
    """是否运行在 Streamlit Community Cloud（云上不启动本地调度器、不写数据）。"""
    if os.getenv("BIGFISH_FORCE_CLOUD", "").lower() in ("1", "true", "yes"):
        return True
    if os.getenv("BIGFISH_DISABLE_SCHEDULER") == "1":
        return False
    markers = (os.getenv("STREAMLIT_SHARING_MODE", ""), os.getenv("STREAMLIT_CLOUD", ""),
               os.getenv("IS_STREAMLIT_CLOUD", ""))
    if any(str(m).strip() for m in markers):
        return True
    return Path("/mount/src").exists()      # 官方容器挂载点特征
