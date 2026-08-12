from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from contact_enrichment.config import DEPARTMENT_TAXONOMY

from securedesk.config import Settings
from securedesk.database import Database
from securedesk.enrichment.batch import BatchEnrichmentService
from securedesk.enrichment.excel import export_enrichment_workbook
from securedesk.enrichment.llm_service import build_llm_enrichment_service
from securedesk.enrichment.identity import canonical_linkedin_url
from securedesk.enrichment.models import ContactEnrichmentResult, ContactInput
from securedesk.enrichment.store import BatchEnrichmentStore
from securedesk.linkedin_cache import LinkedInResultCache
from securedesk.models import utc_now_iso
from securedesk.uploads import batch_directory, create_upload_batch, get_upload_batch


class EnrichmentStartRequest(BaseModel):
    batch_id: str = Field(min_length=32, max_length=32)
    limit: int | None = Field(default=None, ge=1, le=10_000)
    retry_failed: bool = True


class EnrichmentReviewUpdate(BaseModel):
    role: str | None = Field(default=None, max_length=300)
    background_summary: str | None = Field(default=None, max_length=2000)
    city: str | None = Field(default=None, max_length=300)
    state: str | None = Field(default=None, max_length=100)
    department_bucket: str | None = Field(default=None, max_length=100)
    sub_function: str | None = Field(default=None, max_length=300)
    local_context: str | None = Field(default=None, max_length=1000)
    linkedin_url: str | None = Field(default=None, max_length=1000)
    manual_review: bool
    review_reason: str | None = Field(default=None, max_length=1000)

    @field_validator(
        "role",
        "background_summary",
        "city",
        "state",
        "department_bucket",
        "sub_function",
        "local_context",
        "linkedin_url",
        "review_reason",
        mode="before",
    )
    @classmethod
    def clean_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = " ".join(str(value).split()).strip()
        return text or None

    @model_validator(mode="after")
    def validate_review(self) -> "EnrichmentReviewUpdate":
        if self.linkedin_url and canonical_linkedin_url(self.linkedin_url) is None:
            raise ValueError("LinkedIn URL must be a valid linkedin.com/in profile URL")
        if self.department_bucket:
            functions = DEPARTMENT_TAXONOMY.get(self.department_bucket)
            if functions is None:
                raise ValueError("Department bucket is not in the approved taxonomy")
            if self.sub_function and self.sub_function not in functions:
                raise ValueError("Sub-function is not valid for the selected department bucket")
        elif self.sub_function:
            raise ValueError("Choose a department bucket before a sub-function")
        if self.manual_review:
            if not self.review_reason:
                raise ValueError("A review reason is required while the row remains flagged")
        elif not all(
            (self.role, self.background_summary, self.department_bucket, self.sub_function)
        ):
            raise ValueError(
                "Role, background, department bucket, and sub-function are required to clear the flag"
            )
        return self


