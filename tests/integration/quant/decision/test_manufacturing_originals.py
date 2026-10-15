"""A demand-side original may become own-company proof only after issuer binding."""

import copy

from test_candidates_api import source_fixture
from test_decision_api import setup as setup

from marketpulse.quant.contracts import digest
from marketpulse.quant.decision import industry_sources, medium


def test_manufacturing_original_ownership_is_explicit_and_legacy_stays_unchanged(setup):
    app, _ = setup
    repo = app.state.decision_store
    doc = copy.deepcopy(source_fixture(repo, manufacturing=True)["documents"][0])
    parsed = repo.value(doc["text_ref"])
    parsed["subject_id"] = "688981.SH"
    parsed["pages"] = [
        "合成夹具，非真实公司资料。资本开支及资金来源 晶圆制造代工产能利用率现金回收风险。" * 20
    ]
    doc.update(subject_id="688981.SH", text_ref=repo.blob(parsed), text_hash=digest(parsed))
    snapshot = dict(
        subject=dict(security_id="688981.SH"),
        responses=[],
        captured_at="2026-10-13T11:00:00+00:00",
        manufacturer_document=doc,
        business_protocol="manufacturing-originals-2026-10-14-v2",
    )
    items, _, _ = industry_sources.original_items(repo, snapshot)
    assert items and all(i["kind"] == "disclosure" for i in items)
    data = dict(
        bundle=dict(items=items),
        industry_documents=[doc],
        industry_report=dict(target_exposure_refs=[]),
    )
    assert medium.industry_initial_evidence(data, "688981.SH") == items
    # The same PDF can already have general P1 excerpts; only the bounded manufacturing
    # excerpts belong in this addition, rather than pulling in every page from that PDF.
    extra = copy.deepcopy(items[0])
    extra["evidence_id"] = "EV-P1-GENERAL-SYNTHETIC"
    data["bundle"]["items"] = items + [extra]
    assert medium.industry_initial_evidence(data, "688981.SH") == items
    mislabeled = copy.deepcopy(data)
    for e in mislabeled["bundle"]["items"]:
        e["subject_id"] = "688012.SH"
    assert medium.industry_initial_evidence(mislabeled, "688012.SH") == []
    foreign = copy.deepcopy(snapshot)
    foreign["subject"]["security_id"] = "688012.SH"
    assert all(i["kind"] == "industry" for i in industry_sources.original_items(repo, foreign)[0])
    legacy = dict(snapshot)
    legacy.pop("business_protocol")
    assert all(i["kind"] == "industry" for i in industry_sources.original_items(repo, legacy)[0])
