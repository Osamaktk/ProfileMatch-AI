from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from securedesk.models import ContactRow, utc_now_iso


def _normalized(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _canonical_linkedin_url(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = urlsplit(text)
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    path = re.sub(r"/{2,}", "/", parsed.path).rstrip("/")
    if host != "linkedin.com" or not path.casefold().startswith("/in/"):
        return None
    return urlunsplit(("https", "www.linkedin.com", path, "", ""))


def _identity_keys(contact: Any) -> list[str]:
    def field(name: str) -> Any:
        if isinstance(contact, dict):
            return contact.get(name)
        return getattr(contact, name, None)

    name = _normalized(field("name"))
    organization = _normalized(field("organization"))
    title = _normalized(field("title") or field("existing_job_title"))
    email = str(field("email") or "").strip().casefold()
    keys: list[str] = []
    if email and "@" in email:
        keys.append(f"email:{email}")
    if name and organization:
        keys.append(f"person_org:{name}|{organization}")
    if name and title:
        keys.append(f"person_title:{name}|{title}")
    return keys


class LinkedInResultCache:
    """Persistent positive-result cache shared by every uploaded batch."""

    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with closing(self.connect()) as connection, connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS linkedin_result_cache (
                    identity_key TEXT PRIMARY KEY,
                    linkedin_url TEXT NOT NULL,
                    confidence INTEGER NOT NULL,
                    validation_note TEXT,
                    contact_name TEXT,
                    organization TEXT,
                    title TEXT,
                    email TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_linkedin_cache_url
                ON linkedin_result_cache(linkedin_url);
                """
            )

    def find(self, contact: Any) -> dict[str, Any] | None:
        keys = _identity_keys(contact)
        if not keys:
            return None
        with closing(self.connect()) as connection:
            for key in keys:
                row = connection.execute(
                    "SELECT * FROM linkedin_result_cache WHERE identity_key = ?",
                    (key,),
                ).fetchone()
                if row:
                    return dict(row)
        return None

    def save(
        self,
        contact: Any,
        linkedin_url: Any,
        *,
        confidence: int = 0,
        note: str | None = None,
    ) -> bool:
        url = _canonical_linkedin_url(linkedin_url)
        keys = _identity_keys(contact)
        if not url or not keys:
            return False

        def field(name: str) -> Any:
            if isinstance(contact, dict):
                return contact.get(name)
            return getattr(contact, name, None)

        values = (
            url,
            max(0, min(100, int(confidence or 0))),
            str(note or "")[:1000] or None,
            field("name"),
            field("organization"),
            field("title") or field("existing_job_title"),
            field("email"),
            utc_now_iso(),
        )
        with closing(self.connect()) as connection, connection:
            connection.executemany(
                """
                INSERT INTO linkedin_result_cache (
                    identity_key, linkedin_url, confidence, validation_note,
                    contact_name, organization, title, email, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(identity_key) DO UPDATE SET
                    linkedin_url = excluded.linkedin_url,
                    confidence = MAX(linkedin_result_cache.confidence, excluded.confidence),
                    validation_note = excluded.validation_note,
                    contact_name = excluded.contact_name,
                    organization = excluded.organization,
                    title = excluded.title,
                    email = excluded.email,
                    updated_at = excluded.updated_at
                """,
                [(key, *values) for key in keys],
            )
        return True

    def restore_rows(self, rows: Iterable[ContactRow]) -> tuple[list[ContactRow], int]:
        restored: list[ContactRow] = []
        reused = 0
        for row in rows:
            if row.initial_lookup_status == "found" and row.initial_linkedin_url:
                self.save(
                    row,
                    row.initial_linkedin_url,
                    confidence=row.initial_match_confidence,
                    note=row.initial_validation_note,
                )
                restored.append(row)
                continue
            cached = self.find(row)
            if not cached:
                restored.append(row)
                continue
            reused += 1
            restored.append(
                row.model_copy(
                    update={
                        "initial_linkedin_url": cached["linkedin_url"],
                        "initial_lookup_status": "found",
                        "initial_validation_note": (
                            "Reused a previously validated LinkedIn result; "
                            "no Serper request was made."
                        ),
                        "initial_match_confidence": int(cached["confidence"] or 0),
                    }
                )
            )
        return restored, reused

    def restore_database(self, database: Any) -> int:
        """Apply shared matches to an existing upload created before cache support."""
        restored = 0
        for contact in database.list_contacts():
            if contact.get("lookup_status") == "found" and contact.get("linkedin_url"):
                continue
            cached = self.find(contact)
            if not cached:
                continue
            database.save_lookup(
                int(contact["row_id"]),
                status="found",
                linkedin_url=str(cached["linkedin_url"]),
                note="Reused a previously validated LinkedIn result; no Serper request was made.",
                confidence=int(cached["confidence"] or 0),
            )
            restored += 1
        return restored

    def backfill_uploads(self, uploads_root: Path) -> int:
        """Import successful results produced before the shared cache existed."""
        imported = 0
        if not uploads_root.is_dir():
            return imported
        for directory in uploads_root.iterdir():
            if not directory.is_dir() or not re.fullmatch(r"[0-9a-f]{32}", directory.name):
                continue
            database_path = directory / "contacts.db"
            if not database_path.is_file():
                continue
            try:
                with closing(sqlite3.connect(database_path)) as connection:
                    connection.row_factory = sqlite3.Row
                    rows = connection.execute(
                        """
                        SELECT name, organization, title, email, linkedin_url,
                               match_confidence, validation_note
                        FROM contacts
                        WHERE lookup_status = 'found' AND linkedin_url IS NOT NULL
                        """
                    ).fetchall()
                for row in rows:
                    imported += int(
                        self.save(
                            dict(row),
                            row["linkedin_url"],
                            confidence=int(row["match_confidence"] or 0),
                            note=row["validation_note"],
                        )
                    )
            except sqlite3.Error:
                continue
        return imported
