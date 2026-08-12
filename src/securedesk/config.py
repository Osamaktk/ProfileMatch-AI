from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    root: Path
    uploads_directory: Path
    serper_api_key: str | None
    openrouter_api_key: str | None
    groq_api_key: str | None
    deepseek_api_key: str | None
    linkedin_cache_path: Path
    max_upload_bytes: int = 20 * 1024 * 1024
    header_scan_rows: int = 25
    contact_enrichment_enabled: bool = True

    @classmethod
    def load(cls, root: Path | None = None) -> "Settings":
        root = (root or project_root()).resolve()
        load_dotenv(root / ".env", override=False)

        def flag(name: str, default: bool) -> bool:
            raw = os.getenv(name)
            if raw is None:
                return default
            return raw.strip().casefold() in {"1", "true", "yes", "on"}

        return cls(
            root=root,
            uploads_directory=root / "database" / "uploads",
            serper_api_key=os.getenv("SERPER_API_KEY") or None,
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
            groq_api_key=os.getenv("GROQ_API_KEY") or None,
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY") or None,
            linkedin_cache_path=root / "database" / "linkedin_cache.db",
            contact_enrichment_enabled=flag("CONTACT_ENRICHMENT_ENABLED", True),
        )
