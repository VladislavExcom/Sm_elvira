import time
from typing import Callable, Any, Awaitable

from aiogram import BaseMiddleware

from ..metrics import ERROR_COUNTER, PROCESSING_TIME, UPDATE_COUNTER


class MetricsMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict], Awaitable[Any]],
        event: Any,
        data: dict,
    ) -> Any:
        event_type = event.__class__.__name__
        UPDATE_COUNTER.labels(event_type=event_type).inc()
        start = time.perf_counter()
        try:
            return await handler(event, data)
        except Exception:
            ERROR_COUNTER.labels(event_type=event_type).inc()
            raise
        finally:
            PROCESSING_TIME.labels(event_type=event_type).observe(time.perf_counter() - start)
