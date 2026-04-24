from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    # Auto-recover stale connections after DB/container restart
    pool_pre_ping=True,
    # Recycle connections every 30 min to prevent stale TCP sockets
    pool_recycle=1800,
    # FIX-2 (v2.63.8): pool sized for the Settings-page fan-out (34 GETs in
    # parallel) plus the Dashboard burst. Headroom verified live on BOS-HQ
    # (max_connections=100, current=6). Worker(5) + beat(2) + parser(5)
    # + backend(40) = 52, leaves 48% PG headroom.
    pool_size=20,
    max_overflow=20,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        try:
            yield session
            # Commit any uncommitted changes (no-op if endpoint already committed)
            if session.is_active:
                await session.commit()
        except Exception:
            if session.is_active:
                await session.rollback()
            raise
        finally:
            await session.close()
