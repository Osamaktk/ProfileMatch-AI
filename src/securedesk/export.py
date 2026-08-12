from __future__ import annotations

import csv
import uuid
from pathlib import Path
from typing import Any


RESULT_COLUMNS = [
    "LinkedIn Profile URL",
    "LinkedIn Lookup Status",
    "LinkedIn Validation Note",
    "LinkedIn Match Confidence",
    "Flag",
    "Flag Reason",
]


def safe_csv_value(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def export_results_csv(
    rows: list[dict[str, Any]],
    original_headers: list[str],
    path: Path,
) -> Path:
    headers = [*original_headers]
    for result_header in RESULT_COLUMNS:
        if result_header not in headers:
            headers.append(result_header)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                output = dict(row.get("original_data") or {})
                status = row.get("lookup_status")
                output.update(
                    {
                        "LinkedIn Profile URL": (
                            row.get("linkedin_url")
                            if status == "found"
                            else "User LinkedIn not found"
                            if status == "not_found"
                            else "Lookup error - retry required"
                            if status == "error"
                            else "Pending"
                        ),
                        "LinkedIn Lookup Status": {
                            "found": "Found and validated",
                            "not_found": "User LinkedIn not found",
                            "error": "Lookup error",
                            "pending": "Pending",
                        }.get(str(status), "Pending"),
                        "LinkedIn Validation Note": row.get("validation_note") or "",
                        "LinkedIn Match Confidence": row.get("match_confidence") or 0,
                        "Flag": "YES" if row.get("flagged") else "NO",
                        "Flag Reason": row.get("flag_reason") or "",
                    }
                )
                writer.writerow(
                    {header: safe_csv_value(output.get(header, "")) for header in headers}
                )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
