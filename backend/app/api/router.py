from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from backend.app.api.schemas import (
    BootstrapRequest,
    ChatRequest,
    ConversationCreateRequest,
    ConversationUpdateRequest,
    EntityUpdateRequest,
    MemoryBackfillRequest,
    MemoryUpdateRequest,
    TurnRequest,
    VoiceJobCreateRequest,
)
from backend.app.config.metadata import runtime_info
from backend.app.conversations import (
    ConversationBusyError,
    ConversationDeletedError,
    ConversationNotFoundError,
)
from backend.app.llm.models import GenerationOptions
from backend.app.memory import (
    EntityNotFoundError,
    MemoryNotFoundError,
    MemoryStateConflictError,
)
from backend.app.runs.registry import RunAlreadyFinishedError, RunNotFoundError
from backend.app.runtime import RuntimeNotManagedError
from backend.app.security import origin_is_local, require_local_session
from backend.app.stt import VoiceJobConflictError, VoiceJobNotFoundError
from backend.app.stt.models import VoiceJob

router = APIRouter(prefix="/v1")
protected = [Depends(require_local_session)]


async def note_interactive_activity(request: Request) -> None:
    await request.app.state.background_jobs.note_interactive_activity()
    retrieval_jobs = getattr(request.app.state, "retrieval_jobs", None)
    if retrieval_jobs is not None:
        await retrieval_jobs.note_interactive_activity()


def error_response(code: str, retryable: bool, status_code: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "retryable": retryable}}, status_code=status_code)


@router.post("/auth/bootstrap", dependencies=[])
async def bootstrap_session(payload: BootstrapRequest, request: Request) -> JSONResponse:
    if request.app.state.settings.security_enabled and not origin_is_local(request):
        return error_response("LOCAL_ORIGIN_REQUIRED", False, 403)
    response = JSONResponse({"status": "authenticated"})
    if not request.app.state.local_session_manager.issue(response, payload.bootstrap_token):
        return error_response("LOCAL_BOOTSTRAP_REQUIRED", False, 403)
    return response


@router.get("/auth/session", dependencies=protected)
async def authenticated_session() -> dict[str, str]:
    """Cheap authenticated heartbeat for the local browser UI."""
    return {"status": "authenticated"}


def conversation_error(error: Exception) -> JSONResponse:
    if isinstance(error, ConversationNotFoundError):
        return error_response("CONVERSATION_NOT_FOUND", False, 404)
    if isinstance(error, ConversationDeletedError):
        return error_response("CONVERSATION_DELETED", False, 410)
    if isinstance(error, ConversationBusyError):
        return error_response("CONVERSATION_BUSY", True, 409)
    raise error


def voice_job_payload(job: VoiceJob) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "voice_input_id": str(job.voice_input_id),
        "conversation_id": job.conversation_id,
        "client_turn_id": str(job.client_turn_id),
        "status": job.status.value,
        "duration_ms": job.duration_ms,
        "error_code": job.error_code,
    }
    if job.status.value == "transcript_ready":
        payload["transcript"] = job.transcript
    return payload


def voice_error(error: Exception) -> JSONResponse:
    if isinstance(error, VoiceJobNotFoundError):
        return error_response("VOICE_JOB_NOT_FOUND", False, 404)
    if isinstance(error, VoiceJobConflictError):
        return error_response("VOICE_JOB_CONFLICT", True, 409)
    if isinstance(error, ValueError):
        return error_response(str(error), False, 422)
    raise error


def memory_error(error: Exception) -> JSONResponse:
    if isinstance(error, MemoryNotFoundError):
        return error_response("MEMORY_NOT_FOUND", False, 404)
    if isinstance(error, MemoryStateConflictError):
        return error_response("MEMORY_STATE_CONFLICT", False, 409)
    if isinstance(error, EntityNotFoundError):
        return error_response("ENTITY_NOT_FOUND", False, 404)
    raise error


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    database = await request.app.state.database.health()
    llm = await request.app.state.llm_backend.health()
    database_ready = (
        database.reachable
        and database.schema_current
        and database.foreign_keys
        and database.journal_mode == "wal"
        and database.migrations == "current"
        and database.startup_reconciliation
    )
    healthy = database_ready and llm.status == "ok" and llm.model_loaded
    return {
        "status": "healthy" if healthy else "degraded",
        "backend": {"status": "ok"},
        "database": {
            "status": "ready" if database_ready else "not_ready",
            **database.as_dict(),
        },
        "llm": llm.model_dump(mode="json"),
    }


