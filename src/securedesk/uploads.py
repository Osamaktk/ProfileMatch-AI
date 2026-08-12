from __future__ import annotations

import json
import hashlib
import re
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from securedesk.input import detected_columns, read_contact_file
from securedesk.linkedin_cache import LinkedInResultCache
from securedesk.models import ContactRow, utc_now_iso


_BATCH_ID = re.compile(r"^[0-9a-f]{32}$")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


def batch_directory(root: Path, batch_id: str) -> Path:
    if not _BATCH_ID.fullmatch(batch_id):
        raise ValueError("Invalid upload batch ID")
    return root / batch_id


def _safe_filename(filename: str | None) -> str:
    original = Path(filename or "contacts.csv").name
    cleaned = _SAFE_NAME.sub("_", original).strip(" .") or "contacts.csv"
    if Path(cleaned).suffix.casefold() not in {".csv", ".xlsx"}:
        raise ValueError("Only .csv and .xlsx files are supported")
    return cleaned


async def create_upload_batch(
    root: Path,
    upload: UploadFile,
    *,
    max_file_bytes: int,
    header_scan_rows: int,
    linkedin_cache: LinkedInResultCache | None = None,
    reuse_linkedin_results: bool = True,
) -> tuple[dict[str, Any], list[ContactRow]]:
    batch_id = uuid.uuid4().hex
    directory = batch_directory(root, batch_id)
    input_directory = directory / "input"
    input_directory.mkdir(parents=True, exist_ok=False)
    filename = _safe_filename(upload.filename)
    path = input_directory / filename
    size = 0
    digest = hashlib.sha256()
    try:
        with path.open("wb") as handle:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > max_file_bytes:
                    raise ValueError("The uploaded file exceeds the 20 MB limit")
                handle.write(chunk)
                digest.update(chunk)
        await upload.close()
        if size == 0:
            raise ValueError("The uploaded file is empty")
        if path.suffix.casefold() == ".xlsx" and not zipfile.is_zipfile(path):
            raise ValueError("The uploaded file is not a valid .xlsx workbook")

        headers, rows = read_contact_file(path, header_scan_rows=header_scan_rows)
        file_found_count = sum(row.initial_lookup_status == "found" for row in rows)
        cached_found_count = 0
        if reuse_linkedin_results and linkedin_cache:
            rows, cached_found_count = linkedin_cache.restore_rows(rows)
        elif not reuse_linkedin_results:
            rows = [
                row.model_copy(
                    update={
                        "initial_linkedin_url": None,
                        "initial_lookup_status": "pending",
                        "initial_validation_note": (
                            "Scheduled for a fresh Phase 1 LinkedIn check."
                        ),
                        "initial_match_confidence": 0,
                    }
                )
                for row in rows
            ]
            file_found_count = 0
        columns = detected_columns(headers)
        manifest = {
            "batch_id": batch_id,
            "created_at": utc_now_iso(),
            "source_file": upload.filename or filename,
            "stored_name": filename,
            "size_bytes": size,
            "content_sha256": digest.hexdigest(),
            "contact_count": len(rows),
            "headers": headers,
            "name_column": columns["name"],
            "organization_column": columns["organization"],
            "title_column": columns.get("title"),
            "email_column": columns.get("email"),
            "location_column": columns.get("location"),
            "linkedin_column": columns.get("linkedin_url"),
            "file_found_count": file_found_count,
            "cached_found_count": cached_found_count,
            "resumed_found_count": file_found_count + cached_found_count,
            "phase1_complete": False,
            "phase1_reused_file_count": 0,
            "phase1_reused_batch_id": None,
        }
        save_upload_manifest(root, manifest)
        return manifest, rows
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def get_upload_batch(root: Path, batch_id: str) -> dict[str, Any]:
    directory = batch_directory(root, batch_id)
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("Upload batch was not found")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def save_upload_manifest(root: Path, manifest: dict[str, Any]) -> None:
    directory = batch_directory(root, str(manifest["batch_id"]))
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
