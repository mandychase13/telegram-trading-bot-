import logging
import sys
from logging.handlers import RotatingFileHandler
import os


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    logger.setLevel(level)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Rotating file handler
    os.makedirs("logs", exist_ok=True)
    file_handler = RotatingFileHandler(
        "logs/copy_vault.log",
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
