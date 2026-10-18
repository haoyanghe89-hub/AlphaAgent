"""Separate research-plan contract; never changes the old forecast contracts."""

from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from ..domain import FrozenModel

Route = Literal["TREND_SWING", "QUALITY_GROWTH"]


class Holding(FrozenModel):
    security_id: str = Field(pattern=r"^\d{6}\.(SH|SZ)$")
    route_id: Route
    industry_code: str = Field(pattern=r"^\d{6}\.SI$")
    market_value: Decimal = Field(gt=0, le=Decimal("100000000000"))


class Profile(FrozenModel):
    input_origin: Literal["USER_DECLARED", "SYNTHETIC_ACCEPTANCE"] = "USER_DECLARED"
    capital: Decimal = Field(gt=0, le=Decimal("100000000000"))
    available_cash: Decimal = Field(ge=0)
    reserve_cash: Decimal = Field(default=Decimal("0"), ge=0)
    risk_per_trade: Decimal = Field(default=Decimal("0.005"), gt=0, le=Decimal("0.02"))
    total_new_risk: Decimal = Field(default=Decimal("0.02"), gt=0, le=Decimal("0.05"))
    single_cap: Decimal = Field(default=Decimal("0.1"), gt=0, le=Decimal("0.3"))
    industry_cap: Decimal = Field(default=Decimal("0.25"), gt=0, le=Decimal("0.5"))
    liquidity_cap: Decimal = Field(default=Decimal("0.001"), gt=0, le=Decimal("0.01"))
    trend_allocation: Decimal = Field(default=Decimal("0.4"), ge=0, le=1)
    quality_allocation: Decimal = Field(default=Decimal("0.4"), ge=0, le=1)
    commission_rate: Decimal = Field(default=Decimal("0.0003"), ge=0, le=Decimal("0.01"))
    minimum_commission: Decimal = Field(default=Decimal("5"), ge=0, le=100)
    other_cost_rate: Decimal = Field(default=Decimal("0.001"), ge=0, le=Decimal("0.01"))
    holdings: tuple[Holding, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def consistent(self):
        if abs(
            self.available_cash + sum(h.market_value for h in self.holdings) - self.capital
        ) > Decimal("0.01"):
            raise ValueError("现金加持仓市值必须等于总资产；未知持仓不能忽略。")
        if self.reserve_cash > self.available_cash:
            raise ValueError("保留现金不能超过可用现金。")
        if self.trend_allocation + self.quality_allocation > 1:
            raise ValueError("路线预算合计不能超过总资产。")
        if len({(h.security_id, h.route_id) for h in self.holdings}) != len(self.holdings):
            raise ValueError("同一股票同一路线的持仓请合并填写。")
        return self


class PlanRequest(FrozenModel):
    schema_version: Literal["investment-plan-v1"] = "investment-plan-v1"
    request_key: UUID
    entities: tuple[str, ...] = Field(min_length=1, max_length=4)
    horizon: Literal["SWING", "MEDIUM"] = "SWING"
    profile: Profile | None = None

    @model_validator(mode="after")
    def entities_valid(self):
        if any(not e.strip() or len(e) > 30 for e in self.entities):
            raise ValueError("请输入一至四个准确的股票名称或代码。")
        return self
