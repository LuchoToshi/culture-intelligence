import logging

from rich.logging import RichHandler


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(name)s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
        force=True,
    )
    # Keep third-party chatter out of normal runs.
    for noisy in ("httpx", "urllib3", "alembic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
