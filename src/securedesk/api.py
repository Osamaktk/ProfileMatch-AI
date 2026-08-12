from __future__ import annotations

import asyncio
import random
import re
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from securedesk.config import Settings
from securedesk.database import Database
from securedesk.enrichment.api import create_enrichment_router
from securedesk.export import export_results_csv
from securedesk.logging_config import configure_logging
from securedesk.linkedin_cache import LinkedInResultCache
from securedesk.models import utc_now_iso
from securedesk.phase1_verifier import (
    Phase1CandidateVerifier,
    first_exact_name_candidate,
)
from securedesk.serper import (
    LinkedInCandidate,
    LinkedInLookup,
    SerperLinkedInFinder,
    SerperRequestError,
)
from securedesk.uploads import (
    batch_directory,
    create_upload_batch,
    get_upload_batch,
    save_upload_manifest,
)


class LookupStartRequest(BaseModel):
    batch_id: str = Field(min_length=32, max_length=32)
    retry_errors: bool = True


def create_app(
    settings: Settings | None = None,
    *,
    enrichment_service: Any | None = None,
    phase1_verifier: Any | None = None,
    phase1_sleep: Callable[[float], Awaitable[None]] | None = None,
) -> FastAPI:
    settings = settings or Settings.load()
    configure_logging(settings.root)
    schema_path = settings.root / "database" / "schema.sql"
    finder = SerperLinkedInFinder(settings)
    candidate_verifier = phase1_verifier or Phase1CandidateVerifier(settings)
    retry_sleep = phase1_sleep or asyncio.sleep
    linkedin_cache = LinkedInResultCache(settings.linkedin_cache_path)
    linkedin_cache.initialize()
    linkedin_cache.backfill_uploads(settings.uploads_directory)
    lookup_lock = asyncio.Lock()
    lookup_state: dict[str, Any] = {
        "status": "idle",
        "batch_id": None,
        "started_at": None,
        "completed_at": None,
        "total": 0,
        "processed": 0,
        "found": 0,
        "not_found": 0,
        "errors": 0,
        "current_row": None,
        "current_name": None,
        "current_organization": None,
        "stage": None,
        "recent_results": [],
        "last_error": None,
        "cancel_requested": False,
        "task": None,
    }

    app = FastAPI(
        title="ProfileMatch AI API",
        version="0.2.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.state.linkedin_finder = finder
    app.state.phase1_candidate_verifier = candidate_verifier

    def resources(batch_id: str) -> tuple[dict[str, Any], Database]:
        try:
            manifest = get_upload_batch(settings.uploads_directory, batch_id)
        except (ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        database = Database(
            batch_directory(settings.uploads_directory, batch_id) / "contacts.db",
            schema_path,
        )
        database.initialize()
        return manifest, database

    def result_file(
        batch_id: str, manifest: dict[str, Any]
    ) -> tuple[str, Any]:
        source_stem = re.sub(
            r"[^A-Za-z0-9._ -]+", "_", str(manifest["source_file"])
        ).rsplit(".", 1)[0]
        suffix = "_linkedin_results"
        if source_stem.casefold().endswith(suffix):
            source_stem = source_stem[: -len(suffix)]
        filename = f"{source_stem}{suffix}.csv"
        return filename, batch_directory(settings.uploads_directory, batch_id) / filename

    async def save_current_csv(
        batch_id: str, manifest: dict[str, Any], database: Database
    ) -> None:
        _, path = result_file(batch_id, manifest)
        await asyncio.to_thread(
            export_results_csv,
            database.list_contacts(),
            list(manifest["headers"]),
            path,
        )

    def public_state() -> dict[str, Any]:
        return {
            key: value
            for key, value in lookup_state.items()
            if key not in {"task", "cancel_requested"}
        }

    def add_live_result(saved: dict[str, Any]) -> None:
        item = {
            "row_id": saved["row_id"],
            "name": saved.get("name"),
            "organization": saved.get("organization"),
            "status": saved["lookup_status"],
            "linkedin_url": saved.get("linkedin_url"),
            "confidence": saved.get("match_confidence") or 0,
            "note": saved.get("validation_note"),
            "flagged": bool(saved.get("flagged")),
            "flag_reason": saved.get("flag_reason"),
        }
        lookup_state["recent_results"] = [
            item,
            *lookup_state["recent_results"],
        ][:20]

    def combined_candidates(*lookups: LinkedInLookup) -> list[LinkedInCandidate]:
        by_url: dict[str, LinkedInCandidate] = {}
        for lookup in lookups:
            for candidate in lookup.candidates:
                current = by_url.get(candidate.url)
                if current is None or candidate.score > current.score:
                    by_url[candidate.url] = candidate
        return sorted(
            by_url.values(), key=lambda candidate: candidate.score, reverse=True
        )[:10]

    async def lookup_with_retry_and_verification(
        contact: dict[str, Any],
    ) -> LinkedInLookup:
        first = await app.state.linkedin_finder.lookup_linkedin(
            {**contact, "_search_attempt": 1}
        )
        if first.status == "matched" and first.url:
            return first

        lookup_state["stage"] = "waiting_to_retry"
        await retry_sleep(random.uniform(2.0, 3.0))
        if lookup_state["cancel_requested"]:
            return first

        lookup_state["stage"] = "searching_linkedin_retry"
        second = await app.state.linkedin_finder.lookup_linkedin(
            {**contact, "_search_attempt": 2}
        )
        if second.status == "matched" and second.url:
            return second

        candidates = combined_candidates(first, second)
        verifier = app.state.phase1_candidate_verifier
        if not candidates or not getattr(verifier, "configured", False):
            exact_name_match = first_exact_name_candidate(contact, candidates)
            if exact_name_match.candidate:
                candidate = exact_name_match.candidate
                return LinkedInLookup(
                    status="matched",
                    url=candidate.url,
                    confidence=exact_name_match.confidence,
                    title=candidate.title or None,
                    snippet=candidate.snippet or None,
                    candidates=candidates,
                    note=exact_name_match.note,
                )
            note = second.note
            if candidates:
                note = f"{note} LLM candidate verification is not configured."
            return LinkedInLookup(
                status="not_found",
                candidates=candidates,
                note=note,
            )

        lookup_state["stage"] = "verifying_candidates_with_llm"
        verification = await asyncio.to_thread(verifier.verify, contact, candidates[:3])
        if verification.candidate:
            candidate = verification.candidate
            return LinkedInLookup(
                status="matched",
                url=candidate.url,
                confidence=verification.confidence,
                title=candidate.title or None,
                snippet=candidate.snippet or None,
                candidates=candidates,
                note=verification.note,
            )
        exact_name_match = first_exact_name_candidate(contact, candidates)
        if exact_name_match.candidate:
            candidate = exact_name_match.candidate
            return LinkedInLookup(
                status="matched",
                url=candidate.url,
                confidence=exact_name_match.confidence,
                title=candidate.title or None,
                snippet=candidate.snippet or None,
                candidates=candidates,
                note=(
                    f"{exact_name_match.note} The LLM did not confirm a stronger "
                    "organization-based match."
                ),
            )
        return LinkedInLookup(
            status="not_found",
            candidates=candidates,
            note=f"{second.note} {verification.note}".strip(),
        )

    async def execute_lookup(request: LookupStartRequest) -> None:
        try:
            manifest, database = resources(request.batch_id)
            contacts = database.contacts_to_process(include_errors=request.retry_errors)
            lookup_state["total"] = len(contacts)
            for contact in contacts:
                if lookup_state["cancel_requested"]:
                    lookup_state["status"] = "stopped"
                    break
                lookup_state.update(
                    current_row=contact["row_id"],
                    current_name=contact.get("name"),
                    current_organization=contact.get("organization"),
                    stage="searching_linkedin",
                )
                try:
                    if not contact.get("name"):
                        saved = database.save_lookup(
                            contact["row_id"],
                            status="not_found",
                            linkedin_url=None,
                            note="User LinkedIn not found: the contact name is missing from the uploaded row.",
                            flagged=True,
                            flag_reason="LinkedIn not found",
                        )
                    else:
                        lookup = await lookup_with_retry_and_verification(contact)
                        if lookup.status == "matched" and lookup.url:
                            saved = database.save_lookup(
                                contact["row_id"],
                                status="found",
                                linkedin_url=lookup.url,
                                note=lookup.note,
                                confidence=lookup.confidence,
                            )
                            linkedin_cache.save(
                                contact,
                                lookup.url,
                                confidence=lookup.confidence,
                                note=lookup.note,
                            )
                        else:
                            saved = database.save_lookup(
                                contact["row_id"],
                                status="not_found",
                                linkedin_url=None,
                                note=f"User LinkedIn not found. {lookup.note}",
                                flagged=True,
                                flag_reason="LinkedIn not found",
                            )
                    if saved["lookup_status"] == "found":
                        lookup_state["found"] += 1
                    else:
                        lookup_state["not_found"] += 1
                    add_live_result(saved)
                except SerperRequestError as error:
                    message = " ".join(str(error).split())[:800]
                    saved = database.save_lookup(
                        contact["row_id"],
                        status="error",
                        linkedin_url=None,
                        note=message,
                    )
                    lookup_state["errors"] += 1
                    lookup_state["last_error"] = message
                    add_live_result(saved)
                except Exception as error:
                    message = f"Unexpected lookup error: {' '.join(str(error).split())[:700]}"
                    saved = database.save_lookup(
                        contact["row_id"],
                        status="error",
                        linkedin_url=None,
                        note=message,
                    )
                    lookup_state["errors"] += 1
                    lookup_state["last_error"] = message
                    add_live_result(saved)
                finally:
                    lookup_state["processed"] += 1
                    await save_current_csv(request.batch_id, manifest, database)

            if lookup_state["status"] == "running":
                lookup_state["status"] = "complete"
        except Exception as error:
            lookup_state.update(
                status="failed",
                last_error=" ".join(str(error).split())[:800],
            )
        finally:
            lookup_state.update(
                completed_at=utc_now_iso(),
                current_row=None,
                current_name=None,
                current_organization=None,
                stage=None,
            )

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        verification_providers = [
            name
            for name, configured in (
                ("Groq", settings.groq_api_key),
                ("DeepSeek", settings.deepseek_api_key),
                ("OpenRouter", settings.openrouter_api_key),
            )
            if configured
        ]
        return {
            "status": "ok",
            "phase": 1,
            "workflow": "linkedin_profile_discovery",
            "serper_configured": app.state.linkedin_finder.configured,
            "openrouter_configured": bool(settings.openrouter_api_key),
            "groq_configured": bool(settings.groq_api_key),
            "deepseek_configured": bool(settings.deepseek_api_key),
            "verification_provider": " → ".join(verification_providers),
            "phase2_enabled": settings.contact_enrichment_enabled,
        }

    @app.post("/api/uploads")
    async def upload_contacts(file: UploadFile = File(...)) -> dict[str, Any]:
        if lookup_state["status"] == "running":
            raise HTTPException(status_code=409, detail="Stop the current lookup before uploading a new file")
        try:
            manifest, rows = await create_upload_batch(
                settings.uploads_directory,
                file,
                max_file_bytes=settings.max_upload_bytes,
                header_scan_rows=settings.header_scan_rows,
                linkedin_cache=linkedin_cache,
                reuse_linkedin_results=True,
            )
            _, database = resources(manifest["batch_id"])
            await asyncio.to_thread(database.seed, rows)
            statistics = database.statistics()
            manifest.update(
                phase1_complete=(
                    statistics["pending"] == 0 and statistics["errors"] == 0
                ),
                phase1_reused_file_count=0,
                phase1_reused_batch_id=None,
            )
            save_upload_manifest(settings.uploads_directory, manifest)
            await save_current_csv(manifest["batch_id"], manifest, database)
            return {**manifest, "statistics": statistics}
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/contacts")
    async def contacts(
        batch_id: str = Query(..., min_length=32, max_length=32),
    ) -> dict[str, Any]:
        _, database = resources(batch_id)
        rows = database.list_contacts()
        return {"items": rows, "total": len(rows)}

    @app.get("/api/statistics")
    async def statistics(
        batch_id: str = Query(..., min_length=32, max_length=32),
    ) -> dict[str, Any]:
        _, database = resources(batch_id)
        return database.statistics()

    @app.post("/api/linkedin/start", status_code=202)
    async def start_lookup(request: LookupStartRequest) -> dict[str, Any]:
        if not app.state.linkedin_finder.configured:
            raise HTTPException(status_code=503, detail="Serper is not configured on the server")
        resources(request.batch_id)
        async with lookup_lock:
            if lookup_state["status"] == "running":
                raise HTTPException(status_code=409, detail="A LinkedIn lookup is already running")
            lookup_state.update(
                status="running",
                batch_id=request.batch_id,
                started_at=utc_now_iso(),
                completed_at=None,
                total=0,
                processed=0,
                found=0,
                not_found=0,
                errors=0,
                current_row=None,
                current_name=None,
                current_organization=None,
                stage="queued",
                recent_results=[],
                last_error=None,
                cancel_requested=False,
            )
            lookup_state["task"] = asyncio.create_task(execute_lookup(request))
        return public_state()

    @app.get("/api/linkedin/status")
    async def lookup_status(
        batch_id: str = Query(..., min_length=32, max_length=32),
    ) -> dict[str, Any]:
        resources(batch_id)
        if lookup_state["batch_id"] not in {None, batch_id}:
            return {
                **public_state(),
                "status": "idle",
                "batch_id": batch_id,
                "total": 0,
                "processed": 0,
                "recent_results": [],
            }
        return public_state()

    @app.post("/api/linkedin/stop")
    async def stop_lookup(
        batch_id: str = Query(..., min_length=32, max_length=32),
    ) -> dict[str, Any]:
        if lookup_state["status"] != "running" or lookup_state["batch_id"] != batch_id:
            raise HTTPException(status_code=409, detail="No LinkedIn lookup is running")
        lookup_state["cancel_requested"] = True
        return public_state()

    @app.post("/api/export")
    async def generate_export(
        batch_id: str = Query(..., min_length=32, max_length=32),
    ) -> dict[str, str]:
        manifest, database = resources(batch_id)
        filename, _ = result_file(batch_id, manifest)
        await save_current_csv(batch_id, manifest, database)
        return {"filename": filename}

    @app.get("/api/reports/csv")
    async def download_csv(
        batch_id: str = Query(..., min_length=32, max_length=32),
    ) -> FileResponse:
        manifest, _ = resources(batch_id)
        filename, path = result_file(batch_id, manifest)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="The current result CSV is not available")
        return FileResponse(path, filename=filename, media_type="text/csv")

    app.include_router(
        create_enrichment_router(
            settings,
            schema_path,
            service=enrichment_service,
        )
    )

    frontend_dist = settings.root / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
    return app


app = create_app()