def create_enrichment_router(
    settings: Settings,
    contacts_schema_path: Path,
    service: Any | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/enrichment", tags=["Phase 2 enrichment"])
    service_error: str | None = None
    if service is None:
        try:
            service = build_llm_enrichment_service(settings)
        except RuntimeError as error:
            service_error = str(error)
    provider_name = str(getattr(service, "provider_name", "OpenRouter"))
    linkedin_cache = LinkedInResultCache(settings.linkedin_cache_path)
    linkedin_cache.initialize()
    batch_service = BatchEnrichmentService(service) if service is not None else None
    batch_schema = settings.root / "database" / "batch_enrichment_schema.sql"
    lock = asyncio.Lock()
    state: dict[str, Any] = {
        "status": "idle",
        "batch_id": None,
        "total": 0,
        "processed": 0,
        "completed": 0,
        "manual_review": 0,
        "failed": 0,
        "current_row": None,
        "current_name": None,
        "stage": None,
        "searches_completed": 0,
        "searches_total": 3,
        "source_count": 0,
        "verification_provider": provider_name,
        "pause_reason": None,
        "started_at": None,
        "completed_at": None,
        "last_error": None,
        "recent_results": [],
        "stop_requested": False,
        "task": None,
    }

    def public_state() -> dict[str, Any]:
        return {
            key: value
            for key, value in state.items()
            if key not in {"task", "stop_requested"}
        }

    def resources(batch_id: str) -> tuple[dict[str, Any], Database, BatchEnrichmentStore]:
        try:
            manifest = get_upload_batch(settings.uploads_directory, batch_id)
        except (ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        directory = batch_directory(settings.uploads_directory, batch_id)
        database = Database(directory / "contacts.db", contacts_schema_path)
        database.initialize()
        result_store = BatchEnrichmentStore(directory / "contacts.db", batch_schema)
        result_store.initialize()
        contacts = database.list_contacts()
        result_store.seed([int(row["row_id"]) for row in contacts])
        reused_batch_id = str(manifest.get("phase1_reused_batch_id") or "")
        if reused_batch_id:
            try:
                reused_directory = batch_directory(
                    settings.uploads_directory, reused_batch_id
                )
            except ValueError:
                reused_directory = None
            if reused_directory is not None:
                result_store.restore_from_database(reused_directory / "contacts.db")
        result_store.restore_requeued_current_results()
        return manifest, database, result_store

    def report_path(batch_id: str, manifest: dict[str, Any]) -> tuple[str, Path]:
        stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", str(manifest["source_file"])).rsplit(
            ".", 1
        )[0]
        for suffix in ("_linkedin_results", "_enriched"):
            if stem.casefold().endswith(suffix):
                stem = stem[: -len(suffix)]
        filename = f"{stem}_enriched.xlsx"
        return filename, batch_directory(settings.uploads_directory, batch_id) / filename

    async def save_report(
        batch_id: str,
        manifest: dict[str, Any],
        database: Database,
        result_store: BatchEnrichmentStore,
    ) -> None:
        _, path = report_path(batch_id, manifest)
        await asyncio.to_thread(
            export_enrichment_workbook,
            database.list_contacts(),
            result_store.list_results(),
            list(manifest["headers"]),
            path,
        )

    async def run_job(request: EnrichmentStartRequest) -> None:
        try:
            manifest, database, result_store = resources(request.batch_id)
            contacts = database.list_contacts()
            pending = result_store.pending_rows(
                retry_failed=request.retry_failed, limit=request.limit
            )
            state["total"] = len(pending)

            async def progress(event: dict[str, Any]) -> None:
                updates = {
                    "current_row": event.get("row_id"),
                    "current_name": event.get("name"),
                    "stage": event.get("stage"),
                }
                for key in (
                    "searches_completed",
                    "searches_total",
                    "source_count",
                    "verification_provider",
                ):
                    if key in event:
                        updates[key] = event[key]
                if event.get("event") == "started":
                    updates.update(
                        searches_completed=0,
                        searches_total=3,
                        source_count=0,
                        verification_provider=provider_name,
                    )
                if event.get("event") == "daily_limit_reached":
                    updates.update(
                        pause_reason="daily_limit_reached",
                        last_error=event.get("error"),
                    )
                if event.get("event") == "provider_unavailable":
                    updates.update(
                        pause_reason="provider_unavailable",
                        last_error=event.get("error"),
                    )
                state.update(updates)
                if event["event"] in {"completed", "failed"}:
                    statistics = result_store.statistics()
                    state.update(
                        processed=min(
                            state["total"],
                            state["processed"] + 1,
                        ),
                        completed=statistics["completed"],
                        manual_review=statistics["manual_review"],
                        failed=statistics["failed"],
                    )
                    state["recent_results"] = [
                        {
                            "row_id": event.get("row_id"),
                            "name": event.get("name"),
                            "status": event.get("status", "failed"),
                            "result": event.get("result"),
                            "error": event.get("error"),
                        },
                        *state["recent_results"],
                    ][:20]
                    await save_report(request.batch_id, manifest, database, result_store)

            if batch_service is None:
                raise RuntimeError(service_error or "Serper + LLM provider is not configured")
            await batch_service.process_contacts(
                contacts,
                result_store,
                limit=request.limit,
                retry_failed=request.retry_failed,
                progress=progress,
                should_stop=lambda: bool(state["stop_requested"]),
            )
            await save_report(request.batch_id, manifest, database, result_store)
            final_statistics = result_store.statistics()
            state.update(
                completed=final_statistics["completed"],
                manual_review=final_statistics["manual_review"],
                failed=final_statistics["failed"],
            )
            if state["stop_requested"] or state["pause_reason"]:
                state["status"] = "stopped"
            elif final_statistics["pending"] or final_statistics["processing"]:
                state["status"] = "stopped"
            else:
                state["status"] = "complete"
        except Exception as error:
            state.update(
                status="failed",
                last_error=f"{type(error).__name__}: {' '.join(str(error).split())}"[:1000],
            )
        finally:
            state.update(
                current_row=None,
                current_name=None,
                stage=None,
                completed_at=utc_now_iso(),
            )

    async def schedule(request: EnrichmentStartRequest) -> dict[str, Any]:
        if not settings.contact_enrichment_enabled:
            raise HTTPException(status_code=503, detail="Phase 2 enrichment is disabled")
        if batch_service is None:
            raise HTTPException(
                status_code=503,
                detail=service_error or "Serper + LLM provider is not configured",
            )
        async with lock:
            if state["status"] == "running":
                raise HTTPException(status_code=409, detail="A Phase 2 enrichment job is already running")
            _, _, result_store = resources(request.batch_id)
            result_store.requeue_legacy_results()
            result_store.recover_interrupted()
            state.update(
                status="running",
                batch_id=request.batch_id,
                total=0,
                processed=0,
                completed=0,
                manual_review=0,
                failed=0,
                current_row=None,
                current_name=None,
                stage="queued",
                searches_completed=0,
                searches_total=3,
                source_count=0,
                verification_provider=provider_name,
                pause_reason=None,
                started_at=utc_now_iso(),
                completed_at=None,
                last_error=None,
                recent_results=[],
                stop_requested=False,
            )
            state["task"] = asyncio.create_task(run_job(request))
        return public_state()

    @router.post("/contact")
    async def enrich_contact(contact: ContactInput) -> dict[str, Any]:
        if not settings.contact_enrichment_enabled:
            raise HTTPException(status_code=503, detail="Phase 2 enrichment is disabled")
        if service is None:
            raise HTTPException(
                status_code=503,
                detail=service_error or "Serper + LLM provider is not configured",
            )
        try:
            result = await service.enrich(contact)
        except Exception as error:
            raise HTTPException(
                status_code=422,
                detail=f"{type(error).__name__}: {' '.join(str(error).split())}"[:1000],
            ) from error
        return result.model_dump(mode="json")

    @router.post("/start", status_code=202)
    async def start(request: EnrichmentStartRequest) -> dict[str, Any]:
        return await schedule(request)

    @router.post("/batch", status_code=202)
    async def upload_and_start(
        file: UploadFile = File(...),
        limit: int | None = Query(default=None, ge=1, le=10_000),
    ) -> dict[str, Any]:
        if state["status"] == "running":
            raise HTTPException(status_code=409, detail="A Phase 2 enrichment job is already running")
        try:
            manifest, rows = await create_upload_batch(
                settings.uploads_directory,
                file,
                max_file_bytes=settings.max_upload_bytes,
                header_scan_rows=settings.header_scan_rows,
                linkedin_cache=linkedin_cache,
            )
            _, database, result_store = resources(manifest["batch_id"])
            await asyncio.to_thread(database.seed, rows)
            result_store.seed([row.row_id for row in rows])
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        scheduled = await schedule(
            EnrichmentStartRequest(batch_id=manifest["batch_id"], limit=limit)
        )
        return {**manifest, "enrichment": scheduled}

    @router.get("/status/{batch_id}")
    async def status(batch_id: str) -> dict[str, Any]:
        _, _, result_store = resources(batch_id)
        if not (state["status"] == "running" and state["batch_id"] == batch_id):
            result_store.recover_interrupted()
        if state["batch_id"] not in {None, batch_id}:
            return {
                "status": "idle",
                "batch_id": batch_id,
                **result_store.statistics(),
                "recent_results": [],
            }
        return {**public_state(), "statistics": result_store.statistics()}

    @router.get("/results/{batch_id}")
    async def results(batch_id: str) -> dict[str, Any]:
        _, _, result_store = resources(batch_id)
        items = result_store.list_results()
        return {"items": items, "total": len(items), "statistics": result_store.statistics()}

    @router.put("/results/{batch_id}/{row_id}")
    async def update_reviewed_result(
        batch_id: str,
        row_id: int,
        review: EnrichmentReviewUpdate,
    ) -> dict[str, Any]:
        if state["status"] == "running" and state["batch_id"] == batch_id:
            raise HTTPException(
                status_code=409,
                detail="Stop Phase 2 before editing a saved result.",
            )
        manifest, database, result_store = resources(batch_id)
        stored = result_store.get_result(row_id)
        if stored is None:
            raise HTTPException(status_code=404, detail="Contact row was not found")
        if not stored["result"]:
            raise HTTPException(
                status_code=409,
                detail="This contact does not have a completed result to review yet.",
            )
        existing = ContactEnrichmentResult.model_validate(stored["result"])
        linkedin_url = (
            canonical_linkedin_url(review.linkedin_url) if review.linkedin_url else None
        )
        status_value = "manual_review" if review.manual_review else "completed"
        updated = existing.model_copy(
            update={
                "linkedin_url": linkedin_url,
                "role": review.role,
                "background_summary": review.background_summary,
                "city": review.city,
                "state": review.state,
                "department_raw": review.department_bucket,
                "department_bucket": review.department_bucket,
                "sub_function": review.sub_function,
                "local_context": review.local_context,
                "profile_complete": not review.manual_review,
                "manual_review": review.manual_review,
                "review_reason": review.review_reason if review.manual_review else None,
                "status": status_value,
                "verification_method": "human_reviewed",
            }
        )
        result_store.save_result(row_id, updated)
        await save_report(batch_id, manifest, database, result_store)
        return {
            "item": result_store.get_result(row_id),
            "statistics": result_store.statistics(),
        }

    @router.post("/stop/{batch_id}")
    async def stop(batch_id: str) -> dict[str, Any]:
        if state["status"] != "running" or state["batch_id"] != batch_id:
            raise HTTPException(status_code=409, detail="No matching Phase 2 job is running")
        state["stop_requested"] = True
        state["stage"] = "stopping"
        return public_state()

    @router.get("/report/{batch_id}")
    async def report(batch_id: str) -> FileResponse:
        manifest, database, result_store = resources(batch_id)
        filename, path = report_path(batch_id, manifest)
        # Always rebuild from the database so a legacy workbook can never be
        # downloaded after its crawler-generated rows were requeued.
        await save_report(batch_id, manifest, database, result_store)
        return FileResponse(
            path,
            filename=filename,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    return router
