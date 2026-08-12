from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx

from securedesk.config import Settings


logger = logging.getLogger(__name__)


class SerperRequestError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class LinkedInCandidate:
    url: str
    title: str
    snippet: str
    score: int
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class LinkedInLookup:
    status: Literal["matched", "unclear", "not_found", "incomplete"]
    url: str | None = None
    confidence: int = 0
    query: str = ""
    title: str | None = None
    snippet: str | None = None
    candidates: list[LinkedInCandidate] = field(default_factory=list)
    note: str = ""


class SerperLinkedInFinder:
    """Phase 1 LinkedIn finder based only on the user-provided Serper script."""

    endpoint = "https://google.serper.dev/search"

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = settings.serper_api_key
        self.transport = transport
        self.timeout_seconds = 30.0
        self.max_retries = 2

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def lookup_linkedin(self, contact: dict[str, Any]) -> LinkedInLookup:
        uploaded_name = _clean(contact.get("name"))
        organization = _clean(contact.get("organization"))
        email = _clean(contact.get("email"))
        try:
            search_attempt = max(1, int(contact.get("_search_attempt") or 1))
        except (TypeError, ValueError):
            search_attempt = 1

        if not uploaded_name:
            return LinkedInLookup(
                status="incomplete",
                note="A contact name is required before LinkedIn can be searched.",
            )
        if not self.api_key:
            raise SerperRequestError("Serper is not configured. Add SERPER_API_KEY to .env.")

        name_variants = _name_variants(uploaded_name)
        if search_attempt == 1:
            active_names = name_variants[:1]
        else:
            # The application has already waited 2-3 seconds. Try alternate
            # First/Last ordering first, then repeat the original script query.
            active_names = [*name_variants[1:], name_variants[0]]

        queries = _build_queries(active_names, organization, email)
        last_query = ""
        for stage, searched_name, query in queries:
            last_query = query
            try:
                result = await self._search_serper(query)
            except SerperRequestError as error:
                if error.status_code == 400:
                    logger.warning(
                        "phase1_query_skipped | name=%s | query=%s | reason=%s",
                        uploaded_name,
                        query,
                        " ".join(str(error).split())[:300],
                    )
                    continue
                raise

            found = _find_first_linkedin(result)
            logger.info(
                "phase1_linkedin_search | name=%s | searched_as=%s | stage=%s | found=%s",
                uploaded_name,
                searched_name,
                stage,
                bool(found),
            )
            if not found:
                continue

            evidence = ["Serper first LinkedIn result"]
            if stage == "organization":
                evidence.append("organization query")
            elif stage == "email":
                evidence.append("exact email query")
            else:
                evidence.append("name query")
            if _same_name_order(searched_name, uploaded_name):
                evidence.append("uploaded name order")
            else:
                evidence.append("alternate name order")

            candidate = LinkedInCandidate(
                url=found["url"],
                title=found["title"],
                snippet=found["snippet"],
                score={"organization": 9, "name": 7, "email": 10}[stage],
                evidence=tuple(evidence),
            )
            confidence = {"organization": 82, "name": 70, "email": 90}[stage]
            ordering_note = (
                "the uploaded name order"
                if _same_name_order(searched_name, uploaded_name)
                else f"the alternate name order '{searched_name}'"
            )
            return LinkedInLookup(
                status="matched",
                url=candidate.url,
                confidence=confidence,
                query=query,
                title=candidate.title or None,
                snippet=candidate.snippet or None,
                candidates=[candidate],
                note=(
                    f"Selected the first LinkedIn profile returned by the {stage} "
                    f"Serper query using {ordering_note}."
                ),
            )

        return LinkedInLookup(
            status="not_found",
            query=last_query,
            note=(
                "No linkedin.com/in profile was returned by the name and organization, "
                "name and LinkedIn, or exact email searches in either name order."
            ),
        )

    async def _search_serper(self, query: str) -> dict[str, Any]:
        headers = {
            "X-API-KEY": str(self.api_key),
            "Content-Type": "application/json",
        }
        payload = {"q": query, "num": 10}
        last_error: SerperRequestError | None = None

        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout_seconds,
                    transport=self.transport,
                ) as client:
                    response = await client.post(
                        self.endpoint,
                        headers=headers,
                        json=payload,
                    )
            except httpx.TimeoutException as error:
                last_error = SerperRequestError(
                    "Serper request timed out", status_code=408
                )
                if attempt >= self.max_retries:
                    raise last_error from error
                await asyncio.sleep(attempt + 1)
                continue
            except httpx.HTTPError as error:
                raise SerperRequestError(
                    f"Serper connection failed: {type(error).__name__}",
                    status_code=503,
                ) from error

            if response.status_code < 400:
                try:
                    data = response.json()
                except ValueError as error:
                    raise SerperRequestError(
                        "Serper returned invalid JSON", status_code=502
                    ) from error
                if not isinstance(data, dict):
                    raise SerperRequestError(
                        "Serper returned an unexpected response", status_code=502
                    )
                return data

            last_error = SerperRequestError(
                _response_error(response), status_code=response.status_code
            )
            if response.status_code not in {408, 429, 500, 502, 503, 504}:
                raise last_error
            if attempt < self.max_retries:
                await asyncio.sleep(attempt + 1)

        raise last_error or SerperRequestError("Serper search failed")


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip()
    return text or None


