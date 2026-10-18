"""Independent investment research, expressed in trading sessions."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from ..domain import FrozenModel
from .planning_contracts import Profile


class InvestmentRequest(FrozenModel):
    schema_version: Literal[
        "investment-research-v1",
        "investment-research-v2",
        "investment-research-v3",
        "investment-research-v4",
    ] = "investment-research-v1"
    request_key: UUID
    entities: tuple[str, ...] = Field(min_length=1, max_length=4)
    horizon: Literal["SWING", "MEDIUM"] = "SWING"
    horizon_sessions: int = Field(ge=10, le=120)
    profile: Profile | None = None

    @model_validator(mode="before")
    @classmethod
    def defaults(cls, values):
        values = dict(values)
        values.setdefault("horizon_sessions", 60 if values.get("horizon") == "MEDIUM" else 20)
        return values

    @model_validator(mode="after")
    def boundaries(self):
        if any(not e.strip() or len(e) > 30 for e in self.entities):
            raise ValueError("请输入一至四个准确的股票名称或代码。")
        if (
            self.horizon == "SWING"
            and self.horizon_sessions > 40
            or self.horizon == "MEDIUM"
            and self.horizon_sessions < 41
        ):
            raise ValueError("波段使用十至四十个交易日，中期使用四十一至一百二十个交易日。")
        return self

    def calculation_request(self):
        data = self.model_dump(mode="json")
        data.pop("horizon_sessions")
        data["schema_version"] = "investment-plan-v1"
        return data
