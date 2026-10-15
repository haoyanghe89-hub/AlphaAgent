"""Deterministic heat convention, with an unchanged and complete market denominator."""

from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from ..domain import decimal_text
from .contracts import SectorSelectionRecord

RANKING_VERSION = "HEAT_V1:mean20_share_desc,mean5_minus_prior5_desc,industry_id_asc"


def rank_sectors(data, selected_at):
    sessions = data.sessions[-20:]
    if data.sessions != tuple(sorted(set(data.sessions))):
        raise ValueError("ordered unique verified trading sessions required")
    if data.cutoff_at > selected_at:
        raise ValueError("selection cannot use future inputs")
    if datetime.combine(sessions[-1], time(15), ZoneInfo("Asia/Shanghai")) > data.cutoff_at:
        raise ValueError("future/incomplete trading session in heat inputs")
    if len({s.industry_id for s in data.sectors}) != len(data.sectors):
        raise ValueError("duplicate industries")
    market = {p.session: p for p in data.market_turnover}
    valid_market = len(market) == len(data.market_turnover) and all(
        d in market
        and market[d].unit == "CNY"
        and market[d].value is not None
        and market[d].value > 0
        for d in sessions
    )
    rows, excluded = [], []
    if data.market_coverage_complete and valid_market:
        for sector in data.sectors:
            daily = {p.session: p for p in sector.days}
            complete = len(daily) == len(sector.days) and all(
                d in daily
                and daily[d].turnover is not None
                and daily[d].members_available == daily[d].members_total
                for d in sessions
            )
            if not complete:
                excluded.append(dict(industry_id=sector.industry_id, reason="20日序列或成员缺失"))
                continue
            if any(daily[d].turnover > market[d].value for d in sessions):
                raise ValueError("industry numerator exceeds fixed market denominator")
            shares = [daily[d].turnover / market[d].value for d in sessions]
            mean20 = sum(shares) / Decimal(20)
            change5 = (sum(shares[-5:]) - sum(shares[-10:-5])) / Decimal(5)
            rows.append((mean20, change5, sector.industry_id, sector.membership_ref))
    else:
        excluded = [
            dict(industry_id=s.industry_id, reason="同口径市场分母不完整") for s in data.sectors
        ]
    rows.sort(key=lambda r: (-r[0], -r[1], r[2]))
    record = SectorSelectionRecord(
        selected_at=selected_at,
        data_cutoff=data.cutoff_at,
        classification_version=data.classification_version,
        coverage_ref=data.universe_ref,
        ranking_method=RANKING_VERSION,
        metric_refs=tuple(r[3] for r in rows),
        chosen_scope=rows[0][2] if rows else "NONE",
        selection_reason="按已覆盖完整20日成交占比排序；成交活跃不等于资金流入。"
        if rows
        else "同口径市场分母不完整或20日序列不全，无法排名。",
        provisional=False,
    )
    return dict(
        ranking=[
            dict(
                industry_id=r[2],
                mean_20_share=decimal_text(r[0]),
                change_5_share=decimal_text(r[1]),
                membership_ref=r[3],
            )
            for r in rows
        ],
        excluded=excluded,
        selection=record,
        reason=record.selection_reason,
        coverage_label="已覆盖范围的排序" if excluded else "输入行业范围的排序",
    )
