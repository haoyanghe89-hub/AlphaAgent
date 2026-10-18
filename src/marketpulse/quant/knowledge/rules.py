"""Source-bound reference rules. Publication never means a profitable strategy."""

from typing import Literal

from pydantic import Field, model_validator

from ..domain import FrozenModel


class Citation(FrozenModel):
    document_id: str = Field(pattern=r"^[A-Z0-9-]{3,80}$")
    version: str = Field(pattern=r"^[0-9a-f]{64}$")
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=80, max_length=1800)


class Parameter(FrozenModel):
    name: str = Field(min_length=2, max_length=100)
    value: str = Field(min_length=1, max_length=40)
    meaning: str = Field(min_length=10, max_length=300)
    origin: Literal["AUTHOR_ORIGINAL", "SYSTEM_PROPOSAL"]
    citation_index: int | None = Field(default=None, ge=0)
    applicability: Literal["SOURCE_MARKET_ONLY", "UNVALIDATED_PROPOSAL"]

    @model_validator(mode="after")
    def provenance(self):
        if self.origin == "AUTHOR_ORIGINAL":
            if self.citation_index is None or self.applicability != "SOURCE_MARKET_ONLY":
                raise ValueError("author parameter requires its source and source-market boundary")
        elif self.citation_index is not None or self.applicability != "UNVALIDATED_PROPOSAL":
            raise ValueError("system parameter must remain an unvalidated proposal")
        return self


class MethodRule(FrozenModel):
    schema_version: Literal["investment-rule-v1"] = "investment-rule-v1"
    rule_id: str = Field(pattern=r"^KR-[A-Z0-9-]{3,70}$")
    title: str = Field(min_length=4, max_length=100)
    kind: Literal["筛选", "入场", "持有", "退出", "风险", "容量", "估值", "市场环境"]
    source_claim: str = Field(min_length=15, max_length=600)
    system_rule: str = Field(min_length=15, max_length=600)
    adaptation: str = Field(min_length=15, max_length=400)
    horizons: tuple[Literal["SWING", "MEDIUM", "LONG"], ...] = Field(min_length=1)
    required_inputs: tuple[str, ...] = Field(min_length=1, max_length=8)
    conditions: tuple[str, ...] = Field(min_length=1, max_length=5)
    steps: tuple[str, ...] = Field(min_length=1, max_length=5)
    counterexamples: tuple[str, ...] = Field(min_length=1, max_length=5)
    forbidden_inferences: tuple[str, ...] = Field(min_length=1, max_length=5)
    keywords: tuple[str, ...] = Field(min_length=2, max_length=15)
    conflict_group: str = Field(min_length=2, max_length=100)
    citations: tuple[Citation, ...] = Field(min_length=1, max_length=3)
    parameters: tuple[Parameter, ...] = Field(default=(), max_length=8)
    market_scope: Literal["CN_A_REFERENCE"] = "CN_A_REFERENCE"
    execution_status: Literal["REFERENCE_ONLY"] = "REFERENCE_ONLY"
    validation_status: Literal["UNVALIDATED"] = "UNVALIDATED"


class RuleReview(FrozenModel):
    rule_id: str
    version: str = Field(pattern=r"^[0-9a-f]{64}$")
    verdict: Literal["APPROVE", "REJECT"]
    supported: bool
    parameters_supported: bool
    conditions_complete: bool
    counterexamples_useful: bool
    reason: str = Field(min_length=10, max_length=700)


class ReviewBatch(FrozenModel):
    reviews: tuple[RuleReview, ...] = Field(min_length=1, max_length=6)
