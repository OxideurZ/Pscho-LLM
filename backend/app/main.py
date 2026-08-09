from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import get_settings
from backend.app.db import Database


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    database = Database(settings.database_path, settings.database_path.parent / "migrations")
    await database.migrate()
    app.state.database = database
    yield


app = FastAPI(title="Psych-local", version="0.1.0-dev", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/v1/health")
async def health() -> dict[str, object]:
    database_ok = await app.state.database.health()
    return {
        "status": "degraded",
        "backend": {"status": "ok"},
        "database": {"status": "ok" if database_ok else "unavailable"},
        "llm": {
            "status": "unavailable",
            "backend": "llama.cpp",
            "model_loaded": False,
        },
    }
