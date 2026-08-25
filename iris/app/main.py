"""Main FastAPI application entrypoint for IRIS."""

from __future__ import annotations

import time
import uuid
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from iris.app.core import paths
from iris.app.core.auth import (
    auth_required,
    default_rate_limiter,
    ensure_token,
    extract_token,
    is_loopback,
    verify_token,
)
from iris.app.core.config import settings
from iris.app.core.logging import configure_from_settings, correlation_id_ctx, get_logger
from iris.app.database.database import init_db
from iris.app.services.scheduler import default_scheduler_service
from iris.app.services.hotkeys import default_hotkey_service
from iris.app.services.telegram import default_telegram_bridge
from iris.app.tools.loader import load_all_tools
from iris.app.tools.registry import default_tool_registry

# Import routers
from iris.app.api.routes import (
    chat,
    events,
    health,
    llm,
    memory,
    projects,
    system,
    tasks,
    tools,
    voice,
)

logger = get_logger("main")

static_dir = Path(__file__).parent / "static"


def _log_llm_status() -> None:
    """Report which reasoning backend is live, so a missing key is obvious.

    Silent fallback to the offline engine is the single most confusing startup
    state: commands keep working, so nothing looks broken, but conversation
    quality quietly drops. One line at boot removes the guesswork.
    """
    from iris.app.core.config import loaded_env_files

    env_files = loaded_env_files()
    providers = settings.configured_providers()

    if settings.LLM_MODE in ("off", "mock"):
        logger.info(
            "LLM: %s — deterministic commands and offline replies only (LLM_MODE=%s).",
            settings.LLM_MODE, settings.LLM_MODE,
        )
    elif providers:
        logger.info(
            "LLM: %s provider(s) ready — %s (routing in that order).",
            len(providers), ", ".join(providers),
        )
    else:
        where = ", ".join(env_files) if env_files else f"{paths.project_root() / '.env'} (not found)"
        logger.warning(
            "LLM: no API key detected — falling back to the offline engine. "
            "Commands still work; add OPENROUTER_API_KEY or GROQ_API_KEY to %s and restart.",
            where,
        )

    if env_files:
        logger.info("Config loaded from: %s", ", ".join(env_files))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown initialization."""
    configure_from_settings()
    logger.info("Initializing IRIS...")

    resolved = paths.ensure_dirs()
    logger.info("Data directory: %s", resolved["data"])

    try:
        await init_db()
        logger.info("Database initialized.")
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to initialize database: %s", exc)

    count = load_all_tools(default_tool_registry, quiet=True)
    stats = default_tool_registry.stats()
    logger.info(
        "Tools ready: %s registered, %s available on this platform.",
        stats["total"], stats["available"],
    )

    _log_llm_status()

    if settings.SCHEDULER_ENABLED:
        await default_scheduler_service.start()

    if settings.TELEGRAM_ENABLED:
        await default_telegram_bridge.start()

    if settings.HOTKEYS_ENABLED:
        default_hotkey_service.start()

    if auth_required():
        ensure_token()
        logger.info("Remote access enabled — bearer token enforced for non-local clients.")

    logger.info("IRIS is ready at %s", settings.base_url)

    if settings.OPEN_BROWSER_ON_START and settings.DESKTOP_MODE == "browser":
        try:
            webbrowser.open(settings.base_url)
        except Exception:  # noqa: BLE001 - headless hosts
            pass

    yield

    logger.info("Shutting down IRIS...")
    await default_scheduler_service.stop()
    await default_telegram_bridge.stop()
    default_hotkey_service.stop()
    try:
        from iris.app.llm.gateway import default_model_gateway

        await default_model_gateway.close()
    except Exception:  # noqa: BLE001
        pass


app = FastAPI(
    title=settings.APP_NAME,
    description="IRIS — your personal desktop AI assistant.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

#: Paths reachable without a token even when auth is on: the health probe and
#: the UI shell (HTML/CSS/JS contain no secrets — every API call the shell
#: makes is still token-checked individually).
_PUBLIC_PATHS = {"/health", "/", "/chat", "/favicon.ico"}


def _is_public_path(path: str) -> bool:
    return path in _PUBLIC_PATHS or path.startswith("/static/")


@app.middleware("http")
async def auth_and_context_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Correlation IDs, bearer-token auth for remote clients, rate limiting."""
    correlation_id = request.headers.get("X-Correlation-ID", f"cid_{uuid.uuid4().hex[:12]}")
    token_ctx = correlation_id_ctx.set(correlation_id)

    try:
        client_host = request.client.host if request.client else None

        if auth_required() and not is_loopback(client_host) and not _is_public_path(request.url.path):
            presented = extract_token(
                request.headers.get("Authorization"),
                request.headers.get("X-Iris-Token"),
                request.query_params.get("token"),
            )
            if not verify_token(presented):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Unauthorized. Provide the IRIS API token."},
                )
            if not default_rate_limiter.check(client_host or "unknown"):
                return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded."})

        start_time = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Process-Time"] = f"{time.perf_counter() - start_time:.4f}s"
        return response
    finally:
        correlation_id_ctx.reset(token_ctx)


# Static assets + UI
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Serve the IRIS web interface."""
    return FileResponse(str(static_dir / "index.html"))


@app.get("/chat", include_in_schema=False)
async def chat_ui() -> FileResponse:
    """Legacy UI path."""
    return FileResponse(str(static_dir / "index.html"))


# API routers
app.include_router(health.router)
app.include_router(chat.router)
app.include_router(events.router)
app.include_router(voice.router)
app.include_router(system.router)
app.include_router(tasks.router)
app.include_router(tools.router)
app.include_router(memory.router)
app.include_router(llm.router)
app.include_router(projects.router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("iris.app.main:app", host=settings.bind_host, port=settings.PORT, reload=True)
