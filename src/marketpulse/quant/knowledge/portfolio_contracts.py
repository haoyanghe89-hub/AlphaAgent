"""Complete cash-account inputs, kept separate from public research requests."""

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from ..domain import FrozenModel


class Asset(FrozenModel):
    entity: str = Field(min_length=1, max_length=30)
    cost_price: Decimal | None = Field(default=None, gt=0, le=1000000)
    shares: int = Field(ge=0, le=100000000, strict=True)
    sellable_shares: int = Field(ge=0, le=100000000, strict=True)
    bought_today: int = Field(default=0, ge=0, le=100000000, strict=True)
    buy_date: date | None = None
    horizon: Literal["SWING", "MEDIUM"] = "SWING"
    route_id: Literal["AUTO", "TREND_SWING", "QUALITY_GROWTH"] = "AUTO"

    @model_validator(mode="after")
    def consistent(self):
        if not self.entity.strip() or self.entity != self.entity.strip():
            raise ValueError("请填写准确股票名称或六位代码，去除首尾空格。")
        if self.shares and self.cost_price is None:
            raise ValueError("已有持仓必须填写每股成本。")
        if self.sellable_shares + self.bought_today > self.shares:
            raise ValueError("可卖股数不能超过总股数减去今日买入股数。")
        if self.route_id == "QUALITY_GROWTH" and self.horizon != "MEDIUM":
            raise ValueError("质量成长原路线请选择中期；不得以短线切换掩盖失效。")
        if self.route_id == "TREND_SWING" and self.horizon != "SWING":
            raise ValueError("趋势波段原路线请选择波段。")
        return self


class PortfolioRequest(FrozenModel):
    schema_version: Literal["portfolio-advisor-v1"] = "portfolio-advisor-v1"
    request_key: UUID
    previous_run_id: str | None = Field(default=None, pattern=r"^RUN-PA-[0-9a-f]{32}$")
    input_origin: Literal["USER_DECLARED", "SYNTHETIC_ACCEPTANCE"] = "USER_DECLARED"
    all_holdings_declared: Literal[True]
    assets: tuple[Asset, ...] = Field(min_length=2, max_length=8)
    available_cash: Decimal = Field(ge=0, le=Decimal("100000000000"))
    reserve_cash: Decimal = Field(default=Decimal("0"), ge=0)
    equity_cap: Decimal = Field(default=Decimal("0.8"), gt=0, le=1)
    single_cap: Decimal = Field(default=Decimal("0.1"), gt=0, le=Decimal("0.5"))
    industry_cap: Decimal = Field(default=Decimal("0.25"), gt=0, le=1)
    correlation_threshold: Decimal = Field(default=Decimal("0.75"), gt=0, lt=1)
    correlated_cap: Decimal = Field(default=Decimal("0.35"), gt=0, le=1)
    risk_per_trade: Decimal = Field(default=Decimal("0.005"), gt=0, le=Decimal("0.05"))
    total_risk: Decimal = Field(default=Decimal("0.02"), gt=0, le=Decimal("0.1"))
    trend_allocation: Decimal = Field(default=Decimal("0.4"), ge=0, le=1)
    quality_allocation: Decimal = Field(default=Decimal("0.4"), ge=0, le=1)
    liquidity_cap: Decimal = Field(default=Decimal("0.001"), gt=0, le=Decimal("0.01"))
    trim_fraction: Decimal = Field(default=Decimal("0.5"), gt=0, le=1)
    commission_rate: Decimal = Field(default=Decimal("0.0003"), ge=0, le=Decimal("0.01"))
    minimum_commission: Decimal = Field(default=Decimal("5"), ge=0, le=100)
    other_sell_cost_rate: Decimal = Field(default=Decimal("0.001"), ge=0, le=Decimal("0.01"))
    other_buy_cost_rate: Decimal = Field(default=Decimal("0.0001"), ge=0, le=Decimal("0.01"))
    question: str = Field(
        default="请结合账户仓位、行业和相关性给出整体调仓建议。", min_length=1, max_length=2000
    )
    refresh_research: bool = False

    @model_validator(mode="after")
    def consistent(self):
        if len({a.entity.upper() for a in self.assets}) != len(self.assets):
            raise ValueError("同一股票请合并持仓，每只股票仅保留一条原路线。")
        if sum(a.shares > 0 for a in self.assets) < 2:
            raise ValueError("账户联合分析至少需要两只已有持仓；单股请使用持仓顾问。")
        if sum(a.shares == 0 for a in self.assets) > 4:
            raise ValueError("待调入候选最多四只。")
        if self.reserve_cash > self.available_cash:
            raise ValueError("保留现金不能超过当前可用现金。")
        if self.trend_allocation + self.quality_allocation > 1:
            raise ValueError("两条路线预算合计不能超过总资产。")
        if self.risk_per_trade > self.total_risk:
            raise ValueError("单笔风险预算不能超过账户风险预算。")
        if not self.question.strip():
            raise ValueError("请填写组合问题。")
        return self
