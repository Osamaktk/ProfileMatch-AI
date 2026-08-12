# ProfileMatch AI

ProfileMatch AI is a local, two-phase contact research application for discovering LinkedIn profiles and producing evidence-backed contact classifications. It combines a FastAPI backend, a React dashboard, Serper search, configurable LLM providers, resumable processing, and professional CSV/Excel exports.

The application was designed for contact lists where accuracy and reviewability matter more than filling every field. Unclear or incomplete rows are flagged for human review instead of being silently guessed.

## Features

- Upload CSV or XLSX contact files directly from the browser.
- Preserve original columns and row order in every export.
- Display Phase 1 and Phase 2 results live while processing.
- Resume interrupted work and reuse previously found LinkedIn profiles.
- Retry earlier LinkedIn misses when a processed file is uploaded again.
- Flag incomplete contacts and unresolved or invalid results.
- Edit Phase 2 results in a dedicated review workspace before export.
- Export clickable LinkedIn/evidence URLs in CSV and styled Excel reports.
- Store runtime progress locally in SQLite; no external database is required.

## Workflow

### Phase 1: LinkedIn discovery

For each pending contact, the Serper finder runs these queries in order:

1. `site:linkedin.com/in "Name" "Organization"`
2. `Name LinkedIn`
3. `"email@example.com"`

The first valid `linkedin.com/in/` result is saved. When no profile is returned, the application waits 2–3 seconds and repeats the same strategy using alternative `Last First` name order. The original spreadsheet name is never rewritten.

Previously found profiles are restored from local history and skipped. Previous misses remain pending and are searched again. If no profile is returned, the row is flagged with `LinkedIn not found`.

### Phase 2: Contact enrichment

Phase 2 uses Serper result metadata and one configured LLM provider to produce:

- verified role/title;
- a 2–3 sentence role and background summary;
- city and state;
- relevant local context;
- department bucket and closest sub-function;
- evidence URLs;
- manual-review status and a plain-language flag reason.

The application supports Groq, DeepSeek, and OpenRouter. Configure at least one provider for Phase 2. Provider failures pause safely; completed rows remain saved and processing can continue later.

## Department taxonomy

| Department | Supported sub-functions |
| --- | --- |
| Clerk's Office | Meeting packets, Ordinance approval, Minutes archive, Public records requests |
| Zoning and Planning | Permit applications, Site plans, Review routing, GIS lookup, Planning Commission approval |
| Accounting | AP Invoice Processing, Purchasing, Contract Routing |
| Public Works | Work Orders, Capital Projects, Asset Documentation, Fleet Maintenance, Road Projects |
| Human Resources | Personnel Files, Hiring, Onboarding, Performance Reviews |
| Public Records | FOIA/Open Records, Automated Routing, Deadline Tracking, Redaction Workflow |
| IT | Help Desk Requests, Change Management, Asset Documentation, SOP Library |
| Forms | Reusable online forms, Permit Request, Vacation Request, Public Records Request, Citizen Complaint, Work Order, Purchase Request |

## Technology

- Backend: Python 3.11+, FastAPI, Pydantic, SQLite, OpenPyXL
- Frontend: React 19, TypeScript, Vite
- Search: Serper Google Search API
- AI verification: Groq, DeepSeek, or OpenRouter
- Testing: Pytest and TypeScript/Vite production builds

## Prerequisites

- Python 3.11 or newer
- Node.js 20 or newer
- A Serper API key
- At least one Phase 2 LLM key if enrichment is enabled

## Installation

Clone the repository and create a virtual environment:

