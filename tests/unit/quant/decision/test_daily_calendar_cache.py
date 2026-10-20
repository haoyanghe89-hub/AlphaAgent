from types import SimpleNamespace

import pytest

from marketpulse.quant.decision.next_session import calendar


def test_rule_parse_cache_uses_exact_original_bytes_only(monkeypatch):
    seen = []

    def reader(stream):
        raw = stream.read()
        seen.append(raw)
        return SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: raw.decode())])

    calendar.rule_text.cache_clear()
    monkeypatch.setattr(calendar, "PdfReader", reader)
    try:
        assert calendar.rule_text(b"%PDF-A") == "%PDF-A"
        assert calendar.rule_text(b"%PDF-A") == "%PDF-A"
        assert calendar.rule_text(b"%PDF-B") == "%PDF-B"
        assert seen == [b"%PDF-A", b"%PDF-B"]
        assert calendar.rule_text.cache_info().maxsize == 8
    finally:
        calendar.rule_text.cache_clear()


def test_invalid_rule_parse_is_not_cached(monkeypatch):
    seen = []

    def reader(stream):
        seen.append(stream.read())
        raise ValueError("invalid original")

    calendar.rule_text.cache_clear()
    monkeypatch.setattr(calendar, "PdfReader", reader)
    for _ in range(2):
        with pytest.raises(ValueError, match="invalid original"):
            calendar.rule_text(b"%PDF-BAD")
    assert len(seen) == 2
