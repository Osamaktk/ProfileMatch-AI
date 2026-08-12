from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


DEPARTMENT_TAXONOMY = {
    "Clerk's Office": [
        "Meeting packets",
        "Ordinance approval",
        "Minutes archive",
        "Public records requests",
    ],
    "Zoning and Planning": [
        "Permit applications",
        "Site plans",
        "Review routing",
        "GIS lookup",
        "Planning Commission approval",
    ],
    "Accounting": ["AP Invoice Processing", "Purchasing", "Contract Routing"],
    "Public Works": [
        "Work Orders",
        "Capital Projects",
        "Asset Documentation",
        "Fleet Maintenance",
        "Road Projects",
    ],
    "Human Resources": [
        "Personnel Files",
        "Hiring",
        "Onboarding",
        "Performance Reviews",
    ],
    "Public Records": [
        "FOIA/Open Records",
        "Automated Routing",
        "Deadline Tracking",
        "Redaction Workflow",
    ],
    "IT": [
        "Help Desk Requests",
        "Change Management",
        "Asset Documentation",
        "SOP Library",
    ],
    "Forms": [
        "Reusable online forms",
        "Permit Request",
        "Vacation Request",
        "Public Records Request",
        "Citizen Complaint",
        "Work Order",
        "Purchase Request",
    ],
}

@dataclass(frozen=True)
class PipelineSettings:
    """Runtime configuration loaded from the project-level .env file."""

    root: Path
    serper_api_key: str | None
    openrouter_api_key: str | None
    openrouter_model: str
    openrouter_model_fallbacks: tuple[str, ...]
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    groq_model_fallbacks: tuple[str, ...] = (
        "llama-3.3-70b-versatile",
        "openai/gpt-oss-20b",
        "llama-3.1-8b-instant",
    )
    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_model_fallbacks: tuple[str, ...] = ("deepseek-v4-pro",)
    daily_request_limit: int = 50
    llm_throttle_seconds: float = 4.0
    serper_timeout_seconds: float = 15.0
    serper_retries: int = 2
    serper_results: int = 3

    @property
    def cache_directory(self) -> Path:
        return self.root / ".cache"

    @property
    def all_models(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                model.strip()
                for model in (self.openrouter_model, *self.openrouter_model_fallbacks)
                if model.strip()
            )
        )

    @property
    def all_deepseek_models(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                model.strip()
                for model in (self.deepseek_model, *self.deepseek_model_fallbacks)
                if model.strip()
            )
        )

    @property
    def all_groq_models(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                model.strip()
                for model in (self.groq_model, *self.groq_model_fallbacks)
                if model.strip()
            )
        )

    def require_credentials(self) -> None:
        missing = []
        if not self.serper_api_key:
            missing.append("SERPER_API_KEY")
        if not self.groq_api_key and not self.deepseek_api_key and not self.openrouter_api_key:
            missing.append("GROQ_API_KEY, DEEPSEEK_API_KEY, or OPENROUTER_API_KEY")
        if missing:
            raise RuntimeError(
                "Missing required backend environment variable(s): " + ", ".join(missing)
            )

    @classmethod
    def load(cls, root: Path | None = None) -> "PipelineSettings":
        root = (root or Path(__file__).resolve().parents[2]).resolve()
        load_dotenv(root / ".env", override=False)

        def integer(name: str, default: int, minimum: int = 0) -> int:
            try:
                return max(minimum, int(os.getenv(name, str(default))))
            except ValueError:
                return default

        def number(name: str, default: float, minimum: float = 0.0) -> float:
            try:
                return max(minimum, float(os.getenv(name, str(default))))
            except ValueError:
                return default

        fallbacks = tuple(
            item.strip()
            for item in os.getenv(
                "OPENROUTER_MODEL_FALLBACKS",
                "google/gemma-4-26b-a4b-it:free,"
                "nvidia/nemotron-3-super-120b-a12b:free,"
                "nvidia/nemotron-nano-9b-v2:free,openai/gpt-oss-20b:free",
            ).split(",")
            if item.strip()
        )
        deepseek_fallbacks = tuple(
            item.strip()
            for item in os.getenv(
                "DEEPSEEK_MODEL_FALLBACKS", "deepseek-v4-pro"
            ).split(",")
            if item.strip()
        )
        groq_fallbacks = tuple(
            item.strip()
            for item in os.getenv(
                "GROQ_MODEL_FALLBACKS",
                "llama-3.3-70b-versatile,openai/gpt-oss-20b,llama-3.1-8b-instant",
            ).split(",")
            if item.strip()
        )
        return cls(
            root=root,
            serper_api_key=os.getenv("SERPER_API_KEY") or None,
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
            openrouter_model=os.getenv(
                "OPENROUTER_MODEL", "openrouter/free"
            ).strip(),
            openrouter_model_fallbacks=fallbacks,
            groq_api_key=os.getenv("GROQ_API_KEY") or None,
            groq_model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip(),
            groq_model_fallbacks=groq_fallbacks,
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY") or None,
            deepseek_model=os.getenv(
                "DEEPSEEK_MODEL", "deepseek-v4-flash"
            ).strip(),
            deepseek_model_fallbacks=deepseek_fallbacks,
            daily_request_limit=integer("DAILY_REQUEST_LIMIT", 50, 1),
            llm_throttle_seconds=number("LLM_THROTTLE_SECONDS", 4.0),
            serper_timeout_seconds=number("SERPER_TIMEOUT_SECONDS", 15.0, 1.0),
            serper_retries=integer("SERPER_RETRIES", 2),
            serper_results=integer("SERPER_RESULTS", 3, 1),
        )


def taxonomy_text() -> str:
    """Return the exact taxonomy in compact prompt-friendly form."""
    return "\n".join(
        f"- {bucket}: {', '.join(functions)}"
        for bucket, functions in DEPARTMENT_TAXONOMY.items()
    )
