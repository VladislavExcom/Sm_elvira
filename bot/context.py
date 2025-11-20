from __future__ import annotations

from typing import Optional, Set

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from .config import Settings
from .db import Database
from .models import User

bot_instance: Optional[Bot] = None
session_factory: Optional[sessionmaker] = None
settings: Optional[Settings] = None
database: Optional[Database] = None
admin_cache: Set[int] = set()


def init_context(bot: Bot, factory: sessionmaker, config: Settings, db: Database) -> None:
    global bot_instance, session_factory, settings, database
    bot_instance = bot
    session_factory = factory
    settings = config
    database = db


def get_bot() -> Bot:
    if bot_instance is None:
        raise RuntimeError("Bot is not initialized")
    return bot_instance


def get_session_factory() -> sessionmaker:
    if session_factory is None:
        raise RuntimeError("Session factory is not initialized")
    return session_factory


def get_settings() -> Settings:
    if settings is None:
        raise RuntimeError("Settings are not initialized")
    return settings


def get_admins() -> Set[int]:
    global admin_cache
    return admin_cache or get_settings().admins


def get_database() -> Database:
    if database is None:
        raise RuntimeError("Database is not initialized")
    return database


async def refresh_admins_cache() -> Set[int]:
    """Sync admin IDs from DB; seed defaults if missing."""
    global admin_cache
    session_factory = get_session_factory()
    async with session_factory() as session:
        q = await session.execute(select(User.id).where(User.is_admin.is_(True)))
        ids = {row[0] for row in q.all()}
        if not ids:
            defaults = get_settings().admins
            for admin_id in defaults:
                q_user = await session.execute(select(User).where(User.id == admin_id))
                user = q_user.scalars().first()
                if not user:
                    user = User(id=admin_id, username=None, full_name=None, is_admin=True)
                    session.add(user)
                else:
                    user.is_admin = True
            await session.commit()
            ids = defaults
    admin_cache = set(ids)
    return admin_cache
