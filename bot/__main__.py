import asyncio
import logging

from .app import main


def run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Stopped by user")


if __name__ == "__main__":
    run()
