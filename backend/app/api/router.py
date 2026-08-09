from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from backend.app.api.schemas import ChatRequest
from backend.app.llm.models import GenerationOptions
from backend.app.runs.registry import RunAlreadyFinishedError, RunNotFoundError

router = APIRouter(prefix="/v1")


def error_response(code: str, retryable: bool, status_code: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "retryable": retryable}}, status_code=status_code)


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    database_ok = await request.app.state.database.health()
    llm = await request.app.state.llm_backend.health()
    healthy = database_ok and llm.status == "ok" and llm.model_loaded
    return {
        "status": "healthy" if healthy else "degraded",
        "backend": {"status": "ok"},
        "database": {"status": "ok" if database_ok else "unavailable"},
        "llm": llm.model_dump(mode="json"),
    }


@router.get("/models")
async def models(request: Request) -> dict[str, Any]:
    model = await request.app.state.llm_backend.model_info()
    return {"models": [model.model_dump(mode="json")]}


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
