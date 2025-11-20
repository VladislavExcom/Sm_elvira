import logging
import os

logger = logging.getLogger(__name__)


def safe_remove_file(path: str) -> None:
    """Deletes a file ignoring errors."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception as exc:
        logger.debug("safe_remove_file failed for %s: %s", path, exc)
