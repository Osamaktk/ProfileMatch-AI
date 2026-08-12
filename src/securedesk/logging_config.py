from __future__ import annotations

import logging
import os
from pathlib import Path


def configure_logging(root: Path, level: str | None = None) -> None:
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    selected_level = (level or os.getenv("SECUREDESK_LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(
        level=getattr(logging, selected_level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "securedesk.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )
