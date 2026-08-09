import asyncio
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.tests.fakes import FakeBackend


def settings_for(path: Path) -> Settings:
    return Settings(
        data_directory=path.parent / "runtime",
        database_path=path,
        model_expected_sha256="a" * 64,
        security_enabled=False,
        memory_background_idle_seconds=120,
    )


async def seed_memory(database, message_id: str) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC).isoformat()
    memory_id = "memory_api"
    entity_id = "entity_api"
    excerpt = "Je préfère les réponses détaillées."
    async with database.connect() as connection:
        await connection.execute(
            """
            INSERT INTO memory_items(
                id, kind, status, content, epistemic_status, observed_at,
                last_supported_at, created_at, updated_at
            ) VALUES (?, 'preference', 'active', ?, 'stated', ?, ?, ?, ?)
            """,
            (memory_id, "L'utilisateur préfère les réponses détaillées.", now, now, now, now),
        )
        await connection.execute(
            """
            INSERT INTO memory_sources(
                memory_id, message_id, start_char, end_char,
                text_sha256, source_role, created_at
            ) VALUES (?, ?, 0, ?, ?, 'origin', ?)
            """,
            (memory_id, message_id, len(excerpt), sha256(excerpt.encode()).hexdigest(), now),
        )
        await connection.execute(
            """
            INSERT INTO entities(
                id, display_name, entity_type, resolution_status, created_at, updated_at
            ) VALUES (?, 'Réponses', 'other', 'unresolved', ?, ?)
            """,
            (entity_id, now, now),
        )
        await connection.execute(
            "INSERT INTO memory_entities(memory_id, entity_id, role) VALUES (?, ?, 'related')",
            (memory_id, entity_id),
        )
        await connection.commit()
    return memory_id, entity_id


def test_memory_api_inspection_control_and_structured_delete(tmp_path: Path) -> None:
    app = create_app(settings_for(tmp_path / "app.sqlite"), FakeBackend())
    with TestClient(app) as client:
        conversation_id = client.post("/v1/conversations", json={}).json()["id"]
        turn = client.post(
            f"/v1/conversations/{conversation_id}/turns",
            json={
                "client_turn_id": str(uuid4()),
                "content": "Je préfère les réponses détaillées.",
                "input_type": "text",
            },
        )
        assert turn.status_code == 200
        messages = client.get(f"/v1/conversations/{conversation_id}/messages").json()["items"]
        user_message = next(message for message in messages if message["role"] == "user")
        memory_id, entity_id = asyncio.run(seed_memory(app.state.database, user_message["id"]))

        status = client.get("/v1/memory/status")
        listed = client.get("/v1/memory")
        detail = client.get(f"/v1/memory/{memory_id}")
        sources = client.get(f"/v1/memory/{memory_id}/sources")
        edited = client.patch(
            f"/v1/memory/{memory_id}",
            json={"content": "Préférence utilisateur corrigée."},
        )
        disabled = client.post(f"/v1/memory/{memory_id}/disable")
        enabled = client.post(f"/v1/memory/{memory_id}/enable")
        entity = client.patch(
            f"/v1/entities/{entity_id}", json={"display_name": "Préférences de réponse"}
        )
        deleted = client.delete(f"/v1/memory/{memory_id}")
        missing = client.get(f"/v1/memory/{memory_id}")
        raw_messages = client.get(f"/v1/conversations/{conversation_id}/messages")

    assert status.json()["memory_enabled"] is True
    assert listed.json()["items"][0]["id"] == memory_id
    assert detail.json()["sources"][0]["excerpt"] == "Je préfère les réponses détaillées."
    assert detail.json()["sources"][0]["hash_valid"] is True
    assert sources.headers["cache-control"] == "no-store"
    assert edited.json()["user_locked"] == 1
    assert disabled.json()["status"] == "disabled"
    assert enabled.json()["status"] == "active"
    assert entity.json()["display_name"] == "Préférences de réponse"
    assert deleted.status_code == 204
    assert missing.status_code == 404
    assert any(
        message["content"] == "Je préfère les réponses détaillées."
        for message in raw_messages.json()["items"]
    )