@router.get("/live")
async def liveness() -> dict[str, str]:
    """Cheap process liveness probe with no model or database dependency."""
    return {"status": "ok"}


@router.get("/models")
async def models(request: Request) -> dict[str, Any]:
    model = await request.app.state.llm_backend.model_info()
    return {"models": [model.model_dump(mode="json")]}


@router.get("/runtime-info")
async def runtime_information(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    return {
        "runtime": runtime_info(settings),
        "model": {
            "name": settings.model_name,
            "sha256": settings.model_expected_sha256,
            "context_size": settings.context_size,
            "llama_cpp_version": settings.llama_cpp_version,
        },
        "data_directory": str(settings.data_directory),
        "frontend_serving_mode": "fastapi-static",
    }


@router.get("/security/readiness")
async def security_readiness_endpoint(request: Request) -> dict[str, Any]:
    from backend.app.security import security_readiness

    return security_readiness(
        request.app.state.settings,
        secret_store=request.app.state.secret_store,
        database_key=request.app.state.database.encryption_key,
    )


@router.post("/runtime/offload", status_code=202, dependencies=protected)
async def offload_runtime(request: Request) -> JSONResponse:
    try:
        request.app.state.runtime_offload_service.schedule()
    except RuntimeNotManagedError:
        return error_response("RUNTIME_NOT_MANAGED", False, 409)
    return JSONResponse(
        {"status": "shutting_down", "models": "offloading", "restart": "start.ps1"},
        status_code=202,
    )


@router.get("/stt/health")
async def stt_health(request: Request) -> dict[str, Any]:
    model = await request.app.state.stt_backend.health()
    return {"status": "healthy" if model.available else "unavailable", **model.__dict__}


@router.get("/stt/model")
async def stt_model(request: Request) -> dict[str, Any]:
    model = await request.app.state.stt_backend.health()
    return model.__dict__


@router.post("/stt/jobs", status_code=201, dependencies=protected)
async def create_voice_job(payload: VoiceJobCreateRequest, request: Request) -> JSONResponse:
    await note_interactive_activity(request)
    try:
        await request.app.state.conversation_repository.get(payload.conversation_id)
        job = await request.app.state.voice_job_registry.create(
            payload.voice_input_id, payload.conversation_id, payload.client_turn_id
        )
    except (VoiceJobConflictError, ValueError) as error:
        return voice_error(error)
    except (ConversationNotFoundError, ConversationDeletedError) as error:
        return conversation_error(error)
    return JSONResponse(voice_job_payload(job), status_code=201)


@router.post("/stt/jobs/{voice_input_id}/chunks", status_code=204, dependencies=protected)
async def append_voice_chunk(voice_input_id: str, request: Request) -> JSONResponse:
    try:
        from uuid import UUID

        content_length = int(request.headers.get("content-length", "0"))
        if content_length > request.app.state.settings.max_voice_audio_bytes:
            return error_response("VOICE_AUDIO_TOO_LARGE", False, 413)
        job = await request.app.state.voice_job_registry.append_chunk(
            UUID(voice_input_id), await request.body()
        )
    except (VoiceJobNotFoundError, VoiceJobConflictError, ValueError) as error:
        return voice_error(error)
    return JSONResponse(
        content=None, status_code=204, headers={"X-Voice-Bytes": str(job.audio_bytes)}
    )


@router.post("/stt/jobs/{voice_input_id}/finalize", status_code=202, dependencies=protected)
async def finalize_voice_job(voice_input_id: str, request: Request) -> JSONResponse:
    try:
        from uuid import UUID

        job = await request.app.state.voice_job_registry.finalize(UUID(voice_input_id))
    except (VoiceJobNotFoundError, VoiceJobConflictError, ValueError) as error:
        return voice_error(error)
    return JSONResponse(voice_job_payload(job), status_code=202)


@router.get("/stt/jobs/{voice_input_id}", dependencies=protected)
async def get_voice_job(voice_input_id: str, request: Request) -> JSONResponse:
    try:
        from uuid import UUID

        job = await request.app.state.voice_job_registry.get(UUID(voice_input_id))
    except (VoiceJobNotFoundError, ValueError) as error:
        return voice_error(error)
    return JSONResponse(voice_job_payload(job))


@router.post("/stt/jobs/{voice_input_id}/cancel", status_code=202, dependencies=protected)
async def cancel_voice_job(voice_input_id: str, request: Request) -> JSONResponse:
    try:
        from uuid import UUID

        job = await request.app.state.voice_job_registry.cancel(UUID(voice_input_id))
    except (VoiceJobNotFoundError, VoiceJobConflictError, ValueError) as error:
        return voice_error(error)
    return JSONResponse(voice_job_payload(job), status_code=202)


@router.post("/conversations", status_code=201, dependencies=protected)
async def create_conversation(
    payload: ConversationCreateRequest, request: Request
) -> dict[str, Any]:
    conversation = await request.app.state.conversation_repository.create(payload.title)
    return conversation.model_dump(mode="json")


@router.get("/memory/status", dependencies=protected)
async def memory_status(request: Request) -> dict[str, Any]:
    async with request.app.state.database.connect() as connection:
        counts = await (
            await connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM jobs WHERE kind = 'memory_extract' AND status = 'pending'),
                  (SELECT COUNT(*) FROM jobs WHERE kind = 'memory_extract' AND status = 'retry'),
                  (SELECT COUNT(*) FROM jobs WHERE kind = 'memory_extract' AND status = 'failed'),
                  (SELECT COUNT(*) FROM memory_items WHERE status = 'active'),
                  (SELECT COUNT(*) FROM memory_items WHERE status = 'disabled')
                """
            )
        ).fetchone()
    return {
        "memory_enabled": request.app.state.settings.memory_enabled,
        "background_idle_seconds": request.app.state.settings.memory_background_idle_seconds,
        "active_job_id": request.app.state.background_jobs.active_job_id,
        "jobs": {"pending": counts[0], "retry": counts[1], "failed": counts[2]},
        "memories": {"active": counts[3], "disabled": counts[4]},
    }


@router.post("/memory/backfill", dependencies=protected)
async def memory_backfill(payload: MemoryBackfillRequest, request: Request) -> JSONResponse:
    repository = request.app.state.memory_repository
    preview = await repository.preview_backfill(
        conversation_ids=payload.conversation_ids,
        created_after=payload.created_after.isoformat() if payload.created_after else None,
        created_before=payload.created_before.isoformat() if payload.created_before else None,
        all_eligible=payload.all_eligible,
        extractor_version=request.app.state.settings.memory_extraction_prompt_version,
    )
    if not payload.confirm:
        return JSONResponse({"preview": preview, "confirmation_required": True})
    backfill = await repository.start_backfill(
        preview=preview,
        batch_size=request.app.state.settings.memory_backfill_enqueue_batch_size,
        max_attempts=request.app.state.settings.memory_job_max_attempts,
    )
    return JSONResponse({"backfill": backfill, "confirmation_required": False}, status_code=202)


@router.get("/memory/backfill/{backfill_id}", dependencies=protected)
async def memory_backfill_progress(backfill_id: str, request: Request) -> JSONResponse:
    try:
        progress = await request.app.state.memory_repository.backfill_progress(backfill_id)
    except MemoryNotFoundError as error:
        return memory_error(error)
    return JSONResponse(progress)


@router.get("/memory/audit", dependencies=protected)
async def memory_audit(request: Request) -> JSONResponse:
    if not request.app.state.settings.memory_debug_tools_enabled:
        return error_response("MEMORY_AUDIT_DISABLED", False, 404)
    return JSONResponse(await request.app.state.memory_repository.audit_metrics())


@router.get("/memory", response_model=None, dependencies=protected)
async def list_memory(
    request: Request,
    status: str | None = None,
    kind: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any] | JSONResponse:
    allowed_statuses = {"active", "disabled", "superseded"}
    allowed_kinds = {"personal_fact", "event", "goal", "preference", "belief"}
    if status is not None and status not in allowed_statuses:
        return error_response("INVALID_MEMORY_STATUS", False, 422)
    if kind is not None and kind not in allowed_kinds:
        return error_response("INVALID_MEMORY_KIND", False, 422)
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    items = await request.app.state.memory_repository.list_memories(
        status=status, kind=kind, limit=limit, offset=offset
    )
    return {"items": items, "pagination": {"limit": limit, "offset": offset}}


@router.get("/memory/{memory_id}", dependencies=protected)
async def get_memory(memory_id: str, request: Request) -> JSONResponse:
    try:
        detail = await request.app.state.memory_repository.memory_detail(memory_id)
    except (MemoryNotFoundError, MemoryStateConflictError) as error:
        return memory_error(error)
    return JSONResponse(detail)


@router.get("/memory/{memory_id}/sources", dependencies=protected)
async def get_memory_sources(memory_id: str, request: Request) -> JSONResponse:
    try:
        detail = await request.app.state.memory_repository.memory_detail(memory_id)
    except MemoryNotFoundError as error:
        return memory_error(error)
    return JSONResponse({"items": detail["sources"]})


@router.patch("/memory/{memory_id}", dependencies=protected)
async def update_memory(
    memory_id: str, payload: MemoryUpdateRequest, request: Request
) -> JSONResponse:
    try:
        detail = await request.app.state.memory_repository.edit_memory(
            memory_id,
            content=payload.content,
            kind=payload.kind,
            epistemic_status=payload.epistemic_status,
        )
    except (MemoryNotFoundError, MemoryStateConflictError) as error:
        return memory_error(error)
    return JSONResponse(detail)


@router.post("/memory/{memory_id}/disable", dependencies=protected)
async def disable_memory(memory_id: str, request: Request) -> JSONResponse:
    try:
        detail = await request.app.state.memory_repository.set_memory_enabled(memory_id, False)
    except (MemoryNotFoundError, MemoryStateConflictError) as error:
        return memory_error(error)
    return JSONResponse(detail)


@router.post("/memory/{memory_id}/enable", dependencies=protected)
async def enable_memory(memory_id: str, request: Request) -> JSONResponse:
    try:
        detail = await request.app.state.memory_repository.set_memory_enabled(memory_id, True)
    except (MemoryNotFoundError, MemoryStateConflictError) as error:
        return memory_error(error)
    return JSONResponse(detail)


@router.delete("/memory/{memory_id}", status_code=204, dependencies=protected)
async def delete_memory(memory_id: str, request: Request) -> JSONResponse:
    try:
        await request.app.state.memory_repository.delete_memory(memory_id)
    except MemoryNotFoundError as error:
        return memory_error(error)
    return JSONResponse(content=None, status_code=204)


@router.get("/entities", dependencies=protected)
async def list_entities(request: Request) -> dict[str, Any]:
    return {"items": await request.app.state.memory_repository.list_entities()}


@router.get("/entities/{entity_id}", dependencies=protected)
async def get_entity(entity_id: str, request: Request) -> JSONResponse:
    try:
        detail = await request.app.state.memory_repository.entity_detail(entity_id)
    except EntityNotFoundError as error:
        return memory_error(error)
    return JSONResponse(detail)


@router.patch("/entities/{entity_id}", dependencies=protected)
async def update_entity(
    entity_id: str, payload: EntityUpdateRequest, request: Request
) -> JSONResponse:
    try:
        detail = await request.app.state.memory_repository.rename_entity(
            entity_id, payload.display_name
        )
    except EntityNotFoundError as error:
        return memory_error(error)
    return JSONResponse(detail)


@router.get("/conversations", dependencies=protected)
async def list_conversations(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    include_archived: bool = False,
) -> dict[str, Any]:
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    conversations = await request.app.state.conversation_repository.list(
        limit=limit, offset=offset, include_archived=include_archived
    )
    return {
        "items": [conversation.model_dump(mode="json") for conversation in conversations],
        "pagination": {"limit": limit, "offset": offset},
    }


@router.get("/conversations/{conversation_id}", dependencies=protected)
async def get_conversation(conversation_id: str, request: Request) -> JSONResponse:
    try:
        conversation = await request.app.state.conversation_repository.get(conversation_id)
    except (ConversationNotFoundError, ConversationDeletedError) as error:
        return conversation_error(error)
    return JSONResponse(conversation.model_dump(mode="json"))


@router.patch("/conversations/{conversation_id}", dependencies=protected)
async def update_conversation(
    conversation_id: str, payload: ConversationUpdateRequest, request: Request
) -> JSONResponse:
    updates: dict[str, Any] = {"archived": payload.archived}
    if "title" in payload.model_fields_set:
        updates["title"] = payload.title
    try:
        conversation = await request.app.state.conversation_repository.update(
            conversation_id, **updates
        )
    except (ConversationNotFoundError, ConversationDeletedError) as error:
        return conversation_error(error)
    return JSONResponse(conversation.model_dump(mode="json"))


@router.delete("/conversations/{conversation_id}", status_code=204, dependencies=protected)
async def delete_conversation(conversation_id: str, request: Request) -> JSONResponse:
    try:
        await request.app.state.conversation_repository.soft_delete(conversation_id)
    except (ConversationNotFoundError, ConversationDeletedError) as error:
        return conversation_error(error)
    return JSONResponse(content=None, status_code=204)


@router.get("/conversations/{conversation_id}/messages", dependencies=protected)
async def conversation_messages(
    conversation_id: str,
    request: Request,
    after_sequence_no: int = 0,
    limit: int = 100,
) -> JSONResponse:
    limit = min(max(limit, 1), 500)
    try:
        messages = await request.app.state.conversation_repository.messages(
            conversation_id,
            after_sequence_no=max(after_sequence_no, 0),
            limit=limit,
        )
    except (ConversationNotFoundError, ConversationDeletedError) as error:
        return conversation_error(error)
    return JSONResponse(
        {
            "items": [message.model_dump(mode="json") for message in messages],
            "pagination": {
                "after_sequence_no": max(after_sequence_no, 0),
                "limit": limit,
            },
        }
    )


@router.post("/conversations/{conversation_id}/turns", response_model=None, dependencies=protected)
async def conversation_turn(
    conversation_id: str, payload: TurnRequest, request: Request
) -> StreamingResponse | JSONResponse:
    await note_interactive_activity(request)
    settings = request.app.state.settings
    defaults = {
        "temperature": settings.default_temperature,
        "top_p": settings.default_top_p,
        "max_tokens": settings.default_max_tokens,
        "seed": settings.default_seed,
    }
    defaults.update(payload.generation.model_dump(exclude_unset=True))
    options = GenerationOptions.model_validate(defaults)

    iterator = request.app.state.conversation_chat_service.stream_turn(
        conversation_id=conversation_id,
        client_turn_id=str(payload.client_turn_id),
        content=payload.content,
        input_type=payload.input_type,
        options=options,
        is_disconnected=request.is_disconnected,
    )
    try:
        first_frame = await anext(iterator)
    except (ConversationNotFoundError, ConversationDeletedError, ConversationBusyError) as error:
        return conversation_error(error)

    async def stream():
        yield first_frame
        async for frame in iterator:
            yield frame

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat", dependencies=protected)
async def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    await note_interactive_activity(request)
    settings = request.app.state.settings
    defaults = {
        "temperature": settings.default_temperature,
        "top_p": settings.default_top_p,
        "max_tokens": settings.default_max_tokens,
        "seed": settings.default_seed,
    }
    defaults.update(payload.generation.model_dump(exclude_unset=True))
    options = GenerationOptions.model_validate(defaults)
    stream = request.app.state.chat_service.stream(
        payload.messages, options, request.is_disconnected
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runs/{run_id}/cancel", dependencies=protected)
async def cancel(run_id: str, request: Request) -> JSONResponse:
    try:
        run = await request.app.state.run_registry.cancel(run_id)
    except RunNotFoundError:
        return error_response("RUN_NOT_FOUND", False, 404)
    except RunAlreadyFinishedError:
        return error_response("RUN_ALREADY_FINISHED", False, 409)
    return JSONResponse({"run_id": run.id, "status": "cancelling"}, status_code=202)


@router.get("/runs/{run_id}/retrieval", dependencies=protected)
async def retrieval_trace(run_id: str, request: Request) -> JSONResponse:
    runtime = getattr(request.app.state, "retrieval_runtime", None)
    if runtime is None:
        return error_response("RETRIEVAL_UNAVAILABLE", False, 503)
    trace = await runtime.audit.for_model_run(run_id)
    if trace is None:
        return error_response("RETRIEVAL_TRACE_NOT_FOUND", False, 404)
    return JSONResponse(trace)
