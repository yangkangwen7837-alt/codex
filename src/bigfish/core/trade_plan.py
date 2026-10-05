"""交易计划与动作（规格书第 62~65 节）。

动作集合严格限定为：RESEARCH / WATCH / WAIT_TRIGGER / STARTER_POSITION /
ADD_ON_CONFIRMATION / HOLD / REDUCE / EXIT / REJECT / WAIT_PULLBACK。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def build_trade_plan(row: pd.Series, settings) -> dict:
    close = row.get("close", np.nan)
    atr = row.get("atr14", np.nan)
    ma20 = row.get("ma20", np.nan)
    ma60 = row.get("ma60", np.nan)
    frs = row.get("frs", np.nan)
    pcs = row.get("pcs", np.nan)
    bfs = row.get("bfs", np.nan)
    kill_level = row.get("kill_level", "NORMAL")
    stage = row.get("stage", "DISCOVERED")

    starter = settings.path("action.starter_position", {}) or {}
    chase_limit = float(settings.path("pcs.chase_limit", 1.15))

    stop = np.nan
    target1 = np.nan
    target2 = np.nan
    rr = np.nan
    if np.isfinite(close) and np.isfinite(atr) and atr > 0:
        stop = float(close - 2.0 * atr)
        target1 = float(close + 3.0 * atr)
        target2 = float(close + 6.0 * atr)
        risk = close - stop
        rr = float((target1 - close) / risk) if risk > 0 else np.nan

    action = "RESEARCH"
    if kill_level == "EXIT":
        action = "EXIT"
    elif kill_level == "REDUCE":
        action = "REDUCE"
    elif stage == "MATURE":
        action = "HOLD"
    elif (np.isfinite(frs) and np.isfinite(pcs) and np.isfinite(bfs)
          and frs >= float(starter.get("frs_min", 70)) and pcs >= float(starter.get("pcs_min", 65))
          and bfs >= float(starter.get("bfs_min", 75))):
        action = "STARTER_POSITION"
    elif stage in ("PRICE_CONFIRMED", "ACCELERATION"):
        action = "WAIT_TRIGGER"
    elif stage == "TURNAROUND_CONFIRMED":
        action = "WAIT_TRIGGER"
    elif stage == "EARLY_REVERSAL":
        action = "WATCH"
    elif stage == "FUNDAMENTAL_WATCH":
        action = "WATCH"

    # 规格书第 65 节：禁止追高
    if np.isfinite(close) and np.isfinite(ma20) and ma20 > 0 and close / ma20 > chase_limit:
        if action in ("STARTER_POSITION", "ADD_ON_CONFIRMATION"):
            action = "WAIT_PULLBACK"
    if row.get("is_fake_turnaround", False) or row.get("is_value_trap", False):
        action = "REJECT" if action in ("STARTER_POSITION", "ADD_ON_CONFIRMATION") else action
    # 规格书第 82 节：Risk Block 一律不得进入交易候选
    if row.get("risk_block", False):
        action = "REJECT"
    # 回测驱动的硬约束：估值偏高 / 低基数假反转 → 不得进试探仓，只能等更好的价格或证据
    if row.get("expensive_entry", False):
        if action in ("STARTER_POSITION", "ADD_ON_CONFIRMATION"):
            action = "WAIT_TRIGGER"
    # 金融行业：专用模板仍无法验证排序能力 → 禁止试探仓
    if str(row.get("frs_template", "")).upper() == "FINANCIAL" and action == "STARTER_POSITION":
        action = "WAIT_TRIGGER"

    return {
        "entry_ref": close,
        "stop_loss": stop,
        "target_1": target1,
        "target_2": target2,
        "risk_reward": rr,
        "action": action,
        "position_hint": _position_hint(action),
    }


def _position_hint(action: str) -> str:
    return {
        "STARTER_POSITION": "试探仓（≤ 单票上限的 1/3），突破确认后加仓",
        "ADD_ON_CONFIRMATION": "加仓（需价格突破 + 成交量确认）",
        "WAIT_PULLBACK": "等回踩 MA20/结构位，不追高",
        "WAIT_TRIGGER": "等触发条件（突破 / 放量 / 财报确认）",
        "HOLD": "持有但警惕估值空间收窄",
        "WATCH": "观察，不建仓",
        "RESEARCH": "研究，不进入交易候选",
        "REDUCE": "减仓（触发 Kill Signal 降级）",
        "EXIT": "退出（触发 Kill Signal 硬条件）",
        "REJECT": "剔除（风险/假反转/价值陷阱）",
    }.get(action, "")
