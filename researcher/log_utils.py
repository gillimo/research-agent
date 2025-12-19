import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional


def setup_logger(path: Path, name: str = "researcher", max_bytes: int = 2_000_000, backups: int = 3) -> logging.Logger:
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        if os.environ.get("MARTIN_LOG_STDOUT") != "1":
            for h in list(logger.handlers):
                if isinstance(h, logging.StreamHandler):
                    logger.removeHandler(h)
        return logger
    logger.setLevel(logging.INFO)
    fh = RotatingFileHandler(path, encoding="utf-8", maxBytes=max_bytes, backupCount=backups)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    if os.environ.get("MARTIN_LOG_STDOUT") == "1":
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)
    return logger


def log_event(logger: Optional[logging.Logger], msg: str) -> None:
    if logger:
        logger.info(msg)
