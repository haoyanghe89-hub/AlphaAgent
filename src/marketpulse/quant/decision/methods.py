"""Structured, versioned methodology lookup; source text is never executed."""

import json
from importlib.resources import files

from .contracts import EvaluationCase, MethodCard, MethodSelection


def load_config(name):
    return json.loads(files(__package__).joinpath(name).read_text(encoding="utf8"))


def load_methods():
    return tuple(MethodCard.model_validate(row) for row in load_config("method_cards.json"))


def load_cases():
    return tuple(EvaluationCase.model_validate(row) for row in load_config("evaluation_cases.json"))


def select_methods(scope, horizon, available_inputs, selected_at):
    selections = []
    for card in load_methods():
        missing = tuple(x for x in card.input_requirements if x not in available_inputs)
        if scope not in card.applicable_scopes:
            status, reason = "WRONG_SCOPE", "不适用当前研究范围。"
        elif horizon == "NEXT_SESSION" and card.horizon == "MEDIUM_TERM":
            status, reason = "WRONG_HORIZON", "中期经营方法不能单独支持下一交易日方向。"
        elif missing:
            status, reason = "MISSING_INPUTS", "缺少必要输入，不能静默启用：" + "、".join(missing)
        else:
            status, reason = (
                "APPLICABLE",
                "输入合同满足；方法有效性仍待验证，不能据此承诺预测优势。",
            )
        selections.append(
            MethodSelection(
                method_id=card.method_id,
                version=card.version,
                selected_at=selected_at,
                applicability=status,
                missing_inputs=missing,
                reason=reason,
            )
        )
    return tuple(selections)
