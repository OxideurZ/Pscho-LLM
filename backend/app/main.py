from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from backend.app.api import router
from backend.app.backup import BackupService
from backend.app.chat import ChatService
from backend.app.config import Settings, get_settings
from backend.app.config.settings import REPOSITORY_ROOT
from backend.app.context import ContextBuilder
from backend.app.conversations.repository import ConversationRepository
from backend.app.conversations.service import ConversationChatService
from backend.app.db import Database
from backend.app.llm.base import LLMBackend
from backend.app.llm.llama_cpp import LlamaCppBackend
from backend.app.runs import RunRegistry, RunRepository
from backend.app.runtime import RuntimeOffloadService
from backend.app.security import LocalSessionManager, SecretStore, WindowsDpapiSecretStore
from backend.app.stt import VoiceJobRegistry, WhisperCppBackend
from backend.app.summaries import SummaryService


def create_app(settings: Settings | None = None, llm_backend: LLMBackend | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_backend = llm_backend or LlamaCppBackend(resolved_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for directory in ("data", "backups", "models", "runtime", "logs"):
            (resolved_settings.data_directory / directory).mkdir(parents=True, exist_ok=True)
        secret_store: SecretStore | None = None
        database_key: bytes | None = None
        if resolved_settings.security_enabled:
            secret_store = WindowsDpapiSecretStore(resolved_settings.data_directory / "security")
            database_key = secret_store.create(resolved_settings.database_key_name)
        database = Database(
            resolved_settings.database_path,
            REPOSITORY_ROOT / "migrations",
            resolved_settings.sqlite_busy_timeout_ms,
            database_key,
        )
        await database.migrate()
        conversation_repository = ConversationRepository(
            database, resolved_settings.session_timeout_seconds
        )
        await conversation_repository.reconcile_interrupted_process()
        database.mark_reconciliation_completed()
        registry = RunRegistry()
        run_repository = RunRepository(
            resolved_settings.database_path,
            resolved_settings.sqlite_busy_timeout_ms,
            database_key,
        )
        summary_service = SummaryService(
            resolved_settings,
            resolved_backend,
            conversation_repository,
            run_repository,
            REPOSITORY_ROOT,
        )
        context_builder = ContextBuilder(conversation_repository, summary_service)
        app.state.settings = resolved_settings
        app.state.secret_store = secret_store
        app.state.local_session_manager = LocalSessionManager(
            resolved_settings.session_timeout_seconds
        )
        app.state.database = database
        app.state.llm_backend = resolved_backend
        app.state.stt_backend = WhisperCppBackend(
            resolved_settings.whisper_cpp_path, resolved_settings.whisper_model_path
        )
        app.state.voice_job_registry = VoiceJobRegistry(resolved_settings, app.state.stt_backend)
        app.state.run_registry = registry
        app.state.runtime_offload_service = RuntimeOffloadService(resolved_settings)
        app.state.backup_service = BackupService(
            resolved_settings.database_path,
            resolved_settings.data_directory / "backups",
            database_key,
        )
        app.state.conversation_repository = conversation_repository
        app.state.conversation_chat_service = ConversationChatService(
            resolved_settings,
            resolved_backend,
            registry,
            conversation_repository,
            context_builder,
            REPOSITORY_ROOT,
        )
        app.state.chat_service = ChatService(
            resolved_settings,
            resolved_backend,
            registry,
            run_repository,
            REPOSITORY_ROOT,
        )
        try:
            yield
        finally:
            await app.state.voice_job_registry.shutdown()

    application = FastAPI(
        title="Psych-local", version=resolved_settings.app_version, lifespan=lifespan
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @application.middleware("http")
    async def no_store_sensitive_responses(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/v1/auth", "/v1/conversations", "/v1/stt", "/v1/chat", "/v1/runs")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, error: RequestValidationError
    ) -> JSONResponse:
        invalid_client_turn_id = any(
            "client_turn_id" in validation_error.get("loc", ())
            for validation_error in error.errors()
        )
        code = "INVALID_CLIENT_TURN_ID" if invalid_client_turn_id else "REQUEST_VALIDATION_FAILED"
        return JSONResponse(
            {"error": {"code": code, "retryable": False}},
            status_code=422,
        )

    application.include_router(router)
    # The normal local runtime is a two-process application: llama-server plus
    # FastAPI.  Vite remains a development-only convenience.
    frontend_index = resolved_settings.frontend_dist / "index.html"
    if frontend_index.is_file():

        @application.get("/{frontend_path:path}", include_in_schema=False)
        async def frontend(frontend_path: str) -> FileResponse:
            candidate = (resolved_settings.frontend_dist / frontend_path).resolve()
            static_root = resolved_settings.frontend_dist.resolve()
            if candidate.is_file() and candidate.is_relative_to(static_root):
                return FileResponse(candidate)
            return FileResponse(frontend_index)

    return application


app = create_app()
