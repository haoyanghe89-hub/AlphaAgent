"""Synthetic notice text only; real previews always fetch reviewed PDF bytes."""

import copy
import hashlib
from types import SimpleNamespace

import pytest

from marketpulse.quant.backtest import notices
from marketpulse.quant.contracts import digest


def setup_notices(monkeypatch):
    profiles = copy.deepcopy(notices.PROFILES)
    raw = {}
    texts = {
        "issuer_disclosure": "TEST ONLY 证券代码 300114 302132 上市主体没有发生实质变化 "
        "启用当日设置涨跌幅限制 启用日期：2025年2月17日",
        "cni_index_notice": "TEST ONLY 中航电测（300114） 中航成飞（302132） "
        "399001 深证成指 302132 中航成飞 300114 中航电测 决定于2025年2月17日",
    }
    for key, p in profiles.items():
        raw[key] = texts[p["family"]].encode()
        p["sha256"] = hashlib.sha256(raw[key]).hexdigest()
    monkeypatch.setattr(notices, "PROFILES", profiles)

    def reader(stream):
        text = stream.read().decode()
        count = 4 if "上市主体" in text else 2
        return SimpleNamespace(
            pages=[SimpleNamespace(extract_text=lambda: text)]
            + [SimpleNamespace(extract_text=lambda: "") for _ in range(count - 1)]
        )

    monkeypatch.setattr("pypdf.PdfReader", reader)
    return raw


def packets(store, raw):
    result = []
    for key, data in raw.items():
        store.blobs.put_bytes(data)
        p = notices.PROFILES[key]
        result.append(
            dict(
                provider="official_reviewed_notice",
                query=dict(kind="official_notice", profile_id=key),
                rows=[notices.extract_notice(data, key)],
                retrieved_at="2026-10-10T00:00:00Z",
                document_size=len(data),
                document_sha256=p["sha256"],
                source_url=p["url"],
            )
        )
    return result


def target():
    return dict(
        profile_ids=list(notices.PROFILES),
        profile_hash=digest(notices.PROFILES),
        start="2021-10-06",
        end="2026-10-06",
        lifecycle_hash="synthetic-parent",
    )


def test_notice_two_families_and_raw_binding(monkeypatch):
    raw = setup_notices(monkeypatch)
    data = dict(raw)
    store = SimpleNamespace(
        blobs=SimpleNamespace(
            put_bytes=lambda x: None,
            get_bytes=lambda ref: next(
                b for b in data.values() if hashlib.sha256(b).hexdigest() == ref.sha256
            ),
        )
    )
    sources = packets(store, raw)
    partial = notices.inspect_notices(sources[:1], target(), store)
    assert not partial["complete"] and partial["events"][0]["status"] == "UNAVAILABLE"
    audit = notices.inspect_notices(sources, target(), store)
    assert audit["complete"] and audit["events"][0]["status"] == "MATCH"
    assert audit["events"][0]["strict_pit"] is False
    assert audit["execution_status"] == "BLOCKED" and audit["pit_level"] == "PARTIAL"
    assert all(d["publication_time"] is None for d in audit["documents"])
    with pytest.raises(ValueError, match="duplicate"):
        notices.inspect_notices(sources * 2, target(), store)
    sources[0]["rows"][0]["effective_day"] = "2025-02-14"
    with pytest.raises(ValueError, match="binding"):
        notices.inspect_notices(sources, target(), store)
    with pytest.raises(ValueError, match="mismatch"):
        notices.extract_notice(b"tampered", next(iter(raw)))


@pytest.mark.parametrize("change", ["date", "codes", "continuity"])
def test_notice_rejects_incorrect_or_ambiguous_text(monkeypatch, change):
    raw = setup_notices(monkeypatch)
    key = "avic-issuer-20250207"
    data = raw[key]
    data = (
        data.replace("2025年2月17日".encode(), "2025年2月18日".encode())
        if change == "date"
        else data.replace(b"300114", b"300115")
        if change == "codes"
        else data.replace("没有发生实质变化".encode(), "已经发生实质变化".encode())
    )
    notices.PROFILES[key]["sha256"] = hashlib.sha256(data).hexdigest()
    with pytest.raises(ValueError):
        notices.extract_notice(data, key)


def test_notice_target_rejects_outside_scope(monkeypatch):
    setup_notices(monkeypatch)
    parent = dict(
        run_id="TEST",
        check_status="COMPLETED",
        lifecycle_audit=dict(checks=[dict(code="sz.300114")]),
        meta=dict(date_start="2025-03-01", date_end="2026-01-01"),
    )
    with pytest.raises(Exception) as error:
        notices.target_for_parent(parent)
    assert error.value.status_code == 422
