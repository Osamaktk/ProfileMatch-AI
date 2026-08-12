CREATE TABLE IF NOT EXISTS enrichment_results (
    row_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    result_json TEXT,
    error TEXT,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_enrichment_results_status
ON enrichment_results(status);

CREATE TABLE IF NOT EXISTS enrichment_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    row_id INTEGER NOT NULL,
    evidence_type TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_title TEXT,
    extracted_text TEXT,
    source_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    published_date TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_enrichment_evidence_row
ON enrichment_evidence(row_id);
