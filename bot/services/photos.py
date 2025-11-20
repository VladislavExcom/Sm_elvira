import mimetypes
import os
from pathlib import Path
from typing import Iterable, List, Tuple

from sqlalchemy import select

from ..context import get_session_factory
from ..models import OrderPhoto


async def persist_order_photos(order_id: int, entries: List[Tuple[str, str]]) -> None:
    """Store copies of photo files in DB for reliability."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        existing = await session.execute(
            select(OrderPhoto).where(OrderPhoto.order_id == order_id)
        )
        existing_map = {photo.source_path: photo for photo in existing.scalars()}
        desired_paths = {local for local, _ in entries if local}
        changed = False

        # Remove orphaned photos
        for src_path, photo in list(existing_map.items()):
            if src_path not in desired_paths:
                await session.delete(photo)
                changed = True

        for local_path, _ in entries:
            if not local_path:
                continue
            if not os.path.exists(local_path):
                continue
            if local_path in existing_map:
                continue
            try:
                data = Path(local_path).read_bytes()
            except OSError:
                continue
            file_name = os.path.basename(local_path)
            mime_type = mimetypes.guess_type(file_name)[0]
            photo = OrderPhoto(
                order_id=order_id,
                source_path=local_path,
                file_name=file_name,
                mime_type=mime_type,
                data=data,
            )
            session.add(photo)
            changed = True
        if changed:
            await session.commit()


async def restore_order_photos(order_id: int) -> None:
    """Ensure photo files exist on disk, restoring from DB copies if needed."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        q = await session.execute(select(OrderPhoto).where(OrderPhoto.order_id == order_id))
        for photo in q.scalars():
            path = Path(photo.source_path)
            if path.exists():
                continue
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(photo.data)
            except OSError:
                continue
