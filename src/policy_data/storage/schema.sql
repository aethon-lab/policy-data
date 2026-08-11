PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_versions (
    component TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version > 0)
);
INSERT OR IGNORE INTO schema_versions(component, version) VALUES ('canonical', 1);

CREATE TABLE IF NOT EXISTS legislatures (
    number INTEGER PRIMARY KEY CHECK (number > 0),
    roman_numeral TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS chambers (
    code TEXT PRIMARY KEY CHECK (code IN ('camera', 'senato')),
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS people (
    person_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_authorities (
    authority_id TEXT PRIMARY KEY,
    chamber_code TEXT REFERENCES chambers(code),
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_identities (
    identity_id TEXT PRIMARY KEY,
    authority_id TEXT NOT NULL REFERENCES source_authorities(authority_id),
    source_person_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    canonical_person_id TEXT NOT NULL REFERENCES people(person_id),
    same_as_uri TEXT,
    UNIQUE(authority_id, source_person_id)
);

CREATE TABLE IF NOT EXISTS person_crosswalks (
    crosswalk_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    status TEXT NOT NULL CHECK (status IN ('proposed','approved','rejected','superseded')),
    survivor_person_id TEXT NOT NULL REFERENCES people(person_id),
    reviewed_by TEXT,
    reviewed_at TEXT,
    evidence_json TEXT,
    PRIMARY KEY(crosswalk_id, version),
    CHECK (status = 'proposed' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS person_aliases (
    alias_person_id TEXT PRIMARY KEY REFERENCES people(person_id),
    canonical_person_id TEXT NOT NULL REFERENCES people(person_id),
    crosswalk_id TEXT NOT NULL,
    crosswalk_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (alias_person_id <> canonical_person_id),
    FOREIGN KEY(crosswalk_id, crosswalk_version)
        REFERENCES person_crosswalks(crosswalk_id, version)
);

CREATE TRIGGER IF NOT EXISTS person_aliases_immutable_update
BEFORE UPDATE ON person_aliases BEGIN SELECT RAISE(ABORT, 'person aliases are permanent'); END;
CREATE TRIGGER IF NOT EXISTS person_aliases_immutable_delete
BEFORE DELETE ON person_aliases BEGIN SELECT RAISE(ABORT, 'person aliases are permanent'); END;

CREATE TABLE IF NOT EXISTS mandates (
    mandate_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES people(person_id),
    legislature_number INTEGER NOT NULL REFERENCES legislatures(number),
    chamber_code TEXT NOT NULL REFERENCES chambers(code),
    starts_on TEXT,
    ends_on TEXT,
    UNIQUE(mandate_id, legislature_number, chamber_code),
    CHECK (ends_on IS NULL OR starts_on IS NULL OR ends_on >= starts_on)
);

CREATE TABLE IF NOT EXISTS political_groups (
    group_id TEXT PRIMARY KEY,
    legislature_number INTEGER NOT NULL REFERENCES legislatures(number),
    chamber_code TEXT NOT NULL REFERENCES chambers(code),
    name TEXT NOT NULL,
    abbreviation TEXT,
    UNIQUE(group_id, legislature_number, chamber_code)
);

CREATE TABLE IF NOT EXISTS memberships (
    membership_id TEXT PRIMARY KEY,
    mandate_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    legislature_number INTEGER NOT NULL,
    chamber_code TEXT NOT NULL,
    starts_on TEXT NOT NULL,
    ends_on TEXT,
    FOREIGN KEY(mandate_id, legislature_number, chamber_code)
        REFERENCES mandates(mandate_id, legislature_number, chamber_code),
    FOREIGN KEY(group_id, legislature_number, chamber_code)
        REFERENCES political_groups(group_id, legislature_number, chamber_code),
    CHECK (ends_on IS NULL OR ends_on >= starts_on)
);

CREATE TABLE IF NOT EXISTS sittings (
    sitting_id TEXT PRIMARY KEY,
    legislature_number INTEGER NOT NULL REFERENCES legislatures(number),
    chamber_code TEXT NOT NULL REFERENCES chambers(code),
    source_sitting_id TEXT NOT NULL,
    sitting_date TEXT NOT NULL,
    UNIQUE(sitting_id, legislature_number, chamber_code)
);

CREATE TABLE IF NOT EXISTS parliamentary_items (
    item_id TEXT PRIMARY KEY,
    legislature_number INTEGER NOT NULL REFERENCES legislatures(number),
    chamber_code TEXT NOT NULL REFERENCES chambers(code),
    item_type TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    title TEXT,
    official_url TEXT,
    UNIQUE(item_id, legislature_number, chamber_code)
);

CREATE TABLE IF NOT EXISTS parliamentary_item_relations (
    relation_id TEXT PRIMARY KEY,
    legislature_number INTEGER NOT NULL,
    chamber_code TEXT NOT NULL,
    from_item_id TEXT NOT NULL,
    to_item_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    FOREIGN KEY(from_item_id, legislature_number, chamber_code)
        REFERENCES parliamentary_items(item_id, legislature_number, chamber_code),
    FOREIGN KEY(to_item_id, legislature_number, chamber_code)
        REFERENCES parliamentary_items(item_id, legislature_number, chamber_code)
);

CREATE TABLE IF NOT EXISTS roll_calls (
    roll_call_id TEXT PRIMARY KEY,
    legislature_number INTEGER NOT NULL REFERENCES legislatures(number),
    chamber_code TEXT NOT NULL REFERENCES chambers(code),
    sitting_id TEXT NOT NULL,
    primary_item_id TEXT,
    source_vote_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    official_type TEXT NOT NULL DEFAULT 'procedural',
    official_title TEXT,
    official_description TEXT,
    official_result TEXT,
    official_url TEXT,
    present_count INTEGER CHECK (present_count IS NULL OR present_count >= 0),
    voting_count INTEGER CHECK (voting_count IS NULL OR voting_count >= 0),
    yes_count INTEGER CHECK (yes_count IS NULL OR yes_count >= 0),
    no_count INTEGER CHECK (no_count IS NULL OR no_count >= 0),
    abstain_count INTEGER CHECK (abstain_count IS NULL OR abstain_count >= 0),
    majority_count INTEGER CHECK (majority_count IS NULL OR majority_count >= 0),
    legal_number_count INTEGER CHECK (legal_number_count IS NULL OR legal_number_count >= 0),
    positions_available INTEGER NOT NULL DEFAULT 1 CHECK (positions_available IN (0,1)),
    position_coverage TEXT NOT NULL DEFAULT 'partial'
        CHECK (position_coverage IN ('complete','partial','unavailable','secret')),
    UNIQUE(roll_call_id, legislature_number, chamber_code),
    FOREIGN KEY(sitting_id, legislature_number, chamber_code)
        REFERENCES sittings(sitting_id, legislature_number, chamber_code),
    FOREIGN KEY(primary_item_id, legislature_number, chamber_code)
        REFERENCES parliamentary_items(item_id, legislature_number, chamber_code)
);

CREATE TABLE IF NOT EXISTS roll_call_items (
    roll_call_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    legislature_number INTEGER NOT NULL,
    chamber_code TEXT NOT NULL,
    role TEXT NOT NULL,
    raw_predicate TEXT,
    PRIMARY KEY(roll_call_id, item_id, role),
    FOREIGN KEY(roll_call_id, legislature_number, chamber_code)
        REFERENCES roll_calls(roll_call_id, legislature_number, chamber_code),
    FOREIGN KEY(item_id, legislature_number, chamber_code)
        REFERENCES parliamentary_items(item_id, legislature_number, chamber_code)
);

CREATE TABLE IF NOT EXISTS votes (
    vote_id TEXT PRIMARY KEY,
    roll_call_id TEXT NOT NULL,
    mandate_id TEXT NOT NULL,
    legislature_number INTEGER NOT NULL,
    chamber_code TEXT NOT NULL,
    raw_position TEXT NOT NULL,
    normalized_position TEXT,
    normalization_status TEXT NOT NULL,
    group_id_at_vote TEXT,
    FOREIGN KEY(roll_call_id, legislature_number, chamber_code)
        REFERENCES roll_calls(roll_call_id, legislature_number, chamber_code),
    FOREIGN KEY(mandate_id, legislature_number, chamber_code)
        REFERENCES mandates(mandate_id, legislature_number, chamber_code),
    FOREIGN KEY(group_id_at_vote, legislature_number, chamber_code)
        REFERENCES political_groups(group_id, legislature_number, chamber_code)
);

CREATE TABLE IF NOT EXISTS source_datasets (
    dataset_id TEXT PRIMARY KEY,
    publisher TEXT NOT NULL,
    license_id TEXT NOT NULL,
    canonical_url TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_artifacts (
    artifact_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES source_datasets(dataset_id),
    sha256 TEXT NOT NULL UNIQUE,
    observed_at TEXT NOT NULL,
    media_type TEXT NOT NULL,
    byte_count INTEGER NOT NULL CHECK (byte_count >= 0)
);
CREATE TABLE IF NOT EXISTS source_records (
    source_record_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL REFERENCES source_artifacts(artifact_id),
    upstream_key TEXT NOT NULL,
    record_locator TEXT NOT NULL,
    raw_scope TEXT NOT NULL,
    mapping_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fact_lineage (
    fact_type TEXT NOT NULL,
    fact_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL REFERENCES source_records(source_record_id),
    resolution_rule TEXT NOT NULL,
    PRIMARY KEY(fact_type, fact_id, source_record_id)
);

CREATE TABLE IF NOT EXISTS disclosure_documents (
    disclosure_id TEXT PRIMARY KEY,
    mandate_id TEXT NOT NULL REFERENCES mandates(mandate_id),
    official_label TEXT NOT NULL,
    official_url TEXT NOT NULL,
    observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS releases (
    release_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    source_fingerprint TEXT NOT NULL UNIQUE,
    data_through TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Structural filters and joins used by the immutable query service. Free-text
-- matching deliberately remains escaped LIKE: FTS tokenization would change
-- official-title semantics and is not required for the bounded structural paths.
CREATE INDEX IF NOT EXISTS idx_mandates_person_scope
    ON mandates(person_id, legislature_number, chamber_code, mandate_id);
CREATE INDEX IF NOT EXISTS idx_source_identities_person
    ON source_identities(canonical_person_id, authority_id, source_person_id);
CREATE INDEX IF NOT EXISTS idx_groups_scope
    ON political_groups(legislature_number, chamber_code, group_id);
CREATE INDEX IF NOT EXISTS idx_memberships_mandate
    ON memberships(mandate_id, starts_on, membership_id);
CREATE INDEX IF NOT EXISTS idx_roll_calls_scope_order
    ON roll_calls(legislature_number, chamber_code, occurred_at DESC, roll_call_id);
CREATE INDEX IF NOT EXISTS idx_votes_roll_call_order
    ON votes(roll_call_id, normalization_status, vote_id);
CREATE INDEX IF NOT EXISTS idx_votes_group_scope
    ON votes(group_id_at_vote, legislature_number, chamber_code, roll_call_id, vote_id);
CREATE INDEX IF NOT EXISTS idx_votes_mandate_order
    ON votes(mandate_id, roll_call_id, vote_id);
CREATE INDEX IF NOT EXISTS idx_disclosures_mandate
    ON disclosure_documents(mandate_id, disclosure_id);
CREATE INDEX IF NOT EXISTS idx_lineage_fact
    ON fact_lineage(fact_type, fact_id, source_record_id);
