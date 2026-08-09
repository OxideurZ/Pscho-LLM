import yaml

from backend.app.config.settings import REPOSITORY_ROOT

CORPUS = REPOSITORY_ROOT / "tests" / "memory" / "philosophy_cases.yaml"
KINDS = {"personal_fact", "event", "goal", "preference", "belief"}
STATUSES = {"stated", "interpretation", "uncertain"}


def load_cases() -> list[dict[str, object]]:
    document = yaml.safe_load(CORPUS.read_text(encoding="utf-8"))
    assert document["schema_version"] == "1.0"
    assert document["principles"] == {
        "source_policy": "user_only",
        "precision_over_recall": True,
        "assistant_contamination_allowed": False,
    }
    return document["cases"]


def test_philosophy_corpus_has_unique_well_typed_cases() -> None:
    cases = load_cases()
    identifiers = [case["id"] for case in cases]
    assert len(identifiers) == len(set(identifiers))
    assert len(cases) >= 15
    for case in cases:
        assert case["messages"]
        for message in case["messages"]:
            assert message["role"] in {"user", "assistant"}
            assert message["content"]
        for expectation in case.get("expected", []):
            assert expectation["kind"] in KINDS
            assert expectation["epistemic_status"] in STATUSES
        for forbidden in case.get("forbidden", []):
            assert forbidden["kind"] in KINDS


def test_hard_epistemic_and_contamination_cases_are_present() -> None:
    cases = {case["id"]: case for case in load_cases()}

    bad_math = cases["belief_bad_at_math"]
    assert bad_math["expected"] == [{"kind": "belief", "epistemic_status": "stated"}]
    assert {item["kind"] for item in bad_math["forbidden"]} == {"personal_fact"}

    interpretation = cases["interpreted_other_person"]
    assert interpretation["expected"] == [{"kind": "belief", "epistemic_status": "interpretation"}]

    contaminated = cases["assistant_contamination_weak_acknowledgement"]
    assert contaminated["gate"] == "MUST_NOT_CAPTURE"
    assert contaminated["expected"] == []
