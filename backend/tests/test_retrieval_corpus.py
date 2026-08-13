import yaml

from backend.app.config.settings import REPOSITORY_ROOT

CORPUS = REPOSITORY_ROOT / "tests" / "retrieval" / "retrieval_cases.yaml"


def test_retrieval_corpus_has_disjoint_splits_and_required_classes() -> None:
    document = yaml.safe_load(CORPUS.read_text(encoding="utf-8"))
    cases = document["cases"]
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids))
    assert {case["split"] for case in cases} == {"development", "heldout"}
    required = {
        "exact_lexical_match",
        "semantic_paraphrase",
        "rare_proper_noun",
        "number_date",
        "preference",
        "belief",
        "goal",
        "event",
        "raw_detail_fallback",
        "current_state_update",
        "historical_state_query",
        "irrelevant_query",
        "greeting",
        "current_user_contradiction",
        "disabled_memory",
        "deleted_memory",
        "ambiguous_entity",
        "same_information_memory_raw",
        "recent_context_duplicate",
        "historical_prompt_injection",
        "cross_conversation_retrieval",
        "voice_origin_memory",
    }
    actual = {classification for case in cases for classification in case["classes"]}
    assert required <= actual


def test_hard_cases_declare_positive_or_zero_policy() -> None:
    document = yaml.safe_load(CORPUS.read_text(encoding="utf-8"))
    hard = [case for case in document["cases"] if case.get("hard")]
    assert hard
    for case in hard:
        assert case.get("must_retrieve") or case.get("expected_zero")
        if not case.get("expected_zero"):
            assert case.get("must_retrieve") or case.get("must_not_retrieve")
