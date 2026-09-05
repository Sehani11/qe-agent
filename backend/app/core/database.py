"""Database engine and session dependency.

Provides the async SQLAlchemy engine and a FastAPI dependency
for per-request database sessions.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    future=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    # Test a pooled connection before handing it out, and replace it if the
    # server has gone away. Supabase's pooler closes idle connections, and
    # SQLAlchemy otherwise serves the dead one straight from the pool: the
    # failure lands on whatever query runs next, reported as
    # `InterfaceError: connection is closed`, which reads like a database
    # outage rather than an expired connection.
    #
    # The evaluation pipeline provoked this reliably. A single fine-tuned
    # generation can run for ~175s on a holdout item with six AC clauses, and
    # the session sits idle for that whole time before writing its row — so a
    # run would die partway through, having done all the expensive work.
    pool_pre_ping=True,
    # Retire connections proactively rather than waiting for the pooler to do
    # it. Cheap, because pre-ping already handles the ones that slip through.
    pool_recycle=300,
    # Supabase uses PgBouncer in transaction mode which doesn't support
    # prepared statements — disable the cache to avoid DuplicatePreparedStatementError
    connect_args={"statement_cache_size": 0},
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
