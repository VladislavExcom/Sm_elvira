import os
from typing import List, Optional, Tuple

from ..config import Settings


def build_public_photo_url(local_path: str, settings: Settings) -> str:
    base = settings.photo_cdn_base
    if base:
        return f"{base.rstrip('/')}/{os.path.basename(local_path)}"
    return os.path.abspath(local_path)


def pack_photo_entry(local_path: str, settings: Settings, public_url: Optional[str] = None) -> str:
    public = public_url or build_public_photo_url(local_path, settings)
    return f"{local_path}|{public}"


def parse_photo_entries(raw: Optional[str], settings: Settings) -> List[Tuple[str, str]]:
    entries: List[Tuple[str, str]] = []
    if not raw:
        return entries
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "|" in chunk:
            local, public = chunk.split("|", 1)
        else:
            local = chunk
            public = build_public_photo_url(local, settings)
        entries.append((local, public))
    return entries


def telegram_file_url(file_path: str, settings: Settings) -> str:
    return f"https://api.telegram.org/file/bot{settings.bot_token}/{file_path}"
