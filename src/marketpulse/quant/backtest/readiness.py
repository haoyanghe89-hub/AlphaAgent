"""Inspect actual frozen inventory; never treat current masters as a past universe."""

from sqlalchemy import select

from ..storage.models import DatasetSnapshotRow, InstrumentRow


def inspect(session, store, start, end):
    masters = session.scalars(select(InstrumentRow).order_by(InstrumentRow.instrument_id)).all()
    rows = session.scalars(
        select(DatasetSnapshotRow).order_by(DatasetSnapshotRow.snapshot_id).limit(1001)
    ).all()
    bounded = len(rows) <= 1000
    snapshots = []
    for row in rows[:1000]:
        snapshot = store.load(row.snapshot_id)  # validates manifest and all partitions
        r = snapshot.result.request
        if r.end < start or r.start > end:
            continue
        snapshots.append(
            dict(
                snapshot_id=snapshot.snapshot_id,
                semantic_hash=snapshot.semantic_hash,
                dataset=r.dataset,
                instrument_ids=list(r.instruments),
                start=r.start.isoformat(),
                end=r.end.isoformat(),
                asof=r.asof.isoformat(),
                retrieved_at=snapshot.result.retrieved_at.isoformat(),
                provider=snapshot.result.provider,
                upstream=snapshot.result.upstream,
                pit_level=snapshot.result.pit.value,
                row_count=snapshot.row_count,
                adjustment=r.adjustment,
                quality_flags=list(snapshot.result.quality_flags),
                fields=list(r.fields),
                units=dict(snapshot.result.units),
                source_status="UNVERIFIED",
            )
        )
    financials = [s for s in snapshots if s["dataset"] == "financial"]
    raw = [s for s in snapshots if s["dataset"] == "daily" and s["adjustment"] == "raw"]
    price_instruments = {i for s in raw for i in s["instrument_ids"]}
    checks = [
        dict(
            key="historical_universe",
            title="历史股票池与证券生命周期",
            status="UNAVAILABLE",
            detail=(
                f"当前目录有 {len(masters)} 个证券定义，但没有逐期历史股票池档案，"
                "无法证明退市股与历史范围完整。"
            ),
        ),
        dict(
            key="financial_pit",
            title="成交前可得财报与修订版本",
            status="PARTIAL" if financials else "UNAVAILABLE",
            detail=(
                f"区间内有 {len(financials)} 份财务快照；事后抓取及披露日期不能替代"
                "历史最初版本、修订记录与逐次选股输入核验。"
            ),
        ),
        dict(
            key="raw_price_coverage",
            title="完整区间的原始行情与交易日历",
            status="INSUFFICIENT" if raw else "UNAVAILABLE",
            detail=(
                f"找到 {len(raw)} 份原始行情快照，覆盖 {len(price_instruments)} 个证券；"
                "尚未验证历史股票池全量覆盖、分段拼接及日历缺口。"
            ),
        ),
        dict(
            key="corporate_actions",
            title="分红、送转、配股与退市处理",
            status="UNAVAILABLE",
            detail="尚无准入的完整公司行为档案；前／后复权价格不能替代现金、权益及持仓账务处理。",
        ),
        dict(
            key="execution_rules",
            title="历史停牌、涨跌停与成交限制",
            status="UNAVAILABLE",
            detail="尚无逐日完整成交限制与按生效日期核验的交易规则；有收盘价不代表开盘可成交。",
        ),
        dict(
            key="benchmark",
            title="沪深300基准与收益口径",
            status="UNAVAILABLE",
            detail="尚未准入同区间沪深300价格／全收益基准；不能用缺失值或合成曲线比较。",
        ),
        dict(
            key="independent_check",
            title="自算指标与独立权威口径核对",
            status="UNAVAILABLE",
            detail="账本手算和同输入哈希复算只证明内部逻辑；历史选股及绩效尚未进行独立来源交叉核对。",
        ),
    ]
    if not bounded:
        checks.append(
            dict(
                key="inventory_bound",
                title="快照目录检查范围",
                status="INSUFFICIENT",
                detail="快照超过 1000 份检查上限；必须分段检查，不把本次结果当作完整目录。",
            )
        )
    return dict(
        instrument_count=len(masters),
        checked_snapshot_count=len(snapshots),
        inventory_complete=bounded,
        snapshots=snapshots,
        checks=checks,
        instruments=[
            dict(instrument_id=m.instrument_id, name=m.definition["name"]) for m in masters[:100]
        ],
    )
