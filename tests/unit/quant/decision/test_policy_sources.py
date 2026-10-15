"""Synthetic HTML/CAS fixtures test provenance gates, not actual policy eligibility."""

import copy
import hashlib
import json

import pytest

from marketpulse.quant.decision import policy_sources as policy
from marketpulse.quant.decision.contracts import EvidenceItem
from marketpulse.quant.decision.tools import SourceUnavailable, Stopped

STAMP = "2026-10-12T10:00:00+00:00"
SUBJECT = dict(
    security_id="688012.SH",
    code="688012",
    name="合成测试公司",
    industry="半导体",
    description="合成测试夹具：公司从事半导体设备研发和制造，不是真实经营资料。",
)


class MemoryRepo:
    def __init__(self):
        self.bytes = {}

    def raw(self, raw):
        ref = "blob://sha256/" + hashlib.sha256(raw).hexdigest()
        self.bytes[ref] = raw
        return ref

    def blob(self, value):
        return self.raw(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())

    def raw_bytes(self, ref):
        return self.bytes[ref]

    def value(self, ref):
        return json.loads(self.raw_bytes(ref))


def cn_html(state="FORMAL"):
    title = "合成测试：集成电路装备政策" + ("（征求意见稿）" if state == "CONSULTATION" else "")
    state_text = {
        "FORMAL": "现将《合成集成电路政策》印发给你们，请认真贯彻落实。（此件公开发布）",
        "CONSULTATION": "就本合成政策公开征求意见，尚不是正式适用规则。",
        "NOT_YET_EFFECTIVE": "本政策自2030年1月1日起实施。",
        "REPEALED": "本文件已废止。",
        "UNKNOWN": "本页未给出足以确定发布性质和现行有效性的说明。",
    }[state]
    condition = "合成夹具：适用条件须由相关部门制定，不代表目标公司具备资格或已享受补贴。"
    return (
        "<html><title>合成测试政策原文</title><script>不可信脚本政策正文</script>"
        "<style>不可信样式内容</style><nav>门户导航不得作为条款</nav>"
        '<table class="border-table"><tr><td>发文机关：</td><td>国务院</td>'
        "<td>成文日期：</td><td>2020年07月27日</td></tr>"
        f"<tr><td>标　　题：</td><td>{title}</td><td>发文字号：</td>"
        "<td>国发〔2020〕8号</td><td>发布日期：</td><td>2020年08月04日</td></tr></table>"
        '<table class="border-table pages_content"><tr><td><div id="UCAP-CONTENT">'
        "<p>合成测试夹具，并非真实政策原文。国发〔2020〕8号 集成电路</p>"
        f"<p>{state_text}</p><p>（二）{condition * 13}</p>"
        f"<p>（三十八）{condition * 8}</p><p>（三十九）本段合成材料只用于核验。</p></div>"
        "</td></tr></table><footer>不可信页脚政策信息</footer></html>"
    ).encode()


def us_html():
    clause = (
        "Synthetic fixture only, not actual regulatory text. Items subject to the EAR need "
        "end use, product, destination and license checks; "
        "this does not establish company coverage. "
    )
    return (
        "<title>EAR 744 | Bureau of Industry and Security</title>"
        "<main>Current from the <a>eCFR</a> as of October 12, 2026, 1:45AM EDT "
        '<nav>Fake section 744.23 navigation</nav><div id="744.23">'
        '<h4>§ 744.23 "Supercomputer," "advanced-node integrated circuits," and '
        "semiconductor manufacturing equipment end use controls.</h4>"
        "<p>General prohibition. " + clause * 3 + "</p>"
        '<div id="p-744.23(a)(2)"><p>(2) Advanced-node integrated circuits. '
        + clause
        * 3
        + "</p></div>"
        '<div id="p-744.23(a)(4)"><p>(4) Semiconductor manufacturing equipment. '
        + clause
        * 3
        + "</p></div>"
        "</div><script>Not a clause.</script></main>"
    ).encode()


class FixtureClient:
    def __init__(self, repo, cn=None, us=None, error=None):
        self.repo, self.cn, self.us, self.error = repo, cn or cn_html(), us or us_html(), error
        self.calls = []

    def fetch(self, key, url, *, family, binary):
        self.calls.append(key)
        if self.error:
            raise self.error
        raw = self.cn if family == "POLICY_CN" else self.us
        ref = self.repo.raw(raw)
        return dict(
            key=key,
            url=url,
            source_family=family,
            blob_ref=ref,
            sha256=ref.rsplit("/", 1)[-1],
            fetched_at=STAMP,
        ), raw


