from __future__ import annotations

import logging
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from securedesk.enrichment.identity import split_location
from securedesk.enrichment.models import ContactInput
from contact_enrichment.checkpoint import DailyLimitReached
from contact_enrichment.llm import VerificationProviderUnavailableError
from securedesk.enrichment.errors import EnrichmentStoppedError
from securedesk.enrichment.store import BatchEnrichmentStore


logger = logging.getLogger(__name__)
ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


def contact_input_from_row(row: dict[str, Any]) -> ContactInput:
    city, state = split_location(
        row.get("location"), row.get("city"), row.get("state")
    )
    return ContactInput(
        row_id=row.get("row_id"),
        source_row=row.get("source_row"),
        name=row.get("name") or "",
        email=row.get("email"),
        organization=row.get("organization"),
        linkedin_url=row.get("linkedin_url"),
        linkedin_title=row.get("linkedin_title"),
        linkedin_snippet=row.get("linkedin_snippet"),
        existing_city=city,
        existing_state=state,
        existing_job_title=row.get("title"),
        organization_website=row.get("organization_website"),
        original_data=row.get("original_data") or {},
    )


class BatchEnrichmentService:
    def __init__(self, service: Any):
        self.service = service

    async def process_contacts(
        self,
        rows: list[dict[str, Any]],
        store: BatchEnrichmentStore,
        *,
        limit: int | None = None,
        retry_failed: bool = True,
        progress: ProgressCallback | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> dict[str, int | float]:
        row_map = {int(row["row_id"]): row for row in rows}
        store.seed(list(row_map))
        pending = store.pending_rows(retry_failed=retry_failed, limit=limit)
        logger.info("batch_started | total=%s", len(pending))
        for position, row_id in enumerate(pending, start=1):
            if should_stop and should_stop():
                break
            row = row_map.get(row_id)
            if row is None:
                store.save_failure(row_id, "The source contact row was not found.")
                continue
            store.mark_processing(row_id)
            if progress:
                await progress(
                    {
                        "event": "started",
                        "position": position,
                        "row_id": row_id,
                        "name": row.get("name"),
                        "stage": "resolving_identity",
                    }
                )
            try:
                contact = contact_input_from_row(row)
                if not contact.name:
                    raise ValueError("Contact name is missing")

                async def contact_progress(details: dict[str, object]) -> None:
                    if progress:
                        await progress(
                            {
                                "event": "progress",
                                "position": position,
                                "row_id": row_id,
                                "name": contact.name,
                                **details,
                            }
                        )

                parameters = inspect.signature(self.service.enrich).parameters
                if "progress" in parameters:
                    result = await self.service.enrich(
                        contact,
                        progress=contact_progress,
                        should_stop=should_stop,
                    )
                else:
                    result = await self.service.enrich(contact)
                store.save_result(row_id, result)
                event = {
                    "event": "completed",
                    "position": position,
                    "row_id": row_id,
                    "name": contact.name,
                    "stage": "saved",
                    "status": result.status,
                    "result": result.model_dump(mode="json"),
                }
            except EnrichmentStoppedError as error:
                store.mark_pending(row_id, str(error))
                event = {
                    "event": "stopped",
                    "position": position,
                    "row_id": row_id,
                    "name": row.get("name"),
                    "stage": "stopped",
                }
                if progress:
                    await progress(event)
                break
            except DailyLimitReached as error:
                store.mark_pending(row_id, str(error))
                event = {
                    "event": "daily_limit_reached",
                    "position": position,
                    "row_id": row_id,
                    "name": row.get("name"),
                    "stage": "daily_limit_reached",
                    "error": str(error),
                }
                if progress:
                    await progress(event)
                break
            except VerificationProviderUnavailableError as error:
                store.mark_pending(row_id, str(error))
                event = {
                    "event": "provider_unavailable",
                    "position": position,
                    "row_id": row_id,
                    "name": row.get("name"),
                    "stage": "provider_unavailable",
                    "error": str(error),
                }
                if progress:
                    await progress(event)
                break
            except Exception as error:
                message = f"{type(error).__name__}: {' '.join(str(error).split())}"[:1000]
                logger.exception("contact_enrichment_failed | row_id=%s", row_id)
                store.save_failure(row_id, message)
                event = {
                    "event": "failed",
                    "position": position,
                    "row_id": row_id,
                    "name": row.get("name"),
                    "stage": "failed",
                    "error": message,
                }
            if progress:
                await progress(event)
        statistics = store.statistics()
        logger.info(
            "batch_completed | processed=%s | completed=%s | review=%s | failed=%s",
            statistics["processed"],
            statistics["completed"],
            statistics["manual_review"],
            statistics["failed"],
        )
        return statistics
