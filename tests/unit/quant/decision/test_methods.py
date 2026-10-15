from datetime import datetime

from marketpulse.quant.decision.methods import load_cases, load_methods, select_methods

AT = datetime.fromisoformat("2026-10-12T11:00:00+08:00")


def test_all_eight_methods_have_actual_ai_review_and_no_validity_claim():
    cards = load_methods()
    assert [c.method_id for c in cards] == [f"M0{i}" for i in range(1, 9)]
    for card in cards:
        assert card.validation_status == "UNVALIDATED"
        assert {r.identity for r in card.review_records} == {"AI_METHOD_REVIEWED"}
        assert card.counterexamples and card.forbidden_inferences and card.source_refs


def test_daily_data_cannot_enable_financial_or_order_book_methods():
    result = select_methods("single", "NEXT_SESSION", {"daily_prices"}, AT)
    by_id = {r.method_id: r for r in result}
    assert by_id["M01"].applicability == "WRONG_HORIZON"
    assert by_id["M04"].applicability == "MISSING_INPUTS"
    assert "flow_proxy" in by_id["M04"].missing_inputs
    assert all("order_book" not in c.input_requirements for c in load_methods())


def test_complete_inputs_enable_only_matching_methods():
    card = load_methods()[0]
    selected = select_methods("single", "MEDIUM_TERM", set(card.input_requirements), AT)
    assert selected[0].applicability == "APPLICABLE"


def test_fixed_48_cases_are_material_bound_and_balanced_without_evaluation_claim():
    cases = load_cases()
    assert len(cases) == len({c.case_id for c in cases}) == 48
    assert sum(c.split == "development" for c in cases) == 24
    for prefix, total in [("B", 24), ("D", 12), ("E", 12)]:
        group = [c for c in cases if c.case_id.startswith(prefix)]
        assert len(group) == total
        assert sum(c.split == "development" for c in group) == total // 2
        assert all(c.material_kind == "SYNTHETIC_GATE_FIXTURE" for c in group)
        assert all(c.status == "REGISTERED" and c.fixture_json for c in group)
    assert {c.case_id for c in cases if c.case_id.startswith("D") and c.split == "development"} == {
        "D01",
        "D03",
        "D05",
        "D07",
        "D09",
        "D11",
    }
