from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from contact_enrichment.cache import ApiCache
from contact_enrichment.checkpoint import DailyRequestCounter
from contact_enrichment.config import DEPARTMENT_TAXONOMY, PipelineSettings
from contact_enrichment.llm import (
    ContactEnrichment,
    DeepSeekEnricher,
    GroqEnricher,
    OpenRouterEnricher,
    ProviderFallbackEnricher,
)
from contact_enrichment.search import (
    SearchItem,
    SerperClient,
    SerperSearchError,
    first_linkedin_url,
)
from securedesk.config import Settings
from securedesk.enrichment.identity import canonical_linkedin_url, split_location
from securedesk.enrichment.errors import EnrichmentStoppedError
from securedesk.enrichment.models import ContactEnrichmentResult, ContactInput, EvidenceItem


ProgressCallback = Callable[[dict[str, object]], Awaitable[None]]


class LlmContactEnrichmentService:
    """Gather cached search evidence, then let the LLM verify every output field."""

    # One broad professional search, one LinkedIn lookup/reuse step, and one
    # organization/city news search. Dedicated Facebook/YouTube queries added
    # large amounts of unrelated evidence without improving verification.
    SEARCH_TOTAL = 3
    EVIDENCE_PER_SOURCE = 3

    def __init__(
        self,
        serper: SerperClient,
        llm: Any,
    ) -> None:
        self.serper = serper
        self.llm = llm
        self.provider_name = str(getattr(llm, "provider_name", "OpenRouter"))

    async def enrich(
        self,
        contact: ContactInput,
        *,
        progress: ProgressCallback | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> ContactEnrichmentResult:
        organization = contact.organization or ""
        title = contact.linkedin_title or contact.existing_job_title or ""
        city_hint = ", ".join(
            value for value in (contact.existing_city, contact.existing_state) if value
        )
        searches_completed = 0
        sources: dict[str, list[dict[str, str]]] = {
            "web": [],
            "linkedin": [],
            "local_news": [],
        }
        search_failures: list[str] = []

        async def report(stage: str) -> None:
            if progress:
                await progress(
                    {
                        "stage": stage,
                        "searches_completed": searches_completed,
                        "searches_total": self.SEARCH_TOTAL,
                        "source_count": sum(len(items) for items in sources.values()),
                        "verification_provider": self.provider_name,
                    }
                )

        def check_stopped() -> None:
            if should_stop and should_stop():
                raise EnrichmentStoppedError(
                    "Phase 2 was stopped between verification steps; this row is safe to retry."
                )

        async def run_search(
            source_name: str,
            stage: str,
            call: Callable[[], list[SearchItem]],
        ) -> None:
            nonlocal searches_completed
            check_stopped()
            await report(stage)
            try:
                items = await asyncio.to_thread(call)
            except SerperSearchError as error:
                items = []
                search_failures.append(f"{source_name}: {error}")
            items = items[: self.EVIDENCE_PER_SOURCE]
            sources[source_name] = [item.to_dict() for item in items]
            searches_completed += 1
            await report(stage)

        existing_linkedin = canonical_linkedin_url(contact.linkedin_url)
        search_tasks = [
            run_search(
                "web",
                "searching_web",
                partial(self.serper.search_general, contact.name, organization),
            ),
            run_search(
                "local_news",
                "searching_local_news",
                partial(
                    self.serper.search_local_news,
                    city_hint,
                    title,
                    organization,
                ),
            ),
        ]
        if existing_linkedin:
            check_stopped()
            await report("reusing_linkedin")
            linkedin_item = SearchItem(
                title=contact.linkedin_title or f"{contact.name} | LinkedIn",
                snippet=contact.linkedin_snippet or title,
                link=existing_linkedin,
                source="LinkedIn",
            )
            sources["linkedin"] = [linkedin_item.to_dict()]
            searches_completed += 1
            await report("reusing_linkedin")
        else:
            search_tasks.append(
                run_search(
                    "linkedin",
                    "searching_linkedin",
                    partial(self.serper.search_linkedin, contact.name, organization),
                )
            )

        # Independent evidence searches run together. This reduces the search
        # portion from several serial network waits to one network round trip.
        await asyncio.gather(*search_tasks)

        if not existing_linkedin:
            discovered = first_linkedin_url(
                [SearchItem(**item) for item in sources["linkedin"]]
            )
            existing_linkedin = discovered or None

        evidence_groups = [
            (source_name, [SearchItem(**item) for item in sources[source_name]])
            for source_name in ("linkedin", "web", "local_news")
            if sources[source_name]
        ]

        check_stopped()
        await report("verifying_with_llm")
        verified = await asyncio.to_thread(
            self.llm.enrich,
            {
                "name": contact.name,
                "organization": organization,
                "title": title,
                "city_hint": city_hint,
                "linkedin_url": existing_linkedin or "",
            },
            sources,
            search_failed=bool(search_failures),
        )
        completed_provider = str(
            getattr(self.llm, "last_provider_name", self.provider_name)
        )
        check_stopped()
        await report("validating_llm_response")
        return self._to_result(
            contact,
            verified,
            existing_linkedin,
            evidence_groups,
            search_failures,
            completed_provider,
        )

    @staticmethod
    def _to_result(
        contact: ContactInput,
        verified: ContactEnrichment,
        linkedin_url: str | None,
        evidence_groups: list[tuple[str, list[SearchItem]]],
        search_failures: list[str],
        provider_name: str,
    ) -> ContactEnrichmentResult:
        city, state = split_location(
            verified.city or None,
            explicit_state=contact.existing_state,
        )
        evidence: list[EvidenceItem] = []
        seen_urls: set[str] = set()
        for source_name, items in evidence_groups:
            for item in items:
                if item.link in seen_urls:
                    continue
                seen_urls.add(item.link)
                evidence.append(
                    EvidenceItem(
                        evidence_type=f"{source_name}_search_result",
                        source_url=item.link,
                        source_title=item.title or None,
                        extracted_text=item.snippet or None,
                        source_type=_source_type(source_name, item.link),
                        confidence=0.75,
                    )
                )

        review_reasons = [verified.flag_reason] if verified.flag_reason else []
        valid_linkedin = canonical_linkedin_url(linkedin_url)
        bucket = verified.department_bucket
        sub_function = verified.sub_function or None
        if not valid_linkedin:
            review_reasons.append("LinkedIn profile is missing or invalid")
        if not bucket or bucket not in DEPARTMENT_TAXONOMY:
            review_reasons.append("Department category is missing or invalid")
        if (
            not bucket
            or bucket not in DEPARTMENT_TAXONOMY
            or not sub_function
            or sub_function not in DEPARTMENT_TAXONOMY[bucket]
        ):
            review_reasons.append(
                "Sub-function is missing or invalid for the selected department"
            )
        if search_failures:
            review_reasons.append("One or more public-source searches failed")
        review_reasons = list(dict.fromkeys(review_reasons))
        manual_review = bool(verified.needs_review or review_reasons)
        review_reason = "; ".join(review_reasons) or None
        complete = bool(
            not manual_review
            and verified.role_background
            and valid_linkedin
            and bucket
            and sub_function
            and evidence
        )
        return ContactEnrichmentResult(
            name=contact.name,
            organization=contact.organization,
            linkedin_url=valid_linkedin,
            role=verified.role_title or None,
            background_summary=verified.role_background or None,
            city=city,
            state=state,
            department_raw=bucket,
            department_bucket=bucket,
            sub_function=sub_function,
            local_context=verified.local_context or None,
            evidence=evidence,
            sources=[item.source_url for item in evidence],
            profile_complete=complete,
            manual_review=manual_review,
            review_reason=review_reason,
            status="manual_review" if manual_review else "completed",
            verification_method={
                "groq": "serper_groq_v4",
                "deepseek": "serper_deepseek_v1",
                "openrouter": "serper_openrouter_v2",
            }.get(provider_name.casefold(), "serper_llm_v1"),
        )


def build_llm_enrichment_service(settings: Settings) -> LlmContactEnrichmentService:
    pipeline_settings = PipelineSettings.load(settings.root)
    pipeline_settings.require_credentials()
    cache = ApiCache(pipeline_settings.cache_directory / "api_cache.sqlite3")
    providers: list[Any] = []
    if pipeline_settings.groq_api_key:
        providers.append(GroqEnricher(pipeline_settings, cache))
    if pipeline_settings.deepseek_api_key:
        providers.append(DeepSeekEnricher(pipeline_settings, cache))
    if pipeline_settings.openrouter_api_key:
        counter = DailyRequestCounter(
            pipeline_settings.cache_directory / "daily_openrouter_requests.json",
            str(pipeline_settings.openrouter_api_key),
            pipeline_settings.daily_request_limit,
        )
        providers.append(OpenRouterEnricher(pipeline_settings, cache, counter))
    llm = (
        providers[0]
        if len(providers) == 1
        else ProviderFallbackEnricher(*providers)
    )
    return LlmContactEnrichmentService(
        SerperClient(pipeline_settings, cache),
        llm,
    )


def _source_type(source_name: str, url: str) -> str:
    if source_name == "linkedin":
        return "linkedin_existing" if "linkedin.com/in/" in url.casefold() else "linkedin_search"
    return {
        "web": "web_search",
        "facebook": "facebook_search",
        "youtube": "youtube_search",
        "local_news": "news_search",
    }.get(source_name, "other_public_source")
