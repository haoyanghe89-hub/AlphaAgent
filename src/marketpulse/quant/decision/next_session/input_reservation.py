"""Pinned official tokenizer estimates; provider usage remains the accounting authority."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

VERSION = "deepseek-v4-tokenizer-v1"
POLICY = dict(
    version=VERSION,
    tokenizer_sha256="89085f12ef79460ac5f66d1119325ddfc694b4ab209d80bbd81d35f081dc9614",
    template_sha256="841f8cf146e3f0ad1082594a31f68ecf7608c20467ef355081333d85bbaeb1cb",
    framing_margin=1024,
    source="https://cdn.deepseek.com/api-docs/deepseek_v4_tokenizer.zip",
)


@lru_cache(maxsize=1)
def resources():
    from jinja2 import Environment
    from tokenizers import Tokenizer

    root = Path(__file__).with_name("assets")
    raw, config = (
        (root / "tokenizer.json").read_bytes(),
        (root / "tokenizer_config.json").read_bytes(),
    )
    if (
        hashlib.sha256(raw).hexdigest() != POLICY["tokenizer_sha256"]
        or hashlib.sha256(config).hexdigest() != POLICY["template_sha256"]
    ):
        raise ValueError("官方分词器或模板哈希不一致")
    options = json.loads(config)
    return (
        Tokenizer.from_str(raw.decode()),
        Environment().from_string(options["chat_template"]),
        options,
    )


def estimate(request, policy):
    if policy != POLICY or request["model"] not in {"deepseek-flash", "deepseek-v4-pro"}:
        raise ValueError("输入预留的模型或分词版本未固定")
    tokenizer, template, options = resources()
    # Must mirror OpenAICompatibleModelAdapter's appended schema system message exactly.
    messages = list(request["messages"]) + [
        dict(
            role="system",
            content="Return only one JSON object matching this schema: "
            + json.dumps(request["schema"], ensure_ascii=False),
        )
    ]
    text = template.render(
        messages=messages,
        bos_token=options["bos_token"]["content"],
        eos_token=options["eos_token"]["content"],
        add_generation_prompt=True,
    )
    local = len(tokenizer.encode(text, add_special_tokens=False).ids)
    return dict(
        version=VERSION,
        local_tokens=local,
        margin_tokens=POLICY["framing_margin"],
        reserved_input_tokens=local + POLICY["framing_margin"],
    )


def verify(state, request, call):
    expected = estimate(request, state["config"]["input_reservation"])
    if (
        request.get("input_estimate") != expected
        or call.get("input_estimate") != expected
        or call["reserved_tokens"]
        != expected["reserved_input_tokens"] + request["max_output_tokens"]
    ):
        raise ValueError("分词预留与真实请求不一致")
    if (
        type(call.get("input_tokens")) is int
        and call["input_tokens"] > expected["reserved_input_tokens"]
    ):
        raise ValueError("供应商输入超出官方分词预留余量，须停止并核对版本")
