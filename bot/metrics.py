import logging
import os
from typing import Optional

from prometheus_client import Counter, Histogram, start_http_server

logger = logging.getLogger(__name__)

UPDATE_COUNTER = Counter("bot_updates_total", "Всего обработанных обновлений", ["event_type"])
ERROR_COUNTER = Counter("bot_update_errors_total", "Ошибки при обработке обновлений", ["event_type"])
PROCESSING_TIME = Histogram(
    "bot_update_processing_seconds", "Время обработки обновлений", ["event_type"]
)

_METRICS_STARTED = False


def setup_metrics_server() -> None:
    global _METRICS_STARTED
    if _METRICS_STARTED:
        return
    port = int(os.getenv("METRICS_PORT", "9000"))
    start_http_server(port)
    logger.info("Prometheus metrics server started on port %s", port)
    _METRICS_STARTED = True
