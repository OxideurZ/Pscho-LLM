from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api import router
from backend.app.chat import ChatService
from backend.app.config import Settings, get_settings
from backend.app.config.settings import REPOSITORY_ROOT
from backend.app.db import Database
from backend.app.llm.base import LLMBackend
from backend.app.llm.llama_cpp import LlamaCppBackend
from backend.app.runs import RunRegistry, RunRepository


def create_app(settings: Settings | None = None, llm_backend: LLMBackend | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_backend = llm_backend or LlamaCppBackend(resolved_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = Database(resolved_settings.database_path, REPOSITORY_ROOT / "migrations")
        await database.migrate()
        registry = RunRegistry()
        repository = RunRepository(resolved_settings.database_path)
        app.state.settings = resolved_settings
        app.state.database = database
        app.state.llm_backend = resolved_backend
        app.state.run_registry = registry
        app.state.chat_service = ChatService(
            resolved_settings, resolved_backend, registry, repository, REPOSITORY_ROOT
        )
        yield

    application = FastAPI(
        title="Psych-local", version=resolved_settings.app_version, lifespan=lifespan
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    application.include_router(router)
    return application


app = create_app()
