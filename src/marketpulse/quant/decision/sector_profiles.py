"""Versioned, separate business contracts; classifications are discovered at run time."""

VERSION = "sector-business-2026-10-14-v1"
PROFILES = {
    "半导体设备": dict(
        industry_id="semiconductor_equipment",
        role="EQUIPMENT",
        query="主营业务 公司设备 刻蚀 薄膜 沉积 清洗 测试 现金流 风险",
        original_pattern=r"刻蚀|薄膜|沉积|清洗|抛光|测试|检测|量测|计量|MOCVD|CMP",
        required_word=r"设备",
        chain=["客户资本开支", "设备导入与验收", "收入及利润", "现金回收"],
        missing_inputs=["完整订单与验收明细", "规范化分部回款"],
        limits=["合同负债不是完整订单", "验证进展不是量产收入"],
    ),
    "集成电路制造": dict(
        industry_id="semiconductor_manufacturing",
        role="MANUFACTURING",
        query="主要业务 晶圆 制造 代工 产能 利用率 工艺 资本开支 折旧 风险",
        original_pattern=r"晶圆|晶圓|集成电路制造|積體電路製造|芯片制造",
        required_word=r"制造|製造|代工|生产|生產",
        chain=[
            "客户需求与产品结构",
            "产能利用率与晶圆出货",
            "售价及折旧负担",
            "利润现金流与资本开支",
        ],
        missing_inputs=["统一口径产能利用率", "晶圆尺寸及工艺结构", "分币种资本开支与折旧序列"],
        limits=[
            "制造企业不套用设备订单及验收逻辑",
            "代工与IDM不能直接混排",
            "未取得统一口径利用率不能推断产能满载",
            "海外存储增长不能直接证明本公司受益",
        ],
    ),
}


def profile(entity):
    if entity not in PROFILES:
        raise ValueError("unsupported independently configured industry")
    return dict(version=VERSION, entity=entity, **PROFILES[entity])
