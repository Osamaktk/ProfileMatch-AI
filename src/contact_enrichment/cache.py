from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ApiCache:
    """Small SQLite cache shared by Serper and OpenRouter calls."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with closing(self.connect()) as connection, connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS api_cache (
                    namespace TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(namespace, cache_key)
                );
                """
            )

    def get(self, namespace: str, cache_key: str) -> Any | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT response_json FROM api_cache WHERE namespace = ? AND cache_key = ?",
                (namespace, cache_key),
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["response_json"])
        except (TypeError, json.JSONDecodeError):
            return None

    def set(self, namespace: str, cache_key: str, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        now = datetime.now(timezone.utc).isoformat()
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO api_cache(namespace, cache_key, response_json, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(namespace, cache_key) DO UPDATE SET
                    response_json = excluded.response_json,
                    created_at = excluded.created_at
                """,
                (namespace, cache_key, payload, now),
            )

    def count(self, namespace: str | None = None) -> int:
        with closing(self.connect()) as connection:
            if namespace:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM api_cache WHERE namespace = ?",
                    (namespace,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM api_cache"
                ).fetchone()
        return int(row["count"] or 0)
