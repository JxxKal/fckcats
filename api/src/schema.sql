-- fckcats — Datenbankschema
-- Wird beim API-Start idempotent angewandt.

CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    email         TEXT,
    display_name  TEXT,
    password_hash TEXT,                      -- nur bei source='local'
    role          TEXT NOT NULL DEFAULT 'user'
                  CHECK (role IN ('admin', 'user')),
    source        TEXT NOT NULL DEFAULT 'local'
                  CHECK (source IN ('local', 'saml')),
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login    TIMESTAMPTZ
);

-- Systemweite Konfiguration (SAML-Settings etc.)
CREATE TABLE IF NOT EXISTS system_config (
    key   TEXT PRIMARY KEY,
    value JSONB NOT NULL
);

-- ── CATS-Config je User (versioniert) ────────────────────────────────────────
-- Jede Aenderung erzeugt eine neue Version. Berechnungen halten fest, welche
-- Version sie verwendet haben, damit ein Nachvollziehen moeglich bleibt.
CREATE TABLE IF NOT EXISTS cats_config (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    version         INT    NOT NULL,
    personnel_number TEXT  NOT NULL,
    -- [{"wbs": "...", "weight": 40.0}, ...] — Summe der weights == 100
    wbs_elements    JSONB  NOT NULL DEFAULT '[]'::jsonb,
    -- {"Dienstreise": "book", "Fortbildung": "exclude"}
    reason_rules    JSONB  NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, version)
);
CREATE INDEX IF NOT EXISTS cats_config_user_idx ON cats_config (user_id, version DESC);

-- ── Hochgeladene Zeitnachweis-PDFs ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS uploads (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename      TEXT   NOT NULL,
    stored_path   TEXT   NOT NULL,
    sha256        TEXT   NOT NULL,
    period_month  INT,
    period_year   INT,
    personnel_number TEXT,
    uploaded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    committed_at  TIMESTAMPTZ,               -- NULL = Vorschau, noch nicht uebernommen
    parse_result  JSONB  NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS uploads_user_idx ON uploads (user_id, uploaded_at DESC);

-- ── Validierte Tagesliste ────────────────────────────────────────────────────
-- Eine Zeile je User und Tag. Basis fuer die Verteilung.
CREATE TABLE IF NOT EXISTS workday (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    work_date   DATE   NOT NULL,
    hours       NUMERIC(6,2) NOT NULL CHECK (hours > 0),
    source      TEXT   NOT NULL DEFAULT 'pdf'
                CHECK (source IN ('pdf', 'manual')),
    reason_text TEXT,                        -- Originaltext aus der Grund-Spalte
    upload_id   BIGINT REFERENCES uploads(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, work_date)
);
CREATE INDEX IF NOT EXISTS workday_user_date_idx ON workday (user_id, work_date);

-- ── Berechnungslaeufe ────────────────────────────────────────────────────────
-- Haelt den Seed fest, damit eine Verteilung reproduzierbar bleibt.
CREATE TABLE IF NOT EXISTS distribution_run (
    id             BIGSERIAL PRIMARY KEY,
    user_id        BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    config_version INT    NOT NULL,
    seed           BIGINT NOT NULL,
    date_from      DATE   NOT NULL,
    date_to        DATE   NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Zieltabelle ──────────────────────────────────────────────────────────────
-- Der eigentliche Bestand. Die XLSX ist nur eine Sicht darauf.
CREATE TABLE IF NOT EXISTS cats_entry (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    work_date    DATE   NOT NULL,
    wbs_element  TEXT   NOT NULL,
    hours        NUMERIC(6,2) NOT NULL CHECK (hours > 0),
    run_id       BIGINT REFERENCES distribution_run(id) ON DELETE SET NULL,
    export_id    BIGINT,                     -- gesetzt = exportiert (FK weiter unten)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, work_date, wbs_element)
);
CREATE INDEX IF NOT EXISTS cats_entry_user_date_idx ON cats_entry (user_id, work_date);
CREATE INDEX IF NOT EXISTS cats_entry_open_idx ON cats_entry (user_id) WHERE export_id IS NULL;

-- ── Exporte ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS export (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename    TEXT   NOT NULL,
    stored_path TEXT   NOT NULL,
    date_from   DATE   NOT NULL,
    date_to     DATE   NOT NULL,
    row_count   INT    NOT NULL,
    total_hours NUMERIC(8,2) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS export_user_idx ON export (user_id, created_at DESC);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'cats_entry_export_fk'
    ) THEN
        ALTER TABLE cats_entry
            ADD CONSTRAINT cats_entry_export_fk
            FOREIGN KEY (export_id) REFERENCES export(id) ON DELETE SET NULL;
    END IF;
END $$;

-- ── Protokoll ueberschriebener Zeilen ────────────────────────────────────────
-- Wird beim Re-Import eines korrigierten PDFs gefuellt, damit die alte Fassung
-- einsehbar bleibt.
CREATE TABLE IF NOT EXISTS entry_history (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    work_date   DATE   NOT NULL,
    payload     JSONB  NOT NULL,             -- ersetzte cats_entry-Zeilen
    was_exported BOOLEAN NOT NULL DEFAULT FALSE,
    replaced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS entry_history_user_idx ON entry_history (user_id, work_date);
