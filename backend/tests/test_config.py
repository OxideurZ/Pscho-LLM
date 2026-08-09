from hashlib import sha256

from backend.app.config.prompt import load_prompt
from backend.app.config.settings import REPOSITORY_ROOT
from backend.app.llm.models import GenerationOptions


def test_prompt_is_loaded_and_hashed_from_file() -> None:
    prompt = load_prompt(REPOSITORY_ROOT, "conversation_system", "0.1.0")
    assert prompt.content.startswith("Tu es Psych-local")
    assert prompt.sha256 == sha256(prompt.content.encode()).hexdigest()


def test_generation_options_are_stable_and_do_not_share_stop_lists() -> None:
    first = GenerationOptions(seed=42)
    second = GenerationOptions(seed=42)
    first.stop.append("END")

    assert second.stop == []
    assert '"seed":42' in first.model_dump_json()
