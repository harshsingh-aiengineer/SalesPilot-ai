"""Worker entrypoint: python -m app.worker.main"""

from __future__ import annotations

import logging
import signal
import threading

from app.bootstrap import init_app
from app.worker.loop import run_forever


def main() -> None:
    init_app()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = threading.Event()

    def _stop(signum, _frame) -> None:  # type: ignore[no-untyped-def]
        logging.getLogger(__name__).info("shutdown signal %s", signum)
        stop.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    run_forever(stop)


if __name__ == "__main__":
    main()