def base_data(subject=SUBJECT):
    return dict(
        sources=[],
        evidence=[],
        documents=[dict(subject_id=subject["security_id"])],
        bundle=dict(
            items=[],
            sources=[],
            cutoff_at="2026-10-13T00:00:00+00:00",
            coverage=dict(
                universe_total=0,
                attempted=0,
                available=0,
                missing=0,
                excluded=0,
                universe_ref="synthetic-policy-test",
                member_refs=[],
                source_dates=[],
                method="synthetic fixture",
                status="COMPLETE",
            ),
        ),
    )


def attached(repo=None, **kwargs):
    repo = repo or MemoryRepo()
    client = FixtureClient(repo, **kwargs)
    snapshot = policy.collect(repo, client, SUBJECT)
    return repo, client, policy.attach(repo, base_data(), snapshot)


@pytest.mark.parametrize(
    "state", ["FORMAL", "CONSULTATION", "NOT_YET_EFFECTIVE", "REPEALED", "UNKNOWN"]
)
def test_synthetic_document_states_are_taken_from_self_referential_original_text(state):
    parsed, _ = policy.parse_policy(cn_html(state), "CN-STATE-2020-8", STAMP)
    assert parsed["document_status"] == state
    assert parsed["qualification_status"] == "UNKNOWN"
    assert parsed["company_link_status"] == "HYPOTHESIS"
    assert parsed["current_validity"] == (
        state if state in {"REPEALED", "NOT_YET_EFFECTIVE"} else "UNKNOWN"
    )
    assert parsed["document_kind"] == (
        "CONSULTATION" if state == "CONSULTATION" else "FORMAL_POLICY"
    )
    assert parsed["published_at"] == "2020-08-04"
    assert parsed["effective_at"] == ("2030-01-01" if state == "NOT_YET_EFFECTIVE" else None)


def test_content_table_with_same_css_class_does_not_duplicate_body_into_metadata():
    _, texts = policy.parse_policy(cn_html(), "CN-STATE-2020-8", STAMP)
    assert len(texts["meta"]) < 500
    assert "合成夹具：适用条件" not in texts["meta"]


def test_identical_originals_parse_once_without_sharing_mutable_text_views(monkeypatch):
    parsed = []
    feed = policy.PolicyHTML.feed

    def counted(parser, text):
        parsed.append(text)
        return feed(parser, text)

    monkeypatch.setattr(policy.PolicyHTML, "feed", counted)
    raw = us_html().replace(b"Synthetic fixture only", b"Unique performance fixture")
    first = policy.html_texts(raw, "US-EAR-74423")
    original_body = first["body"]
    first["body"] = "modified caller view"
    second = policy.html_texts(raw, "US-EAR-74423")
    assert second["body"] == original_body
    assert len(parsed) == 1
    policy.html_texts(
        raw.replace(b"Unique performance fixture", b"Changed original fixture"), "US-EAR-74423"
    )
    assert len(parsed) == 2  # New original bytes must never reuse stale parsed text.


def test_other_documents_repeal_does_not_repeal_this_document():
    raw = cn_html().replace("（此件公开发布）".encode(), "（此件公开发布）旧政策已废止。".encode())
    parsed, _ = policy.parse_policy(raw, "CN-STATE-2020-8", STAMP)
    assert parsed["document_status"] == "FORMAL" and parsed["current_validity"] == "UNKNOWN"


def test_collect_attach_and_rebuild_preserve_originals_and_first_fetch():
    repo, client, data = attached()
    assert len(client.calls) == 2
    assert data["policy_report"]["status"] == "PARTIAL"
    assert len(data["policy_report"]["policies"]) == 2
    assert data["evidence"] == data["bundle"]["items"]
    assert {s["source_family"] for s in data["bundle"]["sources"]} == {"POLICY_CN", "POLICY_US"}
    for item in data["bundle"]["items"]:
        policy.verify_item(repo, EvidenceItem.model_validate(item))
        assert item["kind"] == "industry" and item["evidence_id"].startswith("EV-POL-")
        assert item["available_at"] == item["fetched_at"] == STAMP.replace("+00:00", "Z")
        assert "不可信" not in item["text"] and "navigation" not in item["text"]
    for entry in data["policy_snapshot"]["entries"]:
        for span in entry["parsedpolicy"]["excerpts"]:
            assert 200 <= span["end"] - span["start"] <= 500
    us = data["policy_report"]["policies"][1]
    assert us["document_kind"] == "REGULATORY_COMPILATION"
    assert us["version_as_of"] == "2026-10-12"
    assert us["published_at"] is None and us["effective_at"] is None
    assert us["current_validity"] == "UNKNOWN"
    policy.verify(repo, data)
    context = json.dumps(policy.model_context(data), ensure_ascii=False)
    assert "Synthetic fixture only" not in context
    assert "qualification_status" in context


