"""Synthetic business profiles test method admission, never market predictions."""

import copy
import hashlib
import json

import pytest

from marketpulse.quant.decision import routing


class Memory:
    def __init__(self):
        self.values = {}

    def raw(self, value):
        ref = "blob://sha256/" + hashlib.sha256(value).hexdigest()
        self.values[ref] = value
        return ref

    def raw_bytes(self, ref):
        return self.values[ref]


def inputs(description, text):
    repo = Memory()
    subject = dict(
        security_id="688012.SH",
        code="688012",
        name="合成样本",
        industry="半导体",
        description=description,
    )
    raw = json.dumps(
        {
            "jbzl": [
                dict(
                    SECURITY_CODE="688012",
                    SECURITY_NAME_ABBR="合成样本",
                    ORG_PROFILE=description,
                    EM2016="半导体",
                )
            ]
        }
    ).encode()
    ref = repo.raw(raw)
    item = dict(
        evidence_id="EV-COMPANY",
        subject_id="688012.SH",
        kind="disclosure",
        text=text,
        available_at="2026-10-13T03:00:00+00:00",
        raw_ref="synthetic-original",
    )
    data = dict(
        sources=[
            dict(
                key="profile",
                source_family="EASTMONEY_PROFILE",
                blob_ref=ref,
                sha256=hashlib.sha256(raw).hexdigest(),
                fetched_at="2026-10-13T03:00:00+00:00",
            )
        ],
        bundle=dict(cutoff_at="2026-10-13T03:30:00+00:00", items=[item]),
        industry_report=dict(
            template_status="CONDITIONAL_ENABLED", target_exposure_refs=["EV-COMPANY"]
        ),
        knowledge_notes=[],
        policy_report=dict(policies=[]),
    )
    return repo, subject, data


def test_role_specific_primary_methods_and_missing_valuation():
    repo, subject, data = inputs(
        "主营半导体刻蚀设备的研发生产销售。", "本公司研发刻蚀设备并开展设备验收。"
    )
    prepared = routing.prepare(repo, data, subject, 3)
    assert prepared["method_routing"]["profile"]["primary_role"] == "EQUIPMENT"
    candidates = {c["method_id"]: c for c in prepared["method_routing"]["candidates"]}
    assert candidates["EQ-TRANSMISSION"]["status"] == "CONDITIONAL"
    assert candidates["MFG-OPERATIONS"]["status"] == "WRONG_ROLE"
    assert candidates["M02"]["status"] == "MISSING_INPUTS"
    with pytest.raises(ValueError):
        routing.guard_plan(
            dict(
                primary_method_id="MFG-OPERATIONS",
                auxiliary_method_ids=[],
                selection_reason="合成误配",
            ),
            prepared,
        )
    with pytest.raises(ValueError):
        routing.guard_plan(
            dict(primary_method_id="M02", auxiliary_method_ids=[], selection_reason="合成缺输入"),
            prepared,
        )


def test_manufacturing_and_unknown_never_inherit_equipment_logic():
    repo, subject, data = inputs(
        "本公司主要提供晶圆代工制造服务。", "本公司提供晶圆代工服务并扩充制造产能。"
    )
    prepared = routing.prepare(repo, data, subject, 3)
    assert prepared["method_routing"]["profile"]["primary_role"] == "MANUFACTURING"
    candidates = {c["method_id"]: c for c in prepared["method_routing"]["candidates"]}
    assert candidates["MFG-OPERATIONS"]["status"] == "CONDITIONAL"
    assert candidates["EQ-TRANSMISSION"]["status"] == "WRONG_ROLE"
    repo, subject, data = inputs("主营业务未核定。", "合成经营披露。")
    prepared = routing.prepare(repo, data, subject, 3)
    assert prepared["method_routing"]["profile"]["primary_role"] == "UNKNOWN"
    assert routing.allowed_primary(prepared) == ["Q-DISCLOSURE"]


def test_identity_cutoff_and_plan_tampering_are_rejected():
    repo, subject, data = inputs("主营刻蚀设备研发生产。", "本公司刻蚀设备验收。")
    bad = copy.deepcopy(subject)
    bad["security_id"] = "688981.SH"
    with pytest.raises(ValueError):
        routing.prepare(repo, data, bad, 3)
    data["bundle"]["items"][0]["available_at"] = "2026-10-14T03:00:00+00:00"
    with pytest.raises(ValueError):
        routing.prepare(repo, data, subject, 3)
    with pytest.raises(ValueError):
        routing.prepare(repo, data, subject, 0)


def test_profile_future_and_current_business_conflicts():
    repo, subject, data = inputs("主营刻蚀设备研发生产。", "本公司刻蚀设备验收。")
    data["sources"][0]["fetched_at"] = "2026-10-14T03:00:00+00:00"
    with pytest.raises(ValueError):
        routing.prepare(repo, data, subject, 3)
    repo, subject, data = inputs(
        "主营刻蚀设备研发生产。", "已剥离刻蚀设备业务，当前仅晶圆代工，扩充产能。"
    )
    prepared = routing.prepare(repo, data, subject, 3)
    assert prepared["method_routing"]["profile"]["primary_role"] == "MANUFACTURING"
    assert routing.allowed_primary(prepared) == ["MFG-OPERATIONS"]
    repo, subject, data = inputs(
        "晶圆代工，采购半导体设备进行研发生产。", "晶圆代工产能利用率尚待核查。"
    )
    assert (
        routing.prepare(repo, data, subject, 3)["method_routing"]["profile"]["primary_role"]
        == "MANUFACTURING"
    )
    repo, subject, data = inputs(
        "不从事半导体设备研发及制造，主营晶圆代工。", "晶圆代工制造产能扩充。"
    )
    prepared = routing.prepare(repo, data, subject, 3)
    assert prepared["method_routing"]["profile"]["primary_role"] == "MANUFACTURING"
    assert routing.allowed_primary(prepared) == ["MFG-OPERATIONS"]


def test_manufacturing_needs_operating_material_and_selection_cannot_change():
    repo, subject, data = inputs("本公司提供晶圆代工服务。", "本公司召开股东会。")
    prepared = routing.prepare(repo, data, subject, 3)
    assert routing.allowed_primary(prepared) == ["Q-DISCLOSURE"]
    plan = dict(
        primary_method_id="Q-DISCLOSURE",
        auxiliary_method_ids=[],
        selection_reason="只有原文有限核查",
        focus_questions=["合成核查"],
    )
    selected = routing.select(prepared, plan)
    routing.verify(repo, selected)
    selected["method_routing"]["primary_method_id"] = "M02"
    with pytest.raises(ValueError):
        routing.verify(repo, selected)
