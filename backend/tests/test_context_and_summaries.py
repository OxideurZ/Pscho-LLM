import json
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.app.config import Settings
from backend.app.config.prompt import load_prompt
from backend.app.config.settings import REPOSITORY_ROOT
from backend.app.context import ContextBudget, ContextBudgetExceededError, ContextBuilder
from backend.app.conversations import ConversationRepository
from backend.app.conversations.models import Message
from backend.app.conversations.repository import new_id
from backend.app.db import Database
from backend.app.runs import RunRepository
from backend.app.summaries import RollingSummary, SummaryGenerationError, SummaryService
from backend.tests.fakes import FakeBackend


def settings_for(path: Path) -> Settings:
    return Settings(
        data_directory=path.parent / "runtime",
        database_path=path,
        model_expected_sha256="a" * 64,
    )


def metadata() -> dict[str, object]:
    return {
        "model_name": "model",
        "model_sha256": "a" * 64,
        "backend_name": "llama.cpp",
        "backend_version": "b9637",
        "backend_build": "commit",
        "prompt_id": "conversation_system",
        "prompt_version": "0.1.2",
        "prompt_sha256": "b" * 64,
        "generation_config": {"max_tokens": 800},
        "seed": 42,
        "app_version": "test",
        "app_git_commit": "commit",
        "runtime_info": {},
        "context_size": 32768,
    }


def ids() -> dict[str, str]:
    return {
        "user_message_id": new_id("msg"),
        "assistant_message_id": new_id("msg"),
        "session_id": new_id("session"),
        "run_id": new_id("run"),
    }


async def add_turn(
    repository: ConversationRepository,
    conversation_id: str,
    user_content: str,
    assistant_content: str,
) -> tuple[Message, Message]:
    turn = await repository.begin_turn(
        conversation_id=conversation_id,
        client_turn_id=str(uuid4()),
        content=user_content,
        input_type="text",
        ids=ids(),
        run_metadata=metadata(),
    )
    await repository.finalize(
        conversation_id=conversation_id,
        assistant_message_id=turn.assistant_message.id,
        run_id=turn.run_id,
        content=assistant_content,
        message_status="complete",
        run_status="complete",
    )
    messages = await repository.messages(conversation_id)
    return messages[-2], messages[-1]


class StructuredBackend(FakeBackend):
    def __init__(self, summaries: list[RollingSummary]) -> None:
        super().__init__()
        self.summaries = summaries
        self.structured_calls = 0

    async def generate_structured(self, messages, schema):  # type: ignore[no-untyped-def]
        self.structured_calls += 1
        return self.summaries.pop(0)


def empty_summary(**overrides: list[str]) -> RollingSummary:
    values: dict[str, list[str]] = {
        "conversation_progression": [],
        "user_stated_facts": [],
        "user_interpretations": [],
        "assistant_proposals": [],
        "topics_discussed": [],
        "open_questions": [],
        "decisions_or_actions": [],
    }
    values.update(overrides)
    return RollingSummary.model_validate(values)