```powershell
git clone <your-repository-url>
Set-Location ProfileMatch-AI

python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Configure the backend environment:

```powershell
Copy-Item .env.example .env
```

Open `.env` and add `SERPER_API_KEY`. Add at least one of `GROQ_API_KEY`, `DEEPSEEK_API_KEY`, or `OPENROUTER_API_KEY` for Phase 2.

Build the frontend:

```powershell
Set-Location frontend
npm ci
npm run build
Set-Location ..
```

Start the application:

```powershell
.\start_web.ps1
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Interactive API documentation is available at [http://127.0.0.1:8000/api/docs](http://127.0.0.1:8000/api/docs).

The startup script resolves the local `src` directory relative to itself, so renaming or moving the project folder does not break Python imports.

## Configuration

| Variable | Required | Purpose |
| --- | --- | --- |
| `SERPER_API_KEY` | Yes | Phase 1 profile discovery and Phase 2 public search evidence |
| `GROQ_API_KEY` | One LLM provider | Groq verification provider |
| `DEEPSEEK_API_KEY` | One LLM provider | Direct DeepSeek verification provider |
| `OPENROUTER_API_KEY` | One LLM provider | OpenRouter verification/fallback provider |
| `CONTACT_ENRICHMENT_ENABLED` | No | Enable or disable Phase 2; default `true` |
| `DAILY_REQUEST_LIMIT` | No | Local Phase 2 request ceiling; default `50` |
| `LLM_THROTTLE_SECONDS` | No | Delay between LLM requests; default `4` |
| `SERPER_TIMEOUT_SECONDS` | No | Phase 2 Serper timeout; default `15` |
| `SERPER_RETRIES` | No | Phase 2 Serper retry count; default `2` |
| `SERPER_RESULTS` | No | Search results retained per Phase 2 query; default `3` |
| `SECUREDESK_LOG_LEVEL` | No | Backend log level; default `INFO` |

Model names and fallback lists are documented in `.env.example`.

## Input format

Upload one `.csv` or `.xlsx` file up to 20 MB.

Required columns:

- name (`Name`, `Contact Name`, `Full Name`, or a supported alias);
- organization (`Organization`, `Company`, `Agency`, `Employer`, or a supported alias).

Recommended columns:

- title;
- work email;
- city/state or location;
- existing LinkedIn profile URL;
- organization website.

Extra columns are preserved. Existing valid LinkedIn URLs are reused, while blank or previous `not found` rows are processed again.

## Outputs and local data

Phase 1 produces a continuously saved CSV. Phase 2 produces a formatted Excel workbook with clickable sources and review fields.

The following remain local and are intentionally excluded from Git:

- `.env` API credentials;
- uploaded contact files;
- CSV/XLS/XLSX files;
- `database/uploads/` and SQLite runtime databases;
- `outputs/`, `logs/`, and `.cache/`;
- virtual environments, Node modules, and frontend build output.

Do not commit contact data or API credentials to a public repository.

## Development

Run backend tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Build the frontend:

```powershell
Set-Location frontend
npm run build
```

Run the Phase 2 CLI directly:

```powershell
.\.venv\Scripts\python.exe -m securedesk.enrichment.cli file `
  --input ".\contacts.xlsx" `
  --output ".\contact_enrichment.xlsx"
```

GitHub Actions runs backend tests and a production frontend build on every push and pull request.

## Project structure

```text
ProfileMatch-AI/
├── .github/workflows/       CI configuration
├── database/                SQLite schemas (runtime databases are ignored)
├── frontend/                React and TypeScript dashboard
├── src/contact_enrichment/  Shared search, taxonomy, cache, and LLM services
├── src/securedesk/          FastAPI app, Phase 1, Phase 2, exports, and storage
├── tests/                   Backend regression tests
├── .env.example             Safe configuration template
├── pyproject.toml           Python package metadata
└── start_web.ps1            Windows application launcher
```

## API overview

- `GET /api/health`
- `POST /api/uploads`
- `POST /api/linkedin/start`
- `GET /api/linkedin/status`
- `POST /api/linkedin/stop`
- `GET /api/reports/csv`
- `POST /api/v1/enrichment/start`
- `GET /api/v1/enrichment/status/{batch_id}`
- `GET /api/v1/enrichment/results/{batch_id}`
- `PUT /api/v1/enrichment/results/{batch_id}/{row_id}`
- `GET /api/v1/enrichment/report/{batch_id}`

## Limitations

- LinkedIn profiles must be publicly indexed by Google/Serper to be discoverable.
- Search results may be outdated or ambiguous.
- The application does not log into or scrape LinkedIn.
- Free-model quotas and availability are controlled by each AI provider.
- Automated matches and classifications should be reviewed before business use.

## License

Released under the [MIT License](LICENSE).
