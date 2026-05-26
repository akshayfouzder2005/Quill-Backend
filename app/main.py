from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.auth import router as auth_router
from app.api.documents import router as docs_router
from app.api.ws import router as ws_router
from app.api.rag import router as rag_router
from app.workers.indexer import run_indexer
import asyncio


def create_app() -> FastAPI:
    app = FastAPI(
        title="Quill API",
        description="Real-time collaborative editor backend",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(docs_router, prefix="/api/v1")
    app.include_router(ws_router, prefix="/api/v1")
    app.include_router(rag_router, prefix="/api/v1")

    @app.on_event("startup")
    async def start_indexer():
        asyncio.create_task(run_indexer())

    @app.get("/health")
    async def health():
        return {"status": "ok", "app": "Quill"}

    return app


app = create_app()