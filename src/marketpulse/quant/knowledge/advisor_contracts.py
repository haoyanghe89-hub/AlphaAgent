"""Private holding inputs are separate from public investment model contracts."""

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, model_serializer, model_validator

from ..domain import FrozenModel


class Position(FrozenModel):
    input_origin: Literal["USER_DECLARED", "SYNTHETIC_ACCEPTANCE"] = "USER_DECLARED"
    entity: str = Field(min_length=1, max_length=30)
    cost_price: Decimal = Field(gt=0, le=1000000)
    shares: int = Field(gt=0, le=100000000, strict=True)
    sellable_shares: int = Field(ge=0, le=100000000, strict=True)
    bought_today: int = Field(default=0, ge=0, le=100000000, strict=True)
    buy_date: date | None = None
    total_assets: Decimal = Field(gt=0, le=Decimal("100000000000"))
    single_cap: Decimal = Field(default=Decimal("0.1"), gt=0, le=1)
    risk_budget: Decimal = Field(default=Decimal("0.005"), gt=0, le=Decimal("0.05"))
    trim_fraction: Decimal = Field(default=Decimal("0.5"), gt=0, le=1)
    commission_rate: Decimal = Field(default=Decimal("0.0003"), ge=0, le=Decimal("0.01"))
    minimum_commission: Decimal = Field(default=Decimal("5"), ge=0, le=100)
    other_sell_cost_rate: Decimal = Field(default=Decimal("0.001"), ge=0, le=Decimal("0.01"))

    @model_validator(mode="after")
    def consistent(self):
        if not self.entity.strip():
            raise ValueError("请填写准确股票名称或六位代码。")
        if self.sellable_shares + self.bought_today > self.shares:
            raise ValueError("可卖股数不能超过总股数减去今日买入股数。")
        return self


class AdvisorRequest(FrozenModel):
    schema_version: Literal["holding-advisor-v1"] = "holding-advisor-v1"
    request_key: UUID
    previous_run_id: str | None = Field(default=None, pattern=r"^RUN-HA-[0-9a-f]{32}$")
    position: Position
    horizon: Literal["SWING", "MEDIUM"] = "SWING"
    horizon_sessions: int = Field(default=20, ge=10, le=120)
    route_id: Literal["AUTO", "TREND_SWING", "QUALITY_GROWTH"] = "AUTO"
    question: str = Field(min_length=1, max_length=2000)
    scenario_price: Decimal | None = Field(default=None, gt=0, le=1000000)
    refresh_research: bool = False
    enhanced_research: bool = False
    semantic_understanding: bool = False
    structured_research: bool = False

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        value = handler(self)
        if not self.enhanced_research:
            value.pop("enhanced_research", None)
        if not self.semantic_understanding:
            value.pop("semantic_understanding", None)
        if not self.structured_research:
            value.pop("structured_research", None)
        return value

    @model_validator(mode="after")
    def consistent(self):
        if not self.question.strip():
            raise ValueError("请填写持仓问题。")
        if self.horizon == "SWING" and self.horizon_sessions > 40:
            raise ValueError("波段使用十至四十个交易日。")
        if self.horizon == "MEDIUM" and self.horizon_sessions < 41:
            raise ValueError("中期使用四十一至一百二十个交易日。")
        return self
