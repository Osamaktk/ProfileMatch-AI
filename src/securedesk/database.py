from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from securedesk.models import ContactRow, utc_now_iso


class Database:
    def __init__(self, path: Path, schema_path: Path):
        self.path = path
        self.schema_path = schema_path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with closing(self.connect()) as connection, connection:
            connection.executescript(self.schema_path.read_text(encoding="utf-8"))
            existing = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(contacts)").fetchall()
            }
            for column in (
                "title",
                "email",
                "location",
                "city",
                "state",
                "linkedin_title",
                "linkedin_snippet",
                "organization_website",
                "flag_reason",
            ):
                if column not in existing:
                    connection.execute(f"ALTER TABLE contacts ADD COLUMN {column} TEXT")
            if "flagged" not in existing:
                connection.execute(
                    "ALTER TABLE contacts ADD COLUMN flagged INTEGER NOT NULL DEFAULT 0"
                )

    def seed(self, rows: list[ContactRow]) -> None:
        now = utc_now_iso()
        with closing(self.connect()) as connection, connection:
            connection.executemany(
                """
                INSERT INTO contacts (
                    row_id, source_row, name, organization, title, email, location,
                    city, state, linkedin_title, linkedin_snippet, organization_website,
                    original_data_json, linkedin_url, lookup_status, validation_note,
                    match_confidence, flagged, flag_reason, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row.row_id,
                        row.source_row,
                        row.name,
                        row.organization,
                        row.title,
                        row.email,
                        row.location,
                        row.city,
                        row.state,
                        row.linkedin_title,
                        row.linkedin_snippet,
                        row.organization_website,
                        json.dumps(row.original_data, ensure_ascii=False, default=str),
                        row.initial_linkedin_url,
                        row.initial_lookup_status,
                        row.initial_validation_note,
                        row.initial_match_confidence,
                        int(row.initial_flagged),
                        row.initial_flag_reason,
                        now,
                    )
                    for row in rows
                ],
            )

    def list_contacts(self) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection, connection:
            rows = connection.execute(
                "SELECT * FROM contacts ORDER BY row_id"
            ).fetchall()
        return [self._public_row(row) for row in rows]

    def contacts_to_process(self, *, include_errors: bool = True) -> list[dict[str, Any]]:
        statuses = ("pending", "error") if include_errors else ("pending",)
        placeholders = ",".join("?" for _ in statuses)
        with closing(self.connect()) as connection, connection:
            rows = connection.execute(
                f"SELECT * FROM contacts WHERE lookup_status IN ({placeholders}) ORDER BY row_id",
                statuses,
            ).fetchall()
        return [self._public_row(row) for row in rows]

    def save_lookup(
        self,
        row_id: int,
        *,
        status: str,
        linkedin_url: str | None,
        note: str,
        confidence: int = 0,
        flagged: bool | None = None,
        flag_reason: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"found", "not_found", "error"}:
            raise ValueError("Invalid lookup status")
        with closing(self.connect()) as connection, connection:
            if flagged is True and flag_reason:
                existing = connection.execute(
                    "SELECT flag_reason FROM contacts WHERE row_id = ?", (row_id,)
                ).fetchone()
                flag_reason = _join_flag_reasons(
                    existing["flag_reason"] if existing else None,
                    flag_reason,
                )
            connection.execute(
                """
                UPDATE contacts SET
                    linkedin_url = ?, lookup_status = ?, validation_note = ?,
                    match_confidence = ?,
                    flagged = COALESCE(?, flagged),
                    flag_reason = CASE WHEN ? IS NULL THEN flag_reason ELSE ? END,
                    updated_at = ?
                WHERE row_id = ?
                """,
                (
                    linkedin_url,
                    status,
                    note[:1000],
                    confidence,
                    None if flagged is None else int(flagged),
                    flag_reason,
                    flag_reason[:1000] if flag_reason else flag_reason,
                    utc_now_iso(),
                    row_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM contacts WHERE row_id = ?", (row_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Contact row {row_id} was not found")
        return self._public_row(row)

    def statistics(self) -> dict[str, int | float]:
        with closing(self.connect()) as connection, connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN lookup_status = 'pending' THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN lookup_status = 'found' THEN 1 ELSE 0 END) AS found,
                    SUM(CASE WHEN lookup_status = 'not_found' THEN 1 ELSE 0 END) AS not_found,
                    SUM(CASE WHEN lookup_status = 'error' THEN 1 ELSE 0 END) AS errors,
                    SUM(CASE WHEN flagged = 1 THEN 1 ELSE 0 END) AS flagged
                FROM contacts
                """
            ).fetchone()
        total = int(row["total"] or 0)
        pending = int(row["pending"] or 0)
        processed = total - pending
        return {
            "total": total,
            "processed": processed,
            "pending": pending,
            "found": int(row["found"] or 0),
            "not_found": int(row["not_found"] or 0),
            "errors": int(row["errors"] or 0),
            "flagged": int(row["flagged"] or 0),
            "progress_percent": round(processed / total * 100, 1) if total else 0.0,
        }

    @staticmethod
    def _public_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["flagged"] = bool(result.get("flagged"))
        result["original_data"] = json.loads(result.pop("original_data_json") or "{}")
        return result


def _join_flag_reasons(*reasons: str | None) -> str | None:
    unique: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        cleaned = " ".join(str(reason or "").split()).strip(" ;")
        if not cleaned or cleaned.casefold() in seen:
            continue
        seen.add(cleaned.casefold())
        unique.append(cleaned)
    return "; ".join(unique) or None
