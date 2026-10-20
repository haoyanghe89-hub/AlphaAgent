"""Versioned wire shape: machine claim identities never change model-authored judgments."""

from typing import Literal

from pydantic import Field

from ..domain import FrozenModel
from . import research_agents as agents

VERSION = "investment-structured-v2"
GUIDANCE = (
    "结构契约：只输出schema字段。claims与opposing只含text和evidence_ids，不输出claim_id。"
    "direction_supports使用S1/S2/S3指向决定方向的支持项；不得指向不存在的支持项。"
    "判断ID由程序按支持、反证顺序分配，不能把同一判断同时列作支持和反证。"
    "逐只满足citation_requirements：技术、资金、政策各至少引用一个可用ID，可写入支持或反证。"
    "每项简短且合并同类引用，不重复指标计票；期限、绝对方向与等待入场分开。"
    "禁止字样即使是否定句也不要出现：概率、胜率、已获补贴、确定受益、机构已买、机构净买、政策导致股价。"
    "改用公司资格尚待核验、资金代理不识别交易者等定性限定。"
)


class Statement(FrozenModel):
    text: str = Field(min_length=5, max_length=180)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=4)


class WireOpinion(FrozenModel):
    security_id: str
    direction: Literal["FAVORABLE", "UNFAVORABLE"]
    summary: str = Field(min_length=5, max_length=120)
    claims: tuple[Statement, ...] = Field(min_length=2, max_length=3)
    opposing: tuple[Statement, ...] = Field(min_length=1, max_length=2)
    direction_supports: tuple[Literal["S1", "S2", "S3"], ...] = Field(min_length=1, max_length=3)
    method_rule_ids: tuple[str, ...] = Field(min_length=0, max_length=3)
    invalidation: tuple[str, ...] = Field(min_length=1, max_length=2)
    next_checks: tuple[str, ...] = Field(min_length=1, max_length=2)


class WireAnalysis(FrozenModel):
    opinions: tuple[WireOpinion, ...] = Field(min_length=1, max_length=4)


def analysis(wire, schema=agents.Analysis):
    value = wire.model_dump(mode="json")
    for opinion in value["opinions"]:
        claims, opposing = opinion["claims"], opinion["opposing"]
        if any(int(s[1:]) > len(claims) for s in opinion["direction_supports"]):
            raise ValueError("方向支持索引不存在")
        if len(set(opinion["direction_supports"])) != len(opinion["direction_supports"]):
            raise ValueError("方向支持索引重复")
        if len({s["text"] for s in claims + opposing}) != len(claims + opposing):
            raise ValueError("支持与反证不得重复同一判断")
        for index, statement in enumerate(claims + opposing, 1):
            statement["claim_id"] = "C" + str(index)
        opinion["direction_claim_ids"] = ["C" + s[1:] for s in opinion.pop("direction_supports")]
    return schema.model_validate(value)


def requirements(context):
    return [
        dict(
            security_id=s["security_id"],
            **{
                key: [
                    f["evidence_id"]
                    for f in context["evidence"]
                    if f.get("security_id") == s["security_id"] and f.get("kind") in kinds
                ]
                for key, kinds in dict(
                    technical={"TECHNICAL"},
                    funding={"DAILY_FLOW", "MINUTE_FLOW"},
                    policy={"POLICY_CLAUSE"},
                ).items()
            },
        )
        for s in context["stocks"]
    ]


def configure(config, enhanced):
    thinking = enhanced and not config.get("enhancement_stage")
    config["execution_contract"] = VERSION
    config["reasoning_profile"] = dict(
        version="daily-reasoning-v1",
        roles={
            role: dict(
                thinking_enabled=thinking and role == "Researcher",
                reasoning_effort="low" if thinking and role == "Researcher" else None,
                reasoning_tokens=2048 if thinking and role == "Researcher" else 0,
            )
            for role in ("Researcher", "Planner", "Analyst", "Verifier", "Writer")
        },
    )


def verify_config(config, enhanced):
    expected = (
        {"enhancement_stage": config["enhancement_stage"]}
        if config.get("enhancement_stage")
        else {}
    )
    configure(expected, enhanced)
    if any(config.get(k) != v for k, v in expected.items()):
        raise ValueError("结构化研究的固定推理职责配置不一致")
