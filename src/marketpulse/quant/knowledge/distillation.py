"""Deterministic admission gates before any model or publication step."""

import re

from marketpulse.infrastructure.storage.models import BlobRef

from .rules import MethodRule

INJECTION = re.compile(
    r"ignore (?:all|previous|prior) (?:rules|instructions)|system\s*prompt|"
    r"忽略.{0,8}(?:指令|规则)|泄露.{0,8}(?:密钥|token)|<\|(?:system|im_start)",
    re.I,
)


def validate_rule(rule: MethodRule, library, cache=None):
    cache = {} if cache is None else cache
    families = set()
    for citation in rule.citations:
        key = (citation.document_id, citation.version)
        if key not in cache:
            doc = library.document(*key)
            body = library.blobs.get_bytes(BlobRef.from_uri(doc["text_ref"])).decode()
            cache[key] = (doc, body)
        doc, body = cache[key]
        if body[citation.start : citation.end] != citation.quote:
            raise ValueError("SOURCE_BODY_REQUIRED: exact body locator does not match")
        if citation.end - citation.start != len(citation.quote):
            raise ValueError("SOURCE_LOCATOR_INVALID")
        if "\ufffd" in citation.quote or re.search(r"/g\d+|\(cid:\d+\)", citation.quote):
            raise ValueError("SOURCE_TEXT_UNRELIABLE: OCR/font decoding needs review")
        if INJECTION.search(citation.quote):
            raise ValueError("SOURCE_INSTRUCTION_QUARANTINED")
        families.add(doc["source"]["source_family"])
    for parameter in rule.parameters:
        if parameter.origin == "AUTHOR_ORIGINAL":
            index = parameter.citation_index
            if index >= len(rule.citations) or not re.search(
                r"(?<![\d.])" + re.escape(parameter.value) + r"(?![\d.])",
                rule.citations[index].quote,
            ):
                raise ValueError("UNSOURCED_AUTHOR_PARAMETER")
    prose = " ".join(
        (
            rule.source_claim,
            rule.system_rule,
            rule.adaptation,
            *rule.steps,
            *rule.conditions,
            *rule.counterexamples,
        )
    )
    # Numeric thresholds belong in the explicit provenance contract, not hidden prose.
    if re.search(r"\d|[%％]|必涨|稳赚|保证盈利", prose) or INJECTION.search(prose):
        raise ValueError("UNBOUND_NUMERIC_OR_UNSAFE_RULE")
    return sorted(families)
