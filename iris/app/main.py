"""Main FastAPI Application Entrypoint for IRIS."""

from contextlib import asynccontextmanager
import time
import uuid
from typing import AsyncGenerator
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from iris.app.core.config import settings
from iris.app.core.logging import get_logger, correlation_id_ctx
from iris.app.database.database import init_db
from iris.app.tools.registry import default_tool_registry
from iris.app.tools.builtin.calculator import CalculatorTool
from iris.app.tools.builtin.system_info import SystemInfoTool
from iris.app.tools.builtin.time import TimeTool
from iris.app.tools.builtin.string_utils import StringUtilsTool
from iris.app.tools.builtin.unit_converter import UnitConverterTool

# Import routers
from iris.app.api.routes import health, chat, tasks, tools, memory, llm, projects

logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for startup & shutdown initialization."""
    logger.info("Initializing IRIS Agent Kernel system...")

    # Initialize DB tables
    try:
        await init_db()
        logger.info("SQLite database tables initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize database tables: {e}")

    # Register builtin tools
    default_tool_registry.register(CalculatorTool())
    default_tool_registry.register(SystemInfoTool())
    default_tool_registry.register(TimeTool())
    default_tool_registry.register(StringUtilsTool())
    default_tool_registry.register(UnitConverterTool())
    logger.info(f"Built-in tools registered cleanly. Total registered: {len(default_tool_registry.list_tools())}")

    yield

    logger.info("Shutting down IRIS Agent Kernel...")


app = FastAPI(
    title=settings.APP_NAME,
    description="Offline-First Multimodal Personal AI Agent Foundation",
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Middleware to inject correlation ID into request context."""
    correlation_id = request.headers.get("X-Correlation-ID", f"cid_{uuid.uuid4().hex[:12]}")
    token = correlation_id_ctx.set(correlation_id)

    start_time = time.perf_counter()
    response = await call_next(request)
    process_time = time.perf_counter() - start_time

    response.headers["X-Correlation-ID"] = correlation_id
    response.headers["X-Process-Time"] = f"{process_time:.4f}s"
    correlation_id_ctx.reset(token)
    return response


from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Mount static files for UI assets
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/chat", summary="IRIS Developer Chat UI", tags=["UI"])
async def get_chat_ui():
    """Serve IRIS Developer Chat Interface."""
    index_path = static_dir / "index.html"
    if not index_path.exists():
        return {"error": "UI index.html not found"}
    return FileResponse(str(index_path))


# Register API routers
app.include_router(health.router)
app.include_router(chat.router)
app.include_router(tasks.router)
app.include_router(tools.router)
app.include_router(memory.router)
app.include_router(llm.router)
app.include_router(projects.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("iris.app.main:app", host="127.0.0.1", port=8000, reload=True)
