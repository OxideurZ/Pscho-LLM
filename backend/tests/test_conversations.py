import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import aiosqlite
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.config.settings import REPOSITORY_ROOT
from backend.app.context import ContextBuilder
from backend.app.conversations import ConversationBusyError, ConversationRepository
from backend.app.conversations.repository import new_id
from backend.app.conversations.service import ConversationChatService
from backend.app.db import Database
from backend.app.llm.errors import LLMBackendProtocolError
from backend.app.llm.models import GenerationOptions, LLMMessage, LLMStreamEvent
from backend.app.main import create_app
from backend.app.runs import ActiveRun, RunRegistry
from backend.tests.fakes import FakeBackend


def settings_for(path: Path, **overrides: object) -> Settings:
    return Settings(
        data_directory=path.parent / "runtime",
        database_path=path,
        model_expected_sha256="a" * 64,
        **overrides,
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
        "generation_config": {"max_tokens": 800, "seed": 42},
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


class DeltaBackend(FakeBackend):
    def __init__(self, chunks: list[str], wait_after_chunks: bool = False) -> None:
        super().__init__()
        self.chunks = chunks
        self.wait_after_chunks = wait_after_chunks

    async def chat_stream(
        self,
        messages: list[LLMMessage],
        options: GenerationOptions,
        run: ActiveRun,
    ) -> AsyncIterator[LLMStreamEvent]:
        self.requests.append(messages)
        yield LLMStreamEvent(type="generation_started")
        for chunk in self.chunks:
            yield LLMStreamEvent(type="delta", text=chunk)
        if self.wait_after_chunks:
            try:
                await asyncio.Event().wait()
            finally:
                self.closed = True


class CrashOnceBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.crashed = False

    async def chat_stream(
        self,
        messages: list[LLMMessage],
        options: GenerationOptions,
        run: ActiveRun,
    ) -> AsyncIterator[LLMStreamEvent]:
        self.requests.append(messages)
        yield LLMStreamEvent(type="generation_started")
        if not self.crashed:
            self.crashed = True
            yield LLMStreamEvent(type="delta", text="partiel")
            raise LLMBackendProtocolError("raw upstream payload must stay private")
        yield LLMStreamEvent(type="delta", text="repris")


def test_crud_turn_order_and_retry_are_persistent(tmp_path: Path) -> None:
    path = tmp_path / "app.sqlite"
    backend = FakeBackend()
    app = create_app(settings_for(path), backend)
    client_turn_id = str(uuid4())

    with TestClient(app) as client:
        created = client.post("/v1/conversations", json={"title": "Test"})
        conversation_id = created.json()["id"]
        first = client.post(
            f"/v1/conversations/{conversation_id}/turns",
            json={"client_turn_id": client_turn_id, "content": "Bonjour persistant"},
        )
        retry = client.post(
            f"/v1/conversations/{conversation_id}/turns",
            json={"client_turn_id": client_turn_id, "content": "Texte ignoré au retry"},
        )
        messages = client.get(f"/v1/conversations/{conversation_id}/messages").json()["items"]
        renamed = client.patch(
            f"/v1/conversations/{conversation_id}",
            json={"title": "Renommée", "archived": True},
        )
        active = client.get("/v1/conversations").json()["items"]
        archived = client.get("/v1/conversations?include_archived=true").json()["items"]

    assert created.status_code == 201
    assert first.status_code == 200
    assert '"reused":false' in first.text
    assert first.text.endswith("event: done\ndata: {}\n\n")
    assert retry.status_code == 200
    assert '"reused":true' in retry.text
    assert '"idempotency_code":"TURN_ALREADY_EXISTS"' in retry.text
    assert "Bonjour" in retry.text
    message_states = [
        (message["sequence_no"], message["role"], message["status"]) for message in messages
    ]
    assert message_states == [
        (1, "user", "complete"),
        (2, "assistant", "complete"),
    ]
    assert messages[0]["content"] == "Bonjour persistant"
    assert messages[1]["content"] == "Bonjour"
    assert messages[1]["model_run_id"]
    assert renamed.json()["title"] == "Renommée"
    assert renamed.json()["archived_at"] is not None
    assert active == []
    assert len(archived) == 1
    assert len(backend.requests) == 1
    upstream_user = [
        message.content for message in backend.requests[0] if message.role.value == "user"
    ]
    assert upstream_user == ["Bonjour persistant"]


def test_soft_deleted_conversation_is_not_accessible(tmp_path: Path) -> None:
    app = create_app(settings_for(tmp_path / "app.sqlite"), FakeBackend())
    with TestClient(app) as client:
        conversation_id = client.post("/v1/conversations", json={}).json()["id"]
        deleted = client.delete(f"/v1/conversations/{conversation_id}")
        fetched = client.get(f"/v1/conversations/{conversation_id}")
        messages = client.get(f"/v1/conversations/{conversation_id}/messages")
    assert deleted.status_code == 204
    assert fetched.status_code == 410
    assert fetched.json()["error"]["code"] == "CONVERSATION_DELETED"
    assert messages.status_code == 410


@pytest.mark.asyncio
async def test_concurrent_duplicate_turn_is_created_once(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.sqlite", REPOSITORY_ROOT / "migrations")
    await database.migrate()
    repository = ConversationRepository(database)
    conversation = await repository.create()
    client_turn_id = str(uuid4())

    first, second = await asyncio.gather(
        repository.begin_turn(
            conversation_id=conversation.id,
            client_turn_id=client_turn_id,
            content="Même tour",
            input_type="text",
            ids=ids(),
            run_metadata=metadata(),
        ),
        repository.begin_turn(
            conversation_id=conversation.id,
            client_turn_id=client_turn_id,
            content="Même tour",
            input_type="text",
            ids=ids(),
            run_metadata=metadata(),
        ),
    )

    assert sorted((first.created, second.created)) == [False, True]
    async with database.connect() as connection:
        counts = await (
            await connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM messages),
                  (SELECT COUNT(*) FROM model_runs),
                  (SELECT next_sequence_no FROM conversations WHERE id = ?)
                """,
                (conversation.id,),
            )
        ).fetchone()
    assert counts == (2, 1, 3)


@pytest.mark.asyncio
async def test_different_turn_is_rejected_while_conversation_busy(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.sqlite", REPOSITORY_ROOT / "migrations")
    await database.migrate()
    repository = ConversationRepository(database)
    conversation = await repository.create()
    await repository.begin_turn(
        conversation_id=conversation.id,
        client_turn_id=str(uuid4()),
        content="Premier",
        input_type="text",
        ids=ids(),
        run_metadata=metadata(),
    )

    with pytest.raises(ConversationBusyError):
        await repository.begin_turn(
            conversation_id=conversation.id,
            client_turn_id=str(uuid4()),
            content="Second",
            input_type="text",
            ids=ids(),
            run_metadata=metadata(),
        )


def test_busy_conflict_is_an_http_409(tmp_path: Path) -> None:
    app = create_app(settings_for(tmp_path / "app.sqlite"), FakeBackend())
    with TestClient(app) as client:
        conversation_id = client.post("/v1/conversations", json={}).json()["id"]
        asyncio.run(
            app.state.conversation_repository.begin_turn(
                conversation_id=conversation_id,
                client_turn_id=str(uuid4()),
                content="Actif",
                input_type="text",
                ids=ids(),
                run_metadata=metadata(),
            )
        )
        response = client.post(
            f"/v1/conversations/{conversation_id}/turns",
            json={"client_turn_id": str(uuid4()), "content": "Concurrent"},
        )

    assert response.status_code == 409
    assert response.json() == {"error": {"code": "CONVERSATION_BUSY", "retryable": True}}


@pytest.mark.asyncio
async def test_initial_turn_transaction_is_all_or_nothing(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.sqlite", REPOSITORY_ROOT / "migrations")
    await database.migrate()
    repository = ConversationRepository(database)
    conversation = await repository.create()
    repository._insert_model_run = AsyncMock(side_effect=RuntimeError("injected"))  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="injected"):
        await repository.begin_turn(
            conversation_id=conversation.id,
            client_turn_id=str(uuid4()),
            content="Rollback",
            input_type="text",
            ids=ids(),
            run_metadata=metadata(),
        )

    async with database.connect() as connection:
        counts = await (
            await connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM messages),
                  (SELECT COUNT(*) FROM model_runs),
                  (SELECT COUNT(*) FROM sessions),
                  (SELECT next_sequence_no FROM conversations WHERE id = ?)
                """,
                (conversation.id,),
            )
        ).fetchone()
    assert counts == (0, 0, 0, 1)


