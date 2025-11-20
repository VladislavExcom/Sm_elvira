from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from .config import Settings
from .models import Base


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine = create_async_engine(settings.database_dsn, future=True, echo=False)
        self.session_factory = sessionmaker(bind=self.engine, class_=AsyncSession, expire_on_commit=False)

    async def init_models(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()


__all__ = ["Database"]
