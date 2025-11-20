import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Set

DEFAULT_BOT_TOKEN = "7179761726:AAGBHM1uZa47rWyXylYJFD7ii61cPDf9pqc"
DEFAULT_DATABASE_DSN = "postgresql+asyncpg://bot_user:DKFl//1502@localhost:5432/orders_db"
DEFAULT_ADMINS = "1279907773"


def _parse_admins(raw: str) -> Set[int]:
    result: Set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            result.add(int(chunk))
        except ValueError:
            continue
    return result or {1279907773}


@dataclass
class Settings:
    bot_token: str = field(default_factory=lambda: os.getenv("BOT_TOKEN", DEFAULT_BOT_TOKEN))
    database_dsn: str = field(default_factory=lambda: os.getenv("DATABASE_DSN", DEFAULT_DATABASE_DSN))
    photos_dir: Path = field(default_factory=lambda: Path(os.getenv("PHOTOS_DIR", "photos")))
    tmp_dir: Path = field(default_factory=lambda: Path(os.getenv("TMP_DIR", "tmp")))
    photo_cdn_base: Optional[str] = field(default_factory=lambda: os.getenv("PHOTO_CDN_BASE"))
    admins: Set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        raw_admins = os.getenv("ADMINS", DEFAULT_ADMINS)
        parsed = _parse_admins(raw_admins)
        if not parsed:
            parsed = {1279907773}
        self.admins = parsed
        self.photos_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    return Settings()