@pytest.mark.asyncio
async def test_session_rotates_after_timeout_without_changing_message_order(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.sqlite", REPOSITORY_ROOT / "migrations")
    await database.migrate()
    repository = ConversationRepository(database, session_timeout_seconds=60)
    conversation = await repository.create()
    first = await repository.begin_turn(
        conversation_id=conversation.id,
        client_turn_id=str(uuid4()),
        content="Premier",
        input_type="text",
        ids=ids(),
        run_metadata=metadata(),
    )
    await repository.finalize(
        conversation_id=conversation.id,
        assistant_message_id=first.assistant_message.id,
        run_id=first.run_id,
        content="Réponse",
        message_status="complete",
        run_status="complete",
    )
    within_timeout = await repository.begin_turn(
        conversation_id=conversation.id,
        client_turn_id=str(uuid4()),
        content="Within timeout",
        input_type="text",
        ids=ids(),
        run_metadata=metadata(),
    )
    await repository.finalize(
        conversation_id=conversation.id,
        assistant_message_id=within_timeout.assistant_message.id,
        run_id=within_timeout.run_id,
        content="Second response",
        message_status="complete",
        run_status="complete",
    )
    assert first.user_message.session_id == within_timeout.user_message.session_id

    old = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
    async with database.connect() as connection:
        await connection.execute(
            "UPDATE sessions SET last_activity_at = ? WHERE id = ?",
            (old, first.user_message.session_id),
        )
        await connection.commit()
    after_timeout = await repository.begin_turn(
        conversation_id=conversation.id,
        client_turn_id=str(uuid4()),
        content="After timeout",
        input_type="text",
        ids=ids(),
        run_metadata=metadata(),
    )

    assert first.user_message.session_id != after_timeout.user_message.session_id
    messages = await repository.messages(conversation.id)
    assert [message.sequence_no for message in messages] == [1, 2, 3, 4, 5, 6]
    assert [
        message.content
        for message in await repository.context_messages(
            conversation.id, after_timeout.user_message.id
        )
    ] == ["Premier", "Réponse", "Within timeout", "Second response", "After timeout"]


@pytest.mark.asyncio
async def test_startup_reconciliation_atomically_repairs_nonterminal_rows(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.sqlite", REPOSITORY_ROOT / "migrations")
    await database.migrate()
    repository = ConversationRepository(database)
    conversation = await repository.create()
    turn = await repository.begin_turn(
        conversation_id=conversation.id,
        client_turn_id=str(uuid4()),
        content="Avant crash",
        input_type="text",
        ids=ids(),
        run_metadata=metadata(),
    )
    await repository.mark_generating(turn.run_id)
    await repository.checkpoint(turn.assistant_message.id, "partiel durable")

    assert await repository.reconcile_interrupted_process() == (1, 1)
    async with database.connect() as connection:
        row = await (
            await connection.execute(
                """
                SELECT m.status, m.content, r.status, r.error_code
                FROM messages m JOIN model_runs r ON r.id = m.model_run_id
                WHERE m.id = ?
                """,
                (turn.assistant_message.id,),
            )
        ).fetchone()
        zombies = await (
            await connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM model_runs WHERE status IN ('starting', 'generating')),
                  (SELECT COUNT(*) FROM messages WHERE status = 'streaming')
                """
            )
        ).fetchone()
    assert row == ("failed", "partiel durable", "failed", "PROCESS_INTERRUPTED")
    assert zombies == (0, 0)


@pytest.mark.asyncio
async def test_checkpoint_then_cancel_preserves_partial_text(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.sqlite", REPOSITORY_ROOT / "migrations")
    await database.migrate()
    repository = ConversationRepository(database)
    conversation = await repository.create()
    registry = RunRegistry()
    backend = DeltaBackend(["abc"], wait_after_chunks=True)
    service = ConversationChatService(
        settings_for(
            database.path,
            stream_checkpoint_characters=3,
            stream_checkpoint_seconds=60,
        ),
        backend,
        registry,
        repository,
        ContextBuilder(repository, AsyncMock()),
        REPOSITORY_ROOT,
    )

    async def connected() -> bool:
        return False

    stream = service.stream_turn(
        conversation_id=conversation.id,
        client_turn_id=str(uuid4()),
        content="Question",
        input_type="text",
        options=GenerationOptions(),
        is_disconnected=connected,
    )
    started = json.loads((await anext(stream)).split("data: ", 1)[1])
    assert json.loads((await anext(stream)).split("data: ", 1)[1]) == {"text": "abc"}
    persisted = await repository.messages(conversation.id)
    assert persisted[-1].content == "abc"
    await registry.cancel(started["run_id"])
    assert (await anext(stream)).startswith("event: cancelled")
    await stream.aclose()

    final_messages = await repository.messages(conversation.id)
    assert final_messages[-1].status.value == "interrupted"
    assert final_messages[-1].content == "abc"
    async with database.connect() as connection:
        run = await (
            await connection.execute(
                "SELECT status FROM model_runs WHERE id = ?", (started["run_id"],)
            )
        ).fetchone()
    assert run == ("cancelled",)
    assert backend.closed is True


@pytest.mark.asyncio
async def test_streaming_checkpoints_are_batched_not_written_per_token(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.sqlite", REPOSITORY_ROOT / "migrations")
    await database.migrate()
    repository = ConversationRepository(database)
    conversation = await repository.create()
    backend = DeltaBackend(["x"] * 10)
    registry = RunRegistry()
    service = ConversationChatService(
        settings_for(
            database.path,
            stream_checkpoint_characters=5,
            stream_checkpoint_seconds=60,
        ),
        backend,
        registry,
        repository,
        ContextBuilder(repository, AsyncMock()),
        REPOSITORY_ROOT,
    )
    original_checkpoint = repository.checkpoint
    repository.checkpoint = AsyncMock(wraps=original_checkpoint)  # type: ignore[method-assign]

    async def connected() -> bool:
        return False

    frames = [
        frame
        async for frame in service.stream_turn(
            conversation_id=conversation.id,
            client_turn_id=str(uuid4()),
            content="Question",
            input_type="text",
            options=GenerationOptions(),
            is_disconnected=connected,
        )
    ]

    assert frames[-1] == "event: done\ndata: {}\n\n"
    assert repository.checkpoint.await_count == 2  # type: ignore[attr-defined]
    messages = await repository.messages(conversation.id)
    assert messages[-1].content == "x" * 10
    assert messages[-1].status.value == "complete"


def test_conversation_context_never_crosses_conversation_boundary(tmp_path: Path) -> None:
    backend = FakeBackend()
    app = create_app(settings_for(tmp_path / "app.sqlite"), backend)
    with TestClient(app) as client:
        conversation_a = client.post("/v1/conversations", json={}).json()["id"]
        conversation_b = client.post("/v1/conversations", json={}).json()["id"]
        client.post(
            f"/v1/conversations/{conversation_a}/turns",
            json={"client_turn_id": str(uuid4()), "content": "Secret CARIBOU"},
        )
        client.post(
            f"/v1/conversations/{conversation_b}/turns",
            json={"client_turn_id": str(uuid4()), "content": "Quel était le mot ?"},
        )

    payload_b = "\n".join(message.content for message in backend.requests[-1])
    assert "CARIBOU" not in payload_b
    assert "Quel était le mot ?" in payload_b


def test_invalid_client_turn_id_has_stable_safe_error_code(tmp_path: Path) -> None:
    app = create_app(settings_for(tmp_path / "app.sqlite"), FakeBackend())
    with TestClient(app) as client:
        conversation_id = client.post("/v1/conversations", json={}).json()["id"]
        response = client.post(
            f"/v1/conversations/{conversation_id}/turns",
            json={"client_turn_id": "not-a-uuid", "content": "private payload"},
        )

    assert response.status_code == 422
    assert response.json() == {"error": {"code": "INVALID_CLIENT_TURN_ID", "retryable": False}}
    assert "private payload" not in response.text


def test_llm_crash_preserves_partial_failed_turn_and_next_turn_recovers(tmp_path: Path) -> None:
    path = tmp_path / "app.sqlite"
    backend = CrashOnceBackend()
    app = create_app(settings_for(path), backend)
    with TestClient(app) as client:
        conversation_id = client.post("/v1/conversations", json={}).json()["id"]
        failed = client.post(
            f"/v1/conversations/{conversation_id}/turns",
            json={"client_turn_id": str(uuid4()), "content": "First"},
        )
        recovered = client.post(
            f"/v1/conversations/{conversation_id}/turns",
            json={"client_turn_id": str(uuid4()), "content": "Second"},
        )
        messages = client.get(f"/v1/conversations/{conversation_id}/messages").json()["items"]

    assert "event: error" in failed.text
    assert "LLM_BACKEND_PROTOCOL_ERROR" in failed.text
    assert recovered.text.endswith("event: done\ndata: {}\n\n")
    assert [(item["status"], item["content"]) for item in messages[1::2]] == [
        ("failed", "partiel"),
        ("complete", "repris"),
    ]

    async def read_runs() -> list[tuple[str, str | None]]:
        database = Database(path, REPOSITORY_ROOT / "migrations")
        async with database.connect() as connection:
            return await (
                await connection.execute(
                    "SELECT status, error_code FROM model_runs ORDER BY started_at"
                )
            ).fetchall()

    assert asyncio.run(read_runs()) == [
        ("failed", "LLM_BACKEND_PROTOCOL_ERROR"),
        ("complete", None),
    ]


@pytest.mark.asyncio
async def test_late_initial_transaction_failure_rolls_back_every_row(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.sqlite", REPOSITORY_ROOT / "migrations")
    await database.migrate()
    repository = ConversationRepository(database)
    conversation = await repository.create()
    async with database.connect() as connection:
        await connection.execute(
            """
            CREATE TRIGGER fail_assistant_insert
            BEFORE INSERT ON messages WHEN NEW.role = 'assistant'
            BEGIN SELECT RAISE(ABORT, 'late failure'); END
            """
        )
        await connection.commit()

    with pytest.raises(aiosqlite.IntegrityError, match="late failure"):
        await repository.begin_turn(
            conversation_id=conversation.id,
            client_turn_id=str(uuid4()),
            content="Atomic",
            input_type="text",
            ids=ids(),
            run_metadata=metadata(),
        )

    async with database.connect() as connection:
        counts = await (
            await connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM messages),
                  (SELECT COUNT(*) FROM model_runs),
                  (SELECT COUNT(*) FROM sessions),
                  (SELECT next_sequence_no FROM conversations WHERE id = ?)
                """,
                (conversation.id,),
            )
        ).fetchone()
    assert counts == (0, 0, 0, 1)


