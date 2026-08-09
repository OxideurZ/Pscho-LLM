from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from backend.app.api.schemas import (
    ChatRequest,
    ConversationCreateRequest,
    ConversationUpdateRequest,
    TurnRequest,
)
from backend.app.config.metadata import runtime_info
from backend.app.conversations import (
    ConversationBusyError,
    ConversationDeletedError,
    ConversationNotFoundError,
)
from backend.app.llm.models import GenerationOptions
from backend.app.runs.registry import RunAlreadyFinishedError, RunNotFoundError

router = APIRouter(prefix="/v1")


def error_response(code: str, retryable: bool, status_code: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "retryable": retryable}}, status_code=status_code)


def conversation_error(error: Exception) -> JSONResponse:
    if isinstance(error, ConversationNotFoundError):
        return error_response("CONVERSATION_NOT_FOUND", False, 404)
    if isinstance(error, ConversationDeletedError):
        return error_response("CONVERSATION_DELETED", False, 410)
    if isinstance(error, ConversationBusyError):
        return error_response("CONVERSATION_BUSY", True, 409)
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


@router.post("/conversations", status_code=201)
async def create_conversation(
    payload: ConversationCreateRequest, request: Request
) -> dict[str, Any]:
    conversation = await request.app.state.conversation_repository.create(payload.title)
    return conversation.model_dump(mode="json")


@router.get("/conversations")
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


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, request: Request) -> JSONResponse:
    try:
        conversation = await request.app.state.conversation_repository.get(conversation_id)
    except (ConversationNotFoundError, ConversationDeletedError) as error:
        return conversation_error(error)
    return JSONResponse(conversation.model_dump(mode="json"))


@router.patch("/conversations/{conversation_id}")
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


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str, request: Request) -> JSONResponse:
    try:
        await request.app.state.conversation_repository.soft_delete(conversation_id)
    except (ConversationNotFoundError, ConversationDeletedError) as error:
        return conversation_error(error)
    return JSONResponse(content=None, status_code=204)


@router.get("/conversations/{conversation_id}/messages")
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


@router.post("/conversations/{conversation_id}/turns", response_model=None)
async def conversation_turn(
    conversation_id: str, payload: TurnRequest, request: Request
) -> StreamingResponse | JSONResponse:
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


@router.post("/chat")
async def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
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


@router.post("/runs/{run_id}/cancel")
async def cancel(run_id: str, request: Request) -> JSONResponse:
    try:
        run = await request.app.state.run_registry.cancel(run_id)
    except RunNotFoundError:
        return error_response("RUN_NOT_FOUND", False, 404)
    except RunAlreadyFinishedError:
        return error_response("RUN_ALREADY_FINISHED", False, 409)
    return JSONResponse({"run_id": run.id, "status": "cancelling"}, status_code=202)
