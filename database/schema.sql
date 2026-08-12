CREATE TABLE IF NOT EXISTS contacts (
    row_id INTEGER PRIMARY KEY,
    source_row INTEGER NOT NULL,
    name TEXT,
    organization TEXT,
    title TEXT,
    email TEXT,
    location TEXT,
    city TEXT,
    state TEXT,
    linkedin_title TEXT,
    linkedin_snippet TEXT,
    organization_website TEXT,
    original_data_json TEXT NOT NULL,
    linkedin_url TEXT,
    lookup_status TEXT NOT NULL DEFAULT 'pending',
    validation_note TEXT,
    match_confidence INTEGER NOT NULL DEFAULT 0,
    flagged INTEGER NOT NULL DEFAULT 0,
    flag_reason TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_contacts_lookup_status ON contacts(lookup_status);