@pytest.mark.asyncio
async def test_read_remains_available_during_uncommitted_checkpoint_write(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.sqlite", REPOSITORY_ROOT / "migrations")
    await database.migrate()
    repository = ConversationRepository(database)
    conversation = await repository.create()
    turn = await repository.begin_turn(
        conversation_id=conversation.id,
        client_turn_id=str(uuid4()),
        content="Reader",
        input_type="text",
        ids=ids(),
        run_metadata=metadata(),
    )

    async with database.connect() as writer:
        await writer.execute("BEGIN IMMEDIATE")
        await writer.execute(
            "UPDATE messages SET content = ? WHERE id = ?",
            ("checkpoint in progress", turn.assistant_message.id),
        )
        visible = await asyncio.wait_for(repository.messages(conversation.id), timeout=1)
        await writer.rollback()

    assert [message.sequence_no for message in visible] == [1, 2]
    assert visible[-1].content == ""


@pytest.mark.asyncio
async def test_unexpected_context_failure_is_durable_and_sanitized(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.sqlite", REPOSITORY_ROOT / "migrations")
    await database.migrate()
    repository = ConversationRepository(database)
    conversation = await repository.create()
    registry = RunRegistry()
    context_builder = AsyncMock(spec=ContextBuilder)
    context_builder.build_context.side_effect = RuntimeError("ULTRA_SECRET_CONTEXT")
    service = ConversationChatService(
        settings_for(database.path),
        FakeBackend(),
        registry,
        repository,
        context_builder,
        REPOSITORY_ROOT,
    )

    async def connected() -> bool:
        return False

    frames = [
        frame
        async for frame in service.stream_turn(
            conversation_id=conversation.id,
            client_turn_id=str(uuid4()),
            content="Question",
            input_type="text",
            options=GenerationOptions(),
            is_disconnected=connected,
        )
    ]

    assert frames[0].startswith("event: run_started")
    assert frames[-1] == (
        'event: error\ndata: {"code":"CONTEXT_BUILD_FAILED","retryable":false}\n\n'
    )
    assert "ULTRA_SECRET_CONTEXT" not in "".join(frames)
    messages = await repository.messages(conversation.id)
    assert messages[-1].status.value == "failed"
    async with database.connect() as connection:
        run = await (
            await connection.execute(
                "SELECT status, error_code FROM model_runs WHERE id = ?",
                (messages[-1].model_run_id,),
            )
        ).fetchone()
    assert run == ("failed", "CONTEXT_BUILD_FAILED")
    assert len(registry) == 0


@pytest.mark.asyncio
async def test_cancel_during_context_preparation_interrupts_turn(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.sqlite", REPOSITORY_ROOT / "migrations")
    await database.migrate()
    repository = ConversationRepository(database)
    conversation = await repository.create()
    registry = RunRegistry()
    context_started = asyncio.Event()
    context_builder = AsyncMock(spec=ContextBuilder)

    async def cancelled_context(**kwargs):  # type: ignore[no-untyped-def]
        context_started.set()
        cancel_event = kwargs["cancel_event"]
        await cancel_event.wait()
        raise asyncio.CancelledError

    context_builder.build_context.side_effect = cancelled_context
    service = ConversationChatService(
        settings_for(database.path),
        FakeBackend(),
        registry,
        repository,
        context_builder,
        REPOSITORY_ROOT,
    )

    async def connected() -> bool:
        return False

    stream = service.stream_turn(
        conversation_id=conversation.id,
        client_turn_id=str(uuid4()),
        content="Long context",
        input_type="text",
        options=GenerationOptions(),
        is_disconnected=connected,
    )
    started = json.loads((await anext(stream)).split("data: ", 1)[1])
    next_frame = asyncio.create_task(anext(stream))
    await context_started.wait()
    await registry.cancel(started["run_id"])

    assert await next_frame == (f'event: cancelled\ndata: {{"run_id":"{started["run_id"]}"}}\n\n')
    await stream.aclose()
    messages = await repository.messages(conversation.id)
    assert messages[-1].status.value == "interrupted"
    async with database.connect() as connection:
        run = await (
            await connection.execute(
                "SELECT status FROM model_runs WHERE id = ?", (started["run_id"],)
            )
        ).fetchone()
    assert run == ("cancelled",)
    assert len(registry) == 0


def test_twenty_persistent_turns_survive_app_restart_and_turn_21_uses_history(
    tmp_path: Path,
) -> None:
    path = tmp_path / "app.sqlite"
    settings = settings_for(path)
    first_backend = FakeBackend()
    with TestClient(create_app(settings, first_backend)) as client:
        conversation_id = client.post("/v1/conversations", json={}).json()["id"]
        for index in range(1, 21):
            response = client.post(
                f"/v1/conversations/{conversation_id}/turns",
                json={"client_turn_id": str(uuid4()), "content": f"Turn {index}"},
            )
            assert response.text.endswith("event: done\ndata: {}\n\n")

    restarted_backend = FakeBackend()
    with TestClient(create_app(settings, restarted_backend)) as client:
        before = client.get(f"/v1/conversations/{conversation_id}/messages").json()["items"]
        health = client.get("/v1/health").json()
        turn_21 = client.post(
            f"/v1/conversations/{conversation_id}/turns",
            json={"client_turn_id": str(uuid4()), "content": "Turn 21"},
        )
        after = client.get(f"/v1/conversations/{conversation_id}/messages").json()["items"]

    assert len(before) == 40
    assert [message["sequence_no"] for message in before] == list(range(1, 41))
    assert health["database"]["startup_reconciliation"] is True
    assert turn_21.text.endswith("event: done\ndata: {}\n\n")
    assert len(after) == 42
    assert [message["sequence_no"] for message in after] == list(range(1, 43))
    final_payload = restarted_backend.requests[-1]
    assert sum(message.role.value == "user" for message in final_payload) == 21
    assert sum(message.role.value == "assistant" for message in final_payload) == 20
    assert sum(message.content == "Turn 21" for message in final_payload) == 1

    async def zombie_counts() -> tuple[int, int]:
        database = Database(path, REPOSITORY_ROOT / "migrations")
        async with database.connect() as connection:
            return await (
                await connection.execute(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM model_runs WHERE status IN ('starting', 'generating')),
                      (SELECT COUNT(*) FROM messages WHERE status = 'streaming')
                    """
                )
            ).fetchone()

    assert asyncio.run(zombie_counts()) == (0, 0)


def test_startup_reconciliation_runs_before_ready_and_preserves_checkpoint(tmp_path: Path) -> None:
    path = tmp_path / "app.sqlite"

    async def seed_interrupted_turn() -> tuple[str, str]:
        database = Database(path, REPOSITORY_ROOT / "migrations")
        await database.migrate()
        repository = ConversationRepository(database)
        conversation = await repository.create()
        turn = await repository.begin_turn(
            conversation_id=conversation.id,
            client_turn_id=str(uuid4()),
            content="Before process kill",
            input_type="text",
            ids=ids(),
            run_metadata=metadata(),
        )
        await repository.mark_generating(turn.run_id)
        await repository.checkpoint(turn.assistant_message.id, "durable partial")
        return conversation.id, turn.run_id

    conversation_id, run_id = asyncio.run(seed_interrupted_turn())
    with TestClient(create_app(settings_for(path), FakeBackend())) as client:
        health = client.get("/v1/health").json()
        messages = client.get(f"/v1/conversations/{conversation_id}/messages").json()["items"]

    assert health["database"]["status"] == "ready"
    assert health["database"]["startup_reconciliation"] is True
    assert messages[-1]["status"] == "failed"
    assert messages[-1]["content"] == "durable partial"

    async def read_run() -> tuple[str, str | None]:
        database = Database(path, REPOSITORY_ROOT / "migrations")
        async with database.connect() as connection:
            return await (
                await connection.execute(
                    "SELECT status, error_code FROM model_runs WHERE id = ?", (run_id,)
                )
            ).fetchone()

    assert asyncio.run(read_run()) == ("failed", "PROCESS_INTERRUPTED")


def test_message_and_assistant_secrets_never_enter_logs(tmp_path: Path, caplog) -> None:
    secret = "ULTRA_SECRET_CARIBOU_7391"
    caplog.set_level(logging.INFO)
    app = create_app(settings_for(tmp_path / "app.sqlite"), DeltaBackend([secret]))
    with TestClient(app) as client:
        conversation_id = client.post("/v1/conversations", json={}).json()["id"]
        response = client.post(
            f"/v1/conversations/{conversation_id}/turns",
            json={"client_turn_id": str(uuid4()), "content": secret},
        )

    assert response.text.endswith("event: done\ndata: {}\n\n")
    assert secret not in caplog.text
