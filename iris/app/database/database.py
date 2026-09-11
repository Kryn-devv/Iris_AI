"""Database engine configuration and session creation."""

from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from iris.app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Base ORM class."""
    pass


#: Set once init_db() has created the tables. Persistent stores check it
#: before touching the database, so a unit test that never initialised one
#: cannot scribble into the developer's real iris.db.
_db_ready = False


def is_ready() -> bool:
    return _db_ready


async def init_db() -> None:
    """Initialize database tables."""
    global _db_ready
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    _db_ready = True


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency yielding async database sessions."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
