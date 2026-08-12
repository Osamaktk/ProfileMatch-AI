from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


class DailyLimitReached(RuntimeError):
    """Raised before an API call that would exceed the configured daily limit."""


def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    os.replace(temporary, path)


class DailyRequestCounter:
    """Persistent, per-key OpenRouter request counter shared across jobs."""

    def __init__(self, path: Path, api_key: str, limit: int):
        self.path = path
        self.limit = limit
        self.key_id = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"version": 1, "days": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"version": 1, "days": {}}
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "days": {}}

    def count(self) -> int:
        data = self._load()
        return int(data.get("days", {}).get(date.today().isoformat(), {}).get(self.key_id, 0))

    def remaining(self) -> int:
        return max(0, self.limit - self.count())

    def ensure_available(self) -> None:
        if self.count() >= self.limit:
            raise DailyLimitReached(
                f"OpenRouter's shared free-model daily limit ({self.limit}) has been reached. "
                "All free models use the same account quota, so model fallback cannot continue "
                "until the daily quota resets."
            )

    def record_request(self) -> int:
        self.ensure_available()
        data = self._load()
        day_key = date.today().isoformat()
        days = data.setdefault("days", {})
        day = days.setdefault(day_key, {})
        day[self.key_id] = int(day.get(self.key_id, 0)) + 1
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_json_write(self.path, data)
        return int(day[self.key_id])


class JobCheckpoint:
    """Stores completed row outputs so a run can safely resume on another day."""

    def __init__(self, cache_directory: Path, input_path: Path, output_path: Path):
        identity = f"{input_path.resolve()}|{output_path.resolve()}"
        job_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        self.path = cache_directory / "checkpoints" / f"{job_id}.json"
        self.data = self._load()
        self.data.update(
            input=str(input_path.resolve()),
            output=str(output_path.resolve()),
        )

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"version": 1, "rows": {}, "status": "new"}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {"version": 1, "rows": {}}
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "rows": {}, "status": "recovered"}

    def result_for(self, excel_row: int, fingerprint: str) -> dict[str, Any] | None:
        item = self.data.get("rows", {}).get(str(excel_row))
        if not isinstance(item, dict) or item.get("fingerprint") != fingerprint:
            return None
        result = item.get("result")
        return result if isinstance(result, dict) else None

    def save_result(
        self, excel_row: int, fingerprint: str, result: dict[str, Any]
    ) -> None:
        self.data.setdefault("rows", {})[str(excel_row)] = {
            "fingerprint": fingerprint,
            "result": result,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        self.data["status"] = "running"
        self.flush()

    def mark(self, status: str, *, next_row: int | None = None, note: str | None = None) -> None:
        self.data["status"] = status
        self.data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.data["next_row"] = next_row
        self.data["note"] = note
        self.flush()

    def flush(self) -> None:
        _atomic_json_write(self.path, self.data)