def _quoted(value: str) -> str:
    return f'"{value.replace(chr(34), " ").strip()}"'


def _name_variants(name: str) -> list[str]:
    """Create search-only name variants without changing the uploaded value."""

    variants: list[str] = []
    comma_parts = [part.strip() for part in name.split(",") if part.strip()]
    if len(comma_parts) == 2:
        variants.append(f"{comma_parts[1]} {comma_parts[0]}")
        variants.append(f"{comma_parts[0]} {comma_parts[1]}")
    else:
        cleaned = " ".join(re.findall(r"[^\W_]+", name, flags=re.UNICODE))
        variants.append(cleaned or name)
        tokens = cleaned.split()
        if len(tokens) >= 2:
            variants.append(" ".join(reversed(tokens)))
            if len(tokens) > 2:
                variants.append(" ".join([tokens[-1], *tokens[:-1]]))

    unique: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        normalized = variant.casefold()
        if variant and normalized not in seen:
            seen.add(normalized)
            unique.append(variant)
    return unique or [name]


def _build_queries(
    names: list[str],
    organization: str | None,
    email: str | None,
) -> list[tuple[str, str, str]]:
    """Build only the three queries from the supplied script for each name order."""

    queries: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for name in names:
        requested: list[tuple[str, str]] = []
        if organization:
            requested.append(
                ("organization", f"site:linkedin.com/in {_quoted(name)} {_quoted(organization)}")
            )
        requested.append(("name", f"{name} LinkedIn"))
        if email:
            requested.append(("email", _quoted(email)))
        for stage, query in requested:
            normalized = query.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            queries.append((stage, name, query))
    return queries


def _canonical_linkedin_url(value: Any) -> str | None:
    text = _clean(value)
    if not text:
        return None
    parsed = urlsplit(text)
    host = parsed.netloc.casefold().split(":", 1)[0]
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return None
    path = re.sub(r"/{2,}", "/", parsed.path).rstrip("/")
    if not path.casefold().startswith("/in/"):
        return None
    return urlunsplit(("https", "www.linkedin.com", path, "", ""))


def _find_first_linkedin(result: dict[str, Any] | None) -> dict[str, str] | None:
    if not result:
        return None
    organic = result.get("organic")
    if not isinstance(organic, list):
        return None
    for item in organic:
        if not isinstance(item, dict):
            continue
        link = _canonical_linkedin_url(item.get("link"))
        if link:
            return {
                "url": link,
                "title": _clean(item.get("title")) or "",
                "snippet": _clean(item.get("snippet")) or "",
            }
    return None


def _same_name_order(left: str, right: str) -> bool:
    normalize = lambda value: re.findall(
        r"[^\W_]+", value.casefold(), flags=re.UNICODE
    )
    return normalize(left) == normalize(right)


def _response_error(response: httpx.Response) -> str:
    fallback = f"Serper search failed ({response.status_code})"
    try:
        payload = response.json()
    except ValueError:
        return fallback
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("error")
        if message:
            return f"Serper: {str(message)[:500]}"
    return fallback
