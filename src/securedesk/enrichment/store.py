from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from securedesk.enrichment.models import ContactEnrichmentResult
from securedesk.models import utc_now_iso


CURRENT_VERIFICATION_METHODS = frozenset(
    {
        "serper_groq_v4",
        "serper_openrouter_v2",
        "serper_deepseek_v1",
        "human_reviewed",
    }
)
LEGACY_ACCEPTED_COMPLETED_METHODS = frozenset(
    {
        "serper_groq_v1",
        "serper_groq_v2",
        "serper_groq_v3",
        "serper_openrouter",
    }
)


class BatchEnrichmentStore:
    def __init__(self, path: Path, schema_path: Path):
        self.path = path
        self.schema_path = schema_path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with closing(self.connect()) as connection, connection:
            connection.executescript(self.schema_path.read_text(encoding="utf-8"))

    def seed(self, row_ids: list[int]) -> None:
        now = utc_now_iso()
        with closing(self.connect()) as connection, connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO enrichment_results (row_id, status, updated_at)
                VALUES (?, 'pending', ?)
                """,
                [(row_id, now) for row_id in row_ids],
            )

    def restore_from_database(self, source_path: Path) -> int:
        """Restore saved Phase 2 rows from an earlier upload of the exact file."""

        if not source_path.is_file() or source_path.resolve() == self.path.resolve():
            return 0
        try:
            with closing(sqlite3.connect(source_path)) as source:
                source.row_factory = sqlite3.Row
                rows = source.execute(
                    """
                    SELECT row_id, status, result_json, error, started_at,
                           completed_at, updated_at
                    FROM enrichment_results
                    WHERE status IN ('completed', 'manual_review', 'failed')
                    ORDER BY row_id
                    """
                ).fetchall()
                evidence_rows = source.execute(
                    """
                    SELECT row_id, evidence_type, source_url, source_title,
                           extracted_text, source_type, confidence,
                           published_date, created_at
                    FROM enrichment_evidence
                    ORDER BY id
                    """
                ).fetchall()
        except sqlite3.Error:
            return 0

        restored_ids: set[int] = set()
        with closing(self.connect()) as connection, connection:
            for row in rows:
                cursor = connection.execute(
                    """
                    UPDATE enrichment_results
                    SET status = ?, result_json = ?, error = ?, started_at = ?,
                        completed_at = ?, updated_at = ?
                    WHERE row_id = ? AND status = 'pending' AND result_json IS NULL
                    """,
                    (
                        row["status"],
                        row["result_json"],
                        row["error"],
                        row["started_at"],
                        row["completed_at"],
                        row["updated_at"],
                        row["row_id"],
                    ),
                )
                if cursor.rowcount:
                    restored_ids.add(int(row["row_id"]))
            connection.executemany(
                """
                INSERT INTO enrichment_evidence (
                    row_id, evidence_type, source_url, source_title,
                    extracted_text, source_type, confidence,
                    published_date, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    tuple(row)
                    for row in evidence_rows
                    if int(row["row_id"]) in restored_ids
                ],
            )
        return len(restored_ids)

    def recover_interrupted(self) -> int:
        """Return rows abandoned by a terminated server to the retry queue."""
        now = utc_now_iso()
        with closing(self.connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE enrichment_results SET status = 'pending',
                    error = 'Previous Phase 2 run was interrupted; safe to retry.',
                    updated_at = ?
                WHERE status = 'processing'
                """,
                (now,),
            )
        return int(cursor.rowcount or 0)

    def restore_requeued_current_results(self) -> int:
        """Restore current LLM results that an older migration incorrectly queued."""

        now = utc_now_iso()
        repairs: list[tuple[str, str, int]] = []
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT row_id, result_json FROM enrichment_results
                WHERE status = 'pending' AND result_json IS NOT NULL
                """
            ).fetchall()
        for row in rows:
            try:
                result = json.loads(row["result_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            status = result.get("status")
            if status not in {"completed", "manual_review"}:
                continue
            method = result.get("verification_method")
            is_current = method in CURRENT_VERIFICATION_METHODS
            is_accepted_completed = (
                method in LEGACY_ACCEPTED_COMPLETED_METHODS and status == "completed"
            )
            if not is_current and not is_accepted_completed:
                continue
            repairs.append((status, now, int(row["row_id"])))

        if repairs:
            with closing(self.connect()) as connection, connection:
                connection.executemany(
                    """
                    UPDATE enrichment_results
                    SET status = ?, error = NULL, completed_at = ?, updated_at = ?
                    WHERE row_id = ? AND status = 'pending'
                    """,
                    [(status, repaired_at, repaired_at, row_id) for status, repaired_at, row_id in repairs],
                )
        return len(repairs)

    def requeue_legacy_results(self) -> int:
        """Queue crawler-era results and old manual reviews for the focused verifier.

        The prior result remains visible/exportable until its replacement is saved.
        This makes upgrades resumable without discarding already collected work.
        """
        now = utc_now_iso()
        with closing(self.connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT row_id, status, result_json FROM enrichment_results
                WHERE result_json IS NOT NULL
                  AND status IN ('completed', 'manual_review')
                """
            ).fetchall()
            legacy_ids: list[int] = []
            for row in rows:
                try:
                    result = json.loads(row["result_json"])
                except (TypeError, json.JSONDecodeError):
                    result = {}
                method = result.get("verification_method")
                is_current = method in CURRENT_VERIFICATION_METHODS
                is_accepted_completed = (
                    method in LEGACY_ACCEPTED_COMPLETED_METHODS
                    and row["status"] == "completed"
                )
                is_accepted_incomplete_review = (
                    method == "serper_groq_v3"
                    and row["status"] == "manual_review"
                    and not result.get("role")
                    and not result.get("background_summary")
                )
                if not is_current and not is_accepted_completed and not is_accepted_incomplete_review:
                    legacy_ids.append(int(row["row_id"]))
            if legacy_ids:
                connection.executemany(
                    """
                    UPDATE enrichment_results
                    SET status = 'pending',
                        error = 'Queued for focused Serper + AI verification.',
                        started_at = NULL, completed_at = NULL, updated_at = ?
                    WHERE row_id = ?
                    """,
                    [(now, row_id) for row_id in legacy_ids],
                )
        return len(legacy_ids)

    def mark_pending(self, row_id: int, note: str | None = None) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE enrichment_results SET status = 'pending', error = ?,
                    completed_at = NULL, updated_at = ? WHERE row_id = ?
                """,
                ((note or "")[:1000] or None, utc_now_iso(), row_id),
            )

    def pending_rows(self, *, retry_failed: bool = True, limit: int | None = None) -> list[int]:
        statuses = ("pending", "failed") if retry_failed else ("pending",)
        placeholders = ",".join("?" for _ in statuses)
        sql = f"SELECT row_id FROM enrichment_results WHERE status IN ({placeholders}) ORDER BY row_id"
        params: list[Any] = list(statuses)
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with closing(self.connect()) as connection:
            rows = connection.execute(sql, params).fetchall()
        return [int(row["row_id"]) for row in rows]

    def mark_processing(self, row_id: int) -> None:
        now = utc_now_iso()
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE enrichment_results SET status = 'processing', started_at = ?,
                    error = NULL, updated_at = ? WHERE row_id = ?
                """,
                (now, now, row_id),
            )

    def save_result(self, row_id: int, result: ContactEnrichmentResult) -> None:
        now = utc_now_iso()
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE enrichment_results SET status = ?, result_json = ?, error = NULL,
                    completed_at = ?, updated_at = ? WHERE row_id = ?
                """,
                (result.status, result.model_dump_json(), now, now, row_id),
            )
            connection.execute("DELETE FROM enrichment_evidence WHERE row_id = ?", (row_id,))
            connection.executemany(
                """
                INSERT INTO enrichment_evidence (
                    row_id, evidence_type, source_url, source_title, extracted_text,
                    source_type, confidence, published_date, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row_id, item.evidence_type, item.source_url, item.source_title,
                        item.extracted_text, item.source_type, item.confidence,
                        item.published_date.isoformat() if item.published_date else None, now,
                    )
                    for item in result.evidence
                ],
            )

    def save_failure(self, row_id: int, error: str) -> None:
        now = utc_now_iso()
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE enrichment_results SET status = 'failed', error = ?,
                    completed_at = ?, updated_at = ? WHERE row_id = ?
                """,
                (error[:1000], now, now, row_id),
            )

    def list_results(self) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM enrichment_results ORDER BY row_id"
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["result"] = json.loads(item.pop("result_json")) if item.get("result_json") else None
            output.append(item)
        return output

    def get_result(self, row_id: int) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM enrichment_results WHERE row_id = ?", (row_id,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["result"] = json.loads(item.pop("result_json")) if item.get("result_json") else None
        return item

    def statistics(self) -> dict[str, int | float]:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total,
                    SUM(status = 'pending') AS pending,
                    SUM(status = 'processing') AS processing,
                    SUM(status = 'completed') AS completed,
                    SUM(status = 'manual_review') AS manual_review,
                    SUM(status = 'failed') AS failed
                FROM enrichment_results
                """
            ).fetchone()
        values = {key: int(row[key] or 0) for key in row.keys()}
        values["processed"] = values["completed"] + values["manual_review"] + values["failed"]
        values["progress_percent"] = (
            round(values["processed"] / values["total"] * 100, 1) if values["total"] else 0.0
        )
        return values