@pytest.mark.asyncio
async def test_short_context_uses_raw_messages_and_current_user_once(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.sqlite", REPOSITORY_ROOT / "migrations")
    await database.migrate()
    repository = ConversationRepository(database)
    conversation = await repository.create()
    await add_turn(repository, conversation.id, "Premier message", "Première réponse")
    current = await repository.begin_turn(
        conversation_id=conversation.id,
        client_turn_id=str(uuid4()),
        content="Message courant unique",
        input_type="text",
        ids=ids(),
        run_metadata=metadata(),
    )
    provider = AsyncMock()
    builder = ContextBuilder(repository, provider)

    result = await builder.build_context(
        conversation_id=conversation.id,
        current_message_id=current.user_message.id,
        system_prompt="SYSTEM",
        budget=ContextBudget(10_000, 100, 100, 1000, 5000),
    )

    assert result.summary_id is None
    assert [message.content for message in result.messages] == [
        "SYSTEM",
        "Premier message",
        "Première réponse",
        "Message courant unique",
    ]
    assert sum(message.content == "Message courant unique" for message in result.messages) == 1
    provider.summarize.assert_not_awaited()


@pytest.mark.asyncio
async def test_long_context_uses_summary_and_recent_raw_within_budget(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.sqlite", REPOSITORY_ROOT / "migrations")
    await database.migrate()
    repository = ConversationRepository(database)
    conversation = await repository.create()
    for index in range(3):
        await add_turn(
            repository,
            conversation.id,
            f"Ancien {index} " + "u" * 180,
            f"Réponse {index} " + "a" * 180,
        )
    current = await repository.begin_turn(
        conversation_id=conversation.id,
        client_turn_id=str(uuid4()),
        content="Question actuelle",
        input_type="text",
        ids=ids(),
        run_metadata=metadata(),
    )
    summary_json = empty_summary(
        conversation_progression=["Les échanges anciens ont été condensés."]
    ).model_dump_json()
    provider = AsyncMock()
    provider.summarize.return_value = ("summary_1", summary_json)
    builder = ContextBuilder(repository, provider)
    budget = ContextBudget(1000, 100, 100, 400, 250)

    result = await builder.build_context(
        conversation_id=conversation.id,
        current_message_id=current.user_message.id,
        system_prompt="SYSTEM",
        budget=budget,
    )

    assert result.summary_id == "summary_1"
    assert result.input_token_upper_bound <= budget.max_input_tokens
    assert summary_json in result.messages[0].content
    assert result.messages[-1].content == "Question actuelle"
    assert sum(message.content == "Question actuelle" for message in result.messages) == 1
    provider.summarize.assert_awaited_once()


@pytest.mark.asyncio
async def test_context_budget_rejects_current_user_that_cannot_fit(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.sqlite", REPOSITORY_ROOT / "migrations")
    await database.migrate()
    repository = ConversationRepository(database)
    conversation = await repository.create()
    current = await repository.begin_turn(
        conversation_id=conversation.id,
        client_turn_id=str(uuid4()),
        content="x" * 500,
        input_type="text",
        ids=ids(),
        run_metadata=metadata(),
    )

    with pytest.raises(ContextBudgetExceededError, match="Current USER"):
        await ContextBuilder(repository, AsyncMock()).build_context(
            conversation_id=conversation.id,
            current_message_id=current.user_message.id,
            system_prompt="SYSTEM",
            budget=ContextBudget(400, 100, 100, 100, 100),
        )


@pytest.mark.asyncio
async def test_partial_failed_interrupted_deleted_and_excluded_context_policy(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "app.sqlite", REPOSITORY_ROOT / "migrations")
    await database.migrate()
    repository = ConversationRepository(database)
    conversation = await repository.create()

    interrupted = await repository.begin_turn(
        conversation_id=conversation.id,
        client_turn_id=str(uuid4()),
        content="Interrupted user",
        input_type="text",
        ids=ids(),
        run_metadata=metadata(),
    )
    await repository.finalize(
        conversation_id=conversation.id,
        assistant_message_id=interrupted.assistant_message.id,
        run_id=interrupted.run_id,
        content="Interrupted partial",
        message_status="interrupted",
        run_status="cancelled",
    )
    failed_empty = await repository.begin_turn(
        conversation_id=conversation.id,
        client_turn_id=str(uuid4()),
        content="Failed empty user",
        input_type="text",
        ids=ids(),
        run_metadata=metadata(),
    )
    await repository.finalize(
        conversation_id=conversation.id,
        assistant_message_id=failed_empty.assistant_message.id,
        run_id=failed_empty.run_id,
        content="",
        message_status="failed",
        run_status="failed",
    )
    failed_partial = await repository.begin_turn(
        conversation_id=conversation.id,
        client_turn_id=str(uuid4()),
        content="Failed partial user",
        input_type="text",
        ids=ids(),
        run_metadata=metadata(),
    )
    await repository.finalize(
        conversation_id=conversation.id,
        assistant_message_id=failed_partial.assistant_message.id,
        run_id=failed_partial.run_id,
        content="Failed partial",
        message_status="failed",
        run_status="failed",
    )
    excluded_user, excluded_assistant = await add_turn(
        repository, conversation.id, "Excluded user", "Excluded assistant"
    )
    current = await repository.begin_turn(
        conversation_id=conversation.id,
        client_turn_id=str(uuid4()),
        content="Current user",
        input_type="text",
        ids=ids(),
        run_metadata=metadata(),
    )
    async with database.connect() as connection:
        await connection.execute(
            "UPDATE messages SET excluded_from_ai = 1 WHERE id IN (?, ?)",
            (excluded_user.id, excluded_assistant.id),
        )
        await connection.commit()

    context = await repository.context_messages(conversation.id, current.user_message.id)

    assert [message.content for message in context] == [
        "Interrupted user",
        "Interrupted partial",
        "Failed empty user",
        "Failed partial user",
        "Failed partial",
        "Current user",
    ]


@pytest.mark.asyncio
async def test_summary_lineage_sources_runs_and_raw_messages_are_preserved(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "app.sqlite", REPOSITORY_ROOT / "migrations")
    await database.migrate()
    repository = ConversationRepository(database)
    conversation = await repository.create()
    first_sources = list(
        await add_turn(repository, conversation.id, "Mon mot est CARIBOU", "Je le note ici.")
    )
    second_sources = list(
        await add_turn(repository, conversation.id, "Je confirme CARIBOU", "Confirmation reçue.")
    )
    backend = StructuredBackend(
        [
            empty_summary(user_stated_facts=["mot CARIBOU"]),
            empty_summary(user_stated_facts=["CARIBOU"]),
        ]
    )
    service = SummaryService(
        settings_for(database.path),
        backend,
        repository,
        RunRepository(database.path),
        REPOSITORY_ROOT,
    )

    first_id, _ = await service.summarize(conversation.id, first_sources)
    second_id, second_json = await service.summarize(
        conversation.id, [*first_sources, *second_sources]
    )

    assert first_id != second_id
    assert json.loads(second_json)["user_stated_facts"] == ["CARIBOU"]
    lineage = await repository.summary_lineage(second_id)
    assert [item["id"] for item in lineage] == [first_id, second_id]
    assert await repository.summary_covered_message_ids(second_id) == {
        message.id for message in [*first_sources, *second_sources]
    }
    assert len(await repository.messages(conversation.id)) == 4
    async with database.connect() as connection:
        runs = await (
            await connection.execute(
                """
                SELECT run_kind, status, prompt_id, prompt_version
                FROM model_runs WHERE run_kind = 'rolling_summary' ORDER BY started_at
                """
            )
        ).fetchall()
        summaries = await (
            await connection.execute(
                """
                SELECT schema_version, parent_summary_id, prompt_sha256
                FROM summaries ORDER BY created_at
                """
            )
        ).fetchall()
    assert runs == [
        ("rolling_summary", "complete", "rolling_summary", "0.1.1"),
        ("rolling_summary", "complete", "rolling_summary", "0.1.1"),
    ]
    assert summaries[0][0:2] == ("1.0", None)
    assert summaries[1][0:2] == ("1.0", first_id)
    prompt = load_prompt(REPOSITORY_ROOT, "rolling_summary", "0.1.1")
    assert all(summary[2] == prompt.sha256 for summary in summaries)


@pytest.mark.asyncio
async def test_assistant_proposal_cannot_become_user_fact(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.sqlite", REPOSITORY_ROOT / "migrations")
    await database.migrate()
    repository = ConversationRepository(database)
    conversation = await repository.create()
    sources = list(
        await add_turn(
            repository,
            conversation.id,
            "X n'a pas répondu.",
            "Peut-être qu'il se désintéresse.",
        )
    )
    invalid = empty_summary(user_stated_facts=["X se désintéresse"])
    backend = StructuredBackend([invalid, invalid])
    service = SummaryService(
        settings_for(database.path),
        backend,
        repository,
        RunRepository(database.path),
        REPOSITORY_ROOT,
    )

    with pytest.raises(SummaryGenerationError):
        await service.summarize(conversation.id, sources)

    assert await repository.latest_summary(conversation.id) is None
    async with database.connect() as connection:
        failed = await (
            await connection.execute(
                """
                SELECT COUNT(*) FROM model_runs
                WHERE run_kind = 'rolling_summary'
                  AND status = 'failed'
                  AND error_code = 'SUMMARY_VALIDATION_FAILED'
                """
            )
        ).fetchone()
    assert failed == (2,)
