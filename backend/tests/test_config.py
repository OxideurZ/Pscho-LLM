from hashlib import sha256

from backend.app.config.prompt import load_prompt
from backend.app.config.settings import REPOSITORY_ROOT, Settings
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


def test_prompt_v012_tightens_style_without_dropping_epistemic_rules() -> None:
    revised = load_prompt(REPOSITORY_ROOT, "conversation_system", "0.1.2")

    assert "Distingue clairement les faits" in revised.content
    assert "N'établis pas de diagnostic" in revised.content
    assert "un à trois" in revised.content
    assert "paragraphes courts" in revised.content
    assert "relève doucement la tension" in revised.content


def test_g0_retrieval_runtime_defaults_are_pinned_and_cpu_only() -> None:
    settings = Settings(_env_file=None)

    assert settings.embedding_model_name == "Qwen/Qwen3-Embedding-0.6B"
    assert settings.embedding_model_revision == "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
    assert settings.embedding_model_expected_sha256 == (
        "0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd"
    )
    assert settings.reranker_model_name == "Qwen/Qwen3-Reranker-0.6B"
    assert settings.reranker_model_revision == "e61197ed45024b0ed8a2d74b80b4d909f1255473"
    assert settings.reranker_model_expected_sha256 == (
        "27cd75a405b9c1b46b59abfd88aaa209e6fed2a1972cde9b70e7659537c5e65b"
    )
    assert settings.retrieval_device == "cpu"
    assert settings.retrieval_embedding_dimensions == 1024
    assert settings.retrieval_embedding_dtype == "float32"
    assert settings.retrieval_rerank_top_k == 8
    assert settings.retrieval_interactive_timeout_ms == 6000


def test_generation_options_are_stable_and_do_not_share_stop_lists() -> None:
    first = GenerationOptions(seed=42)
    second = GenerationOptions(seed=42)
    first.stop.append("END")

    assert second.stop == []
    assert '"seed":42' in first.model_dump_json()
