"""Logging setup shared by the CLI."""

from __future__ import annotations

import logging


def configure_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # plexapi is chatty at INFO; keep it to warnings.
    logging.getLogger("plexapi").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
