import json

import pytest

from marketpulse.telemetry.content import PUBLIC_CONTENT_POLICY, public_payload
from marketpulse.telemetry.tracing import observation, stage

from .test_runtime import receiver as receiver
from .test_runtime import runtime as runtime
from .test_runtime import spans


def attributes(span):
    return {a.key: a.value.string_value for a in span.attributes}


def payload(attributes, key):
    value = attributes["langfuse.observation." + key]
    try:
        return json.loads(value)
    except ValueError:
        return value


def test_public_text_and_messages_survive_both_filters(runtime, receiver):
    messages = [{"role": "user", "content": "核对海康威视的公开资料"}]
    with observation("public", content_policy=PUBLIC_CONTENT_POLICY, input=messages) as current:
        current.update(output="取得公司资料、资金数据和政策原文。")
        with observation("private", input={"shares": "PRIVATE-SHARES"}):
            pass
    runtime.flush()
    public = attributes(next(x for x in spans(receiver[2]) if x.name == "public"))
    assert payload(public, "input") == messages
    assert "政策原文" in payload(public, "output")
    assert not any(b"PRIVATE-" in body for _, body in receiver[2])


def test_nested_json_messages_hide_accounts_and_credentials():
    messages = [
        {
            "role": "user",
            "content": json.dumps({"text": "公开现金流", "profile": {"capital": "PRIVATE-CAPITAL"}})
            + "\n检查：引用待核。",
        },
        {"role": "system", "content": "Authorization: Bearer sk-PRIVATE-SECRET-1234567890"},
    ]
    safe, info = public_payload(messages)
    encoded = json.dumps(safe, ensure_ascii=False)
    assert "公开现金流" in encoded and "引用待核" in encoded
    assert "PRIVATE-" not in encoded and info["redactions"] >= 2


def test_truncation_is_explicit_and_preserves_message_shape():
    safe, info = public_payload([{"role": "user", "content": "长" * 80000}])
    assert info["truncated"] and safe[0]["role"] == "user"
    assert "观测截断" in safe[0]["content"] and len(safe[0]["content"]) < 61000


def test_direct_sdk_public_policy_still_redacts_private_fields(runtime, receiver):
    with runtime.client.start_as_current_observation(
        name="public.sdk",
        metadata={"content_policy": PUBLIC_CONTENT_POLICY},
        input={"text": "公开材料", "account": {"shares": "PRIVATE-DIRECT"}},
    ):
        pass
    runtime.flush()
    body = b"".join(body for _, body in receiver[2])
    assert "公开材料".encode() in body and b"PRIVATE-DIRECT" not in body


def test_stage_capture_preserves_return_identity_and_single_execution(runtime, receiver):
    result = {"message": "完整公开结果"}
    calls = []

    @stage(
        "public.stage",
        content_policy=PUBLIC_CONTENT_POLICY,
        inputs=lambda a: "查询 " + a["target"],
        outputs=lambda value, a: value["message"],
    )
    def run(target):
        calls.append(target)
        return result

    with observation("root"):
        assert run("公开公司") is result
    runtime.flush()
    assert calls == ["公开公司"]
    actual = attributes(next(x for x in spans(receiver[2]) if x.name == "public.stage"))
    assert payload(actual, "input") == "查询 公开公司"
    assert payload(actual, "output") == "完整公开结果"


def test_capture_error_never_changes_business_result(runtime, receiver):
    def broken(*args):
        raise ValueError("PRIVATE-CAPTURE-ERROR")

    @stage("capture.failed", content_policy=PUBLIC_CONTENT_POLICY, outputs=broken)
    def run():
        return "business result"

    with observation("root"):
        assert run() == "business result"
    runtime.flush()
    actual = attributes(next(x for x in spans(receiver[2]) if x.name == "capture.failed"))
    assert "OBSERVABILITY_GAP" in actual["langfuse.observation.output"]
    assert not any(b"PRIVATE-" in body for _, body in receiver[2])


@pytest.mark.parametrize("name", ["profile", "holdings", "available_cash", "cost_price", "api_key"])
def test_sensitive_keys_are_removed_at_any_depth(name):
    safe, info = public_payload({"public": {name: "PRIVATE-VALUE", "text": "保留公开文本"}})
    assert "PRIVATE-VALUE" not in json.dumps(safe) and info["redactions"] == 1
