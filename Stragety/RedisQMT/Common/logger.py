"""Small timestamped logger shared by external RedisQMT strategies."""
import logging
from pathlib import Path


def build_logger(name, log_directory=None):
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("[%(asctime)s] %(message)s", "%H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)
    if log_directory:
        directory = Path(log_directory)
        directory.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(directory / (name + ".log"), encoding="utf-8")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger

