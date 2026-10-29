"""One place to configure logging so every module logs the same way."""

from __future__ import annotations

import logging

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level.upper(), format=LOG_FORMAT, force=True)
    # httpx logs every request at INFO, which is noisy and shows URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
