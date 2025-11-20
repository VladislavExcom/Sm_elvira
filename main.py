import asyncio
import logging

from bot.app import main as run_bot


if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Stopped by user")
