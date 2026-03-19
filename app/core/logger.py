import logging
import os
from logging.handlers import RotatingFileHandler

from app.core.config import settings


def setup_logger():
    logger = logging.getLogger("ollama2api")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if logger.handlers:
        return logger

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    fh = RotatingFileHandler(
        os.path.join(log_dir, "ollama2api.log"),
        maxBytes=settings.max_log_file_mb * 1024 * 1024,
        backupCount=3,
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


logger = setup_logger()
