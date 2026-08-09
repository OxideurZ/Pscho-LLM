from backend.app.memory.repository import normalize_level0


def test_level0_normalization_changes_only_non_semantic_form() -> None:
    assert normalize_level0("  J'AIME courir ! ") == normalize_level0("j'aime courir")


def test_level0_keeps_paraphrases_and_distinct_referents_separate() -> None:
    assert normalize_level0("J'étudie à l'EPFL.") != normalize_level0(
        "J'étudie la physique à l'EPFL."
    )
    assert normalize_level0("Mon amie Alex habite Genève.") != normalize_level0(
        "Mon collègue Alex travaille avec moi."
    )
