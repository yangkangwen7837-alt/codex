"""Big Fish Card（规格书第 58~61 节）。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _fmt(value, pct: bool = False, digits: int = 1, suffix: str = "") -> str:
    if value is None:
        return "DATA INCOMPLETE"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "DATA INCOMPLETE"
    if not np.isfinite(num):
        return "DATA INCOMPLETE"
    if pct:
        return f"{num * 100:.{digits}f}%"
    return f"{num:.{digits}f}{suffix}"


def render_card(row: pd.Series, rank: int | None = None) -> str:
    title = f"{row.get('name', '')}（{row.get('ts_code', '')}）"
    header = f"## {f'#{rank} ' if rank else ''}{title}"
    lines = [header, ""]
    lines.append(f"- **行业**：{row.get('industry', 'DATA INCOMPLETE')}")
    lines.append(f"- **Big Fish Score**：{_fmt(row.get('bfs'), digits=1)} "
                 f"（FRS {_fmt(row.get('frs'), digits=1)} / LIS {_fmt(row.get('lis'), digits=1)} / "
                 f"PCS {_fmt(row.get('pcs'), digits=1)} / ODS {_fmt(row.get('ods'), digits=1)} / "
                 f"RPS {_fmt(row.get('rps'), digits=1)} / CS {_fmt(row.get('catalyst'), digits=1)}）")
    lines.append(f"- **抓鱼分 / 避雷分**：{_fmt(row.get('upside_score'), digits=1)} / "
                 f"{_fmt(row.get('downside_score'), digits=1)}"
                 f"（避雷分分位实测：Q5 踩雷率 9.1% vs Q1 14.0%，见 docs/11）")
    lines.append(f"- **通道 / 连续在榜**：{row.get('channel', 'CORE')} / "
                 f"{int(row.get('streak', 1) or 1)} 期"
                 f"（同行业第 {int(row.get('ind_seq', 1) or 1)} 只、同反转类型第 "
                 f"{int(row.get('type_seq', 1) or 1)} 只——注意力配额请自行把握）")
    lines.append(f"- **评级 / 生命周期 / 象限**：{row.get('grade')} / {row.get('stage')} / {row.get('quadrant')}")
    lines.append(f"- **反转类型**：{row.get('reversal_type')}")
    lines.append(f"- **交易状态**：{row.get('action')} —— {row.get('position_hint', '')}")
    lines.append("")
    lines.append("**【为什么出现反转】**")
    lines.append(f"{row.get('frs_reasons', 'DATA INCOMPLETE')}")
    lines.append("")
    lines.append("**【核心基本面变化】**")
    lines.append(
        f"- 单季收入同比 {_fmt(row.get('f_revenue_yoy'), pct=True)}，"
        f"单季归母同比 {_fmt(row.get('f_np_yoy'), pct=True)}"
    )
    lines.append(
        f"- 单季毛利率 {_fmt(row.get('f_gross_margin'), pct=True)}，"
        f"净利率 {_fmt(row.get('f_net_margin'), pct=True)}"
    )
    lines.append(
        f"- 存货/收入 {_fmt(row.get('f_inventory_to_revenue'), pct=True)}，"
        f"应收同比 {_fmt(row.get('f_receivable_yoy'), pct=True)}，"
        f"合同负债同比 {_fmt(row.get('f_contract_liab_yoy'), pct=True)}，"
        f"资产负债率 {_fmt(row.get('f_debt_to_assets'), pct=True)}"
    )
    lines.append(f"- 最新报告期 {row.get('latest_period')}（公告日 {row.get('latest_ann_date')}，"
                 f"可用季度数 {row.get('quarters_available')}）")
    lines.append("")
    lines.append("**【领先指标】**")
    lines.append(
        f"- LIS {_fmt(row.get('lis'), digits=1)}（模板 {row.get('lis_template')}）"
        f"，订单/需求 {_fmt(row.get('lis_orders_demand'), digits=1)}"
        f"，产品价格 {_fmt(row.get('lis_product_price'), digits=1)}"
        f"，库存周期 {_fmt(row.get('lis_inventory_cycle'), digits=1)}"
        f"，合同负债 {_fmt(row.get('lis_contract_liability'), digits=1)}"
    )
    lines.append(f"- 领先一致性（合同负债增速领先收入）：{bool(row.get('lis_lead_consistency', False))}")
    lines.append(f"- 数据缺口：{row.get('lis_data_gap', '')}")
    lines.append("")
    lines.append("**【市场是否已经确认】**")
    lines.append(
        f"- PCS {_fmt(row.get('pcs'), digits=1)}（{row.get('pcs_stage')}）："
        f"价格结构 {_fmt(row.get('pcs_price_structure'), digits=1)}"
        f"，均线 {_fmt(row.get('pcs_moving_average'), digits=1)}"
        f"，相对强弱 {_fmt(row.get('pcs_relative_strength'), digits=1)}"
        f"，量能 {_fmt(row.get('pcs_volume'), digits=1)}"
        f"，聪明钱 {_fmt(row.get('pcs_smart_money'), digits=1)}"
        f"，消息反应 {_fmt(row.get('pcs_news_asymmetry'), digits=1)}"
    )
    lines.append(f"- 相对行业 20 日超额 {_fmt(row.get('p_rs20_vs_industry'), pct=True)}，"
                 f"60 日超额 {_fmt(row.get('p_rs60_vs_industry'), pct=True)}，"
                 f"量比(20/120) {_fmt(row.get('p_volume_ratio_20_120'), digits=2)}")
    lines.append("")
    lines.append("**【市场可能还没有定价什么】**")
    lines.append(f"{row.get('what_is_priced_in', '')}")
    lines.append("")
    lines.append("**【盈利弹性】**")
    lines.append(f"- TTM 归母 {_fmt(row.get('ttm_np'), digits=0, suffix=' 万元')}，"
                 f"归一化盈利 {_fmt(row.get('normalized_np'), digits=0, suffix=' 万元')}"
                 f"（口径 {row.get('rps_basis', 'DATA INCOMPLETE')}）")
    lines.append("")
    lines.append("**【估值重构空间】**")
    lines.append(
        f"- Bear {_fmt(row.get('upside_bear'), pct=True)} / Base {_fmt(row.get('upside_base'), pct=True)} "
        f"/ Bull {_fmt(row.get('upside_bull'), pct=True)}（情景推演，非目标价）"
    )
    lines.append(f"- 当前市值 {_fmt((row.get('current_mv') or np.nan) / 1e4, digits=1, suffix=' 亿元')}，"
                 f"行业中枢 PE {_fmt(row.get('industry_pe'), digits=1)}，"
                 f"当前 PE(TTM) {_fmt(row.get('current_pe'), digits=1)}")
    lines.append("")
    lines.append("**【核心催化剂】**")
    lines.append(
        f"- 事件 {_fmt(row.get('cs_earnings_event'), digits=1)}"
        f"，资本动作 {_fmt(row.get('cs_capital_action'), digits=1)}"
        f"，行业供给 {_fmt(row.get('cs_industry_supply'), digits=1)}"
        f"，价格动作 {_fmt(row.get('cs_price_action'), digits=1)}"
    )
    lines.append("")
    lines.append("**【最大风险】**")
    lines.append(
        f"- Kill：{row.get('kill_level')}（{row.get('kill_count')} 个信号）"
        f"{'；' + str(row.get('kill_reasons')) if row.get('kill_reasons') else ''}"
    )
    lines.append(f"- 过滤器：{row.get('filter_flag')} {row.get('filter_notes', '')}")
    lines.append(f"- 扣分项：{row.get('frs_penalties') or '无'}")
    lines.append("")
    lines.append("**【First Rejection】**")
    lines.append(f"{row.get('first_rejection', '')}")
    lines.append("")
    lines.append("**【Kill Signal】**")
    lines.append(f"{row.get('kill_level')}：{row.get('kill_reasons') or '无'}")
    lines.append("")
    lines.append("**【交易状态】**")
    lines.append(f"- 动作：{row.get('action')}；入场参考 {_fmt(row.get('entry_ref'), digits=2)}，"
                 f"止损 {_fmt(row.get('stop_loss'), digits=2)}，"
                 f"目标 1 {_fmt(row.get('target_1'), digits=2)}，"
                 f"目标 2 {_fmt(row.get('target_2'), digits=2)}，"
                 f"盈亏比 {_fmt(row.get('risk_reward'), digits=2)}")
    lines.append(f"- 距离 MA20：{_fmt(row.get('p_dist_ma20'), digits=3)}（>1.15 触发禁止追高）")
    lines.append("")
    lines.append("**【下一验证指标】**")
    lines.append(f"{row.get('next_verification', '')}")
    lines.append("")
    lines.append(f"> 数据置信度 {_fmt(row.get('data_confidence'), digits=2)}（{row.get('data_flag')}）"
                 f"，数据源 {row.get('data_source')}，point-in-time = {row.get('is_point_in_time')}")
    return "\n".join(lines)


def render_cards(frame: pd.DataFrame, top_n: int = 5) -> str:
    parts = []
    for i, (_, row) in enumerate(frame.head(top_n).iterrows(), start=1):
        parts.append(render_card(row, rank=i))
        parts.append("\n---\n")
    return "\n".join(parts)
