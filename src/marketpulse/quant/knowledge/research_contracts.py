"""Independent investment research, expressed in trading sessions."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_serializer, model_validator

from ..domain import FrozenModel
from .planning_contracts import Profile
from .question_understanding import Focus


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
    enhanced_research: bool = False
    execution_contract: Literal["investment-structured-v2"] | None = None
    input_reservation: Literal["deepseek-v4-tokenizer-v1"] | None = None
    enhancement_stage: Literal["METHODS", "TOOLS"] | None = None
    analysis_focus: tuple[Focus, ...] = Field(default=(), max_length=5)

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        value = handler(self)
        if not self.enhanced_research:
            value.pop("enhanced_research", None)
        if not self.analysis_focus:
            value.pop("analysis_focus", None)
        if self.execution_contract is None:
            value.pop("execution_contract", None)
        if self.input_reservation is None:
            value.pop("input_reservation", None)
        if self.enhancement_stage is None:
            value.pop("enhancement_stage", None)
        return value

    @model_validator(mode="before")
    @classmethod
    def defaults(cls, values):
        values = dict(values)
        values.setdefault("horizon_sessions", 60 if values.get("horizon") == "MEDIUM" else 20)
        return values

    @model_validator(mode="after")
    def boundaries(self):
        if self.enhancement_stage and not (self.enhanced_research and self.execution_contract):
            raise ValueError("组件对照必须绑定增强研究与结构契约")
        if self.input_reservation and not self.execution_contract:
            raise ValueError("分词预留须绑定结构契约")
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
        data.pop("enhanced_research", None)
        data.pop("analysis_focus", None)
        data.pop("execution_contract", None)
        data.pop("input_reservation", None)
        data.pop("enhancement_stage", None)
        data["schema_version"] = "investment-plan-v1"
        return data