def test_navigation_or_wrong_document_identity_cannot_be_used_as_policy():
    repo = MemoryRepo()
    portal = b"<title>Official policy portal</title><nav>EAR 744.23</nav>"
    client = FixtureClient(repo, cn=portal, us=portal)
    data = policy.attach(repo, base_data(), policy.collect(repo, client, SUBJECT))
    assert data["policy_report"]["status"] == "UNAVAILABLE"
    assert not data["policy_report"]["policies"] and not data["bundle"]["items"]
    assert len(data["sources"]) == 2  # Identical bytes still preserve both source identities.
    assert len(data["policy_report"]["gaps"]) == 2
    policy.verify(repo, data)
    with pytest.raises(ValueError, match="题录"):
        policy.parse_policy(
            cn_html().replace("国务院".encode(), "某地方机关".encode()), "CN-STATE-2020-8", STAMP
        )


def test_failed_originals_remain_honest_gaps_and_stop_propagates():
    repo = MemoryRepo()
    client = FixtureClient(repo, error=SourceUnavailable("synthetic transport failure"))
    snapshot = policy.collect(repo, client, SUBJECT)
    assert len(snapshot["gaps"]) == 2 and not snapshot["entries"]
    data = policy.attach(repo, base_data(), snapshot)
    assert data["policy_report"]["status"] == "UNAVAILABLE"
    policy.verify(repo, data)
    with pytest.raises(Stopped):
        policy.collect(repo, FixtureClient(repo, error=Stopped("synthetic stop")), SUBJECT)


def test_overseas_sources_require_company_semiconductor_relevance():
    repo = MemoryRepo()
    software = dict(SUBJECT, industry="软件开发", description="合成普通软件公司")
    client = FixtureClient(repo)
    snapshot = policy.collect(repo, client, software)
    assert (
        len(client.calls) == 1 and snapshot["entries"][0]["source"]["source_family"] == "POLICY_CN"
    )
    bank = dict(SUBJECT, industry="银行", description="为半导体企业提供金融服务")
    client = FixtureClient(repo)
    data = policy.attach(repo, base_data(bank), policy.collect(repo, client, bank))
    assert not client.calls and data["policy_report"]["status"] == "NOT_APPLICABLE"
    policy.verify(repo, data)


@pytest.mark.parametrize(
    "change", ["locator", "text", "hash", "subject", "report", "snapshot", "source", "extra"]
)
def test_sealed_originals_reject_tampered_policy_views(change):
    repo, _, data = attached()
    damaged = copy.deepcopy(data)
    item = damaged["bundle"]["items"][0]
    if change == "locator":
        item["locator"]["start"] += 1
    elif change == "text":
        item["text"] = "篡改政策原文"
    elif change == "hash":
        item["content_hash"] = "0" * 64
    elif change == "subject":
        item["subject_id"] = "688380.SH"
    elif change == "report":
        damaged["policy_report"]["policies"][0]["qualification_status"] = "ELIGIBLE"
    elif change == "snapshot":
        damaged["policy_snapshot"]["target_subject_id"] = "688380.SH"
    elif change == "source":
        damaged["sources"][0]["sha256"] = "0" * 64
    else:
        damaged["bundle"]["items"].append(dict(item, evidence_id="EV-POL-FAKE"))
    with pytest.raises(ValueError, match="policy"):
        policy.verify(repo, damaged)


@pytest.mark.parametrize("change", ["locator", "hash", "family", "backdate", "bytes"])
def test_item_verifier_rejects_inexact_locators_hashes_and_backdating(change):
    repo, _, data = attached()
    item = copy.deepcopy(data["bundle"]["items"][0])
    if change == "locator":
        item["locator"]["start"] += 1
    elif change == "hash":
        item["content_hash"] = "0" * 64
    elif change == "family":
        item["source_family"] = "POLICY_US"
    elif change == "backdate":
        item["available_at"] = "2026-10-11T10:00:00+00:00"
    else:
        repo.bytes[item["raw_ref"]] += b"tampered bytes"
    with pytest.raises(ValueError):
        policy.verify_item(repo, EvidenceItem.model_validate(item))


def test_future_fetch_cannot_enter_fixed_cutoff_and_target_must_match():
    repo = MemoryRepo()
    snapshot = policy.collect(repo, FixtureClient(repo), SUBJECT)
    data = base_data()
    data["bundle"]["cutoff_at"] = "2026-10-11T00:00:00+00:00"
    with pytest.raises(ValueError, match="cutoff"):
        policy.attach(repo, data, snapshot)
    data = base_data(dict(SUBJECT, security_id="688380.SH"))
    with pytest.raises(ValueError, match="subject"):
        policy.attach(repo, data, snapshot)
