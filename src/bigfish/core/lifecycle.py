"""生命周期状态机（规格书第 36~43 节）。"""
from __future__ import annotations

import numpy as np
import pandas as pd


STAGES = ["DISCOVERED", "FUNDAMENTAL_WATCH", "EARLY_REVERSAL", "TURNAROUND_CONFIRMED",
          "PRICE_CONFIRMED", "ACCELERATION", "MATURE", "EXIT"]


def classify_stage(row: pd.Series, settings) -> str:
    frs = row.get("frs", np.nan)
    lis = row.get("lis", np.nan)
    pcs = row.get("pcs", np.nan)
    bfs = row.get("bfs", np.nan)
    rps = row.get("rps", np.nan)
    kill_level = row.get("kill_level", "NORMAL")

    if kill_level in ("EXIT",):
        return "EXIT"
    if not np.isfinite(frs):
        return "DISCOVERED"
    if np.isfinite(bfs) and bfs >= float(settings.path("lifecycle.mature.bfs_min", 75)) \
            and np.isfinite(rps) and rps <= float(settings.path("lifecycle.mature.rps_max", 30)):
        return "MATURE"
    acc = settings.path("lifecycle.acceleration", {}) or {}
    if (frs >= float(acc.get("frs_min", 70)) and np.isfinite(lis) and lis >= float(acc.get("lis_min", 65))
            and np.isfinite(pcs) and pcs >= float(acc.get("pcs_min", 65))
            and bool(row.get("acceleration_confirm", False))):
        return "ACCELERATION"
    pc = settings.path("lifecycle.price_confirmed", {}) or {}
    if frs >= float(pc.get("frs_min", 70)) and np.isfinite(pcs) and pcs >= float(pc.get("pcs_min", 65)):
        return "PRICE_CONFIRMED"
    tc = settings.path("lifecycle.turnaround_confirmed", {}) or {}
    if frs >= float(tc.get("frs_min", 70)) and np.isfinite(lis) and lis >= float(tc.get("lis_min", 65)):
        return "TURNAROUND_CONFIRMED"
    er = settings.path("lifecycle.early_reversal", {}) or {}
    if frs >= float(er.get("frs_min", 60)):
        return "EARLY_REVERSAL"
    fw = settings.path("lifecycle.fundamental_watch", {}) or {}
    if frs >= float(fw.get("frs_min", 50)):
        return "FUNDAMENTAL_WATCH"
    return "DISCOVERED"
