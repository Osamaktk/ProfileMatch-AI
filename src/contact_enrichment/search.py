from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import requests

from contact_enrichment.cache import ApiCache
from contact_enrichment.config import PipelineSettings


logger = logging.getLogger(__name__)


class SerperSearchError(RuntimeError):
    """A Serper request failed after all configured retries."""


@dataclass(frozen=True)
class SearchItem:
    title: str
    snippet: str
    link: str
    date: str = ""
    source: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class SerperClient:
    """Cached, retrying client for Serper's /search and /news endpoints."""

    base_url = "https://google.serper.dev"

    def __init__(
        self,
        settings: PipelineSettings,
        cache: ApiCache,
        *,
        session: requests.Session | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        if not settings.serper_api_key:
            raise RuntimeError("SERPER_API_KEY is not configured")
        self.settings = settings
        self.cache = cache
        self.session = session or requests.Session()
        self.sleeper = sleeper

    def search_general(self, name: str, organization: str) -> list[SearchItem]:
        return self._items("search", f'"{name}" "{organization}"')

    def search_facebook(self, name: str, organization: str) -> list[SearchItem]:
        return self._items("search", f'"{name}" "{organization}" facebook')

    def search_youtube(self, name: str, organization: str) -> list[SearchItem]:
        return self._items("search", f'"{name}" "{organization}" youtube')

    def search_linkedin(self, name: str, organization: str) -> list[SearchItem]:
        return self._items(
            "search", f'"{name}" "{organization}" site:linkedin.com/in'
        )

    def search_local_news(
        self,
        city: str,
        department_guess: str,
        organization: str,
    ) -> list[SearchItem]:
        if city and department_guess:
            query = f'"{city}" "{department_guess}" news'
        elif city:
            query = f'"{city}" local government news'
        else:
            query = f'"{organization}" local government news'
        return self._items("news", query)

    def _items(self, endpoint: str, query: str) -> list[SearchItem]:
        payload = self._call(endpoint, query, self.settings.serper_results)
        collection = payload.get("news" if endpoint == "news" else "organic", [])
        if not isinstance(collection, list):
            return []
        output: list[SearchItem] = []
        for raw in collection:
            if not isinstance(raw, dict):
                continue
            link = _single_line(raw.get("link"), 1000)
            if not link.startswith(("http://", "https://")):
                continue
            output.append(
                SearchItem(
                    title=_single_line(raw.get("title"), 300),
                    snippet=_single_line(raw.get("snippet"), 1200),
                    link=link,
                    date=_single_line(raw.get("date"), 100),
                    source=_single_line(raw.get("source"), 200),
                )
            )
        return output

    def _call(self, endpoint: str, query: str, num: int) -> dict[str, Any]:
        if endpoint not in {"search", "news"}:
            raise ValueError("Serper endpoint must be 'search' or 'news'")
        cache_key = f"{endpoint}|{num}|{query}"
        cached = self.cache.get("serper", cache_key)
        if isinstance(cached, dict):
            logger.info("serper_cache_hit | endpoint=%s | query=%s", endpoint, query)
            return cached

        last_error: Exception | None = None
        for attempt in range(self.settings.serper_retries + 1):
            try:
                response = self.session.post(
                    f"{self.base_url}/{endpoint}",
                    headers={
                        "X-API-KEY": str(self.settings.serper_api_key),
                        "Content-Type": "application/json",
                    },
                    json={"q": query, "num": num},
                    timeout=self.settings.serper_timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Serper returned a non-object JSON response")
                self.cache.set("serper", cache_key, payload)
                logger.info("serper_request_ok | endpoint=%s | query=%s", endpoint, query)
                return payload
            except (requests.RequestException, ValueError) as error:
                last_error = error
                logger.warning(
                    "serper_request_failed | endpoint=%s | attempt=%s/%s | error=%s",
                    endpoint,
                    attempt + 1,
                    self.settings.serper_retries + 1,
                    type(error).__name__,
                )
                if attempt < self.settings.serper_retries:
                    self.sleeper(float(2**attempt))
        raise SerperSearchError(
            f"Serper {endpoint} failed after {self.settings.serper_retries + 1} attempts: "
            f"{type(last_error).__name__ if last_error else 'unknown error'}"
        )


def first_linkedin_url(items: list[SearchItem]) -> str:
    for item in items:
        parsed = urlsplit(item.link)
        host = (parsed.hostname or "").casefold().removeprefix("www.")
        path = parsed.path.rstrip("/")
        if host == "linkedin.com" and path.casefold().startswith("/in/"):
            return urlunsplit(("https", "www.linkedin.com", path, "", ""))
    return ""


def _single_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]
