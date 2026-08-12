from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from securedesk.config import Settings
from securedesk.database import Database
from securedesk.enrichment.batch import BatchEnrichmentService
from securedesk.enrichment.excel import export_enrichment_workbook
from securedesk.enrichment.llm_service import build_llm_enrichment_service
from securedesk.enrichment.models import ContactInput
from securedesk.enrichment.store import BatchEnrichmentStore
from securedesk.input import read_contact_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ProfileMatch AI Phase 2 enrichment")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="Initialize or migrate the local Phase 2 cache")
    file_parser = subparsers.add_parser("file", help="Enrich one CSV/XLSX file")
    file_parser.add_argument("--input", required=True, type=Path)
    file_parser.add_argument("--output", required=True, type=Path)
    file_parser.add_argument("--limit", type=int)

    contact_parser = subparsers.add_parser("contact", help="Test one contact")
    contact_parser.add_argument("--name", required=True)
    contact_parser.add_argument("--organization")
    contact_parser.add_argument("--title")
    contact_parser.add_argument("--email")
    contact_parser.add_argument("--city")
    contact_parser.add_argument("--state")
    contact_parser.add_argument("--linkedin-url")
    contact_parser.add_argument("--website")
    return parser


async def run_file(args: argparse.Namespace, settings: Settings) -> int:
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    headers, rows = read_contact_file(input_path, header_scan_rows=settings.header_scan_rows)
    progress_database = output_path.with_suffix(".enrichment.sqlite")
    database = Database(progress_database, settings.root / "database" / "schema.sql")
    database.initialize()
    if not database.list_contacts():
        database.seed(rows)
    result_store = BatchEnrichmentStore(
        progress_database, settings.root / "database" / "batch_enrichment_schema.sql"
    )
    result_store.initialize()
    contacts = database.list_contacts()
    result_store.seed([int(row["row_id"]) for row in contacts])
    service = build_llm_enrichment_service(settings)
    batch = BatchEnrichmentService(service)

    async def progress(event: dict) -> None:
        print(
            f"[{event.get('position', '-')}] row={event.get('row_id')} "
            f"name={event.get('name')} stage={event.get('stage')}"
        )
        if event["event"] in {"completed", "failed"}:
            export_enrichment_workbook(
                contacts, result_store.list_results(), headers, output_path
            )

    await batch.process_contacts(
        contacts,
        result_store,
        limit=args.limit,
        progress=progress,
    )
    export_enrichment_workbook(contacts, result_store.list_results(), headers, output_path)
    print(json.dumps(result_store.statistics(), indent=2))
    print(f"Saved: {output_path}")
    return 0


async def run_contact(args: argparse.Namespace, settings: Settings) -> int:
    service = build_llm_enrichment_service(settings)
    result = await service.enrich(
        ContactInput(
            name=args.name,
            organization=args.organization,
            existing_job_title=args.title,
            email=args.email,
            existing_city=args.city,
            existing_state=args.state,
            linkedin_url=args.linkedin_url,
            organization_website=args.website,
        )
    )
    print(result.model_dump_json(indent=2))
    return 0


def main() -> int:
    args = build_parser().parse_args()
    settings = Settings.load()
    if args.command == "init-db":
        build_llm_enrichment_service(settings)
        print(f"LLM enrichment cache ready: {settings.root / '.cache'}")
        return 0
    if args.command == "file":
        return asyncio.run(run_file(args, settings))
    return asyncio.run(run_contact(args, settings))


if __name__ == "__main__":
    raise SystemExit(main())
