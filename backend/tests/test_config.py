from hashlib import sha256

from backend.app.config.prompt import load_prompt
from backend.app.config.settings import REPOSITORY_ROOT
from backend.app.llm.models import GenerationOptions


def test_prompt_is_loaded_and_hashed_from_file() -> None:
    prompt = load_prompt(REPOSITORY_ROOT, "conversation_system", "0.1.0")
    assert prompt.content.startswith("Tu es Psych-local")
    assert prompt.sha256 == sha256(prompt.content.encode()).hexdigest()
    assert prompt.sha256 == "c6050fcbe1ad7dfcb878586a726429783331534fb5dce4ca6c4d5ce97979da7e"


def test_prompt_v011_is_a_versioned_style_only_revision() -> None:
    baseline = load_prompt(REPOSITORY_ROOT, "conversation_system", "0.1.0")
    revised = load_prompt(REPOSITORY_ROOT, "conversation_system", "0.1.1")

    assert revised.sha256 != baseline.sha256
    assert "Distingue clairement les faits" in revised.content
    assert "N'établis pas de diagnostic" in revised.content
    assert "ni titre, ni liste à" in revised.content
    assert "puces, ni cadre numéroté" in revised.content
    assert "au plus une question" in revised.content
    assert "significative à la fois" in revised.content


def test_generation_options_are_stable_and_do_not_share_stop_lists() -> None:
    first = GenerationOptions(seed=42)
    second = GenerationOptions(seed=42)
    first.stop.append("END")

    assert second.stop == []
    assert '"seed":42' in first.model_dump_json()
