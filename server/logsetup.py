"""Console + rotating file logging. Import once from app entrypoints."""
from __future__ import annotations

import logging
import logging.handlers
import os

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "logs")


def setup(level: str = "INFO", filename: str = "pickle.log") -> logging.Logger:
    os.makedirs(LOG_DIR, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    if not root.handlers:
        fh = logging.handlers.RotatingFileHandler(os.path.join(LOG_DIR, filename), maxBytes=20_000_000, backupCount=5)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        ch = logging.StreamHandler()
        ch.setLevel(getattr(logging, level.upper(), logging.INFO))
        ch.setFormatter(fmt)
        root.addHandler(fh)
        root.addHandler(ch)
    for noisy in ("aioice", "aiortc", "av"):
        logging.getLogger(noisy).setLevel(logging.INFO)
    return logging.getLogger("pickle")
