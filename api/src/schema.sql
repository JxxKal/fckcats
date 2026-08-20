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

-- Speichermodus je Benutzer:
--   persistent -- Arbeitszeiten, Zieltabelle und Historie werden aufbewahrt
--   ephemeral  -- reines Import/Export-Werkzeug, es wird nichts davon abgelegt
ALTER TABLE users ADD COLUMN IF NOT EXISTS storage_mode TEXT NOT NULL DEFAULT 'persistent';
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_storage_mode_chk') THEN
        ALTER TABLE users ADD CONSTRAINT users_storage_mode_chk
            CHECK (storage_mode IN ('persistent', 'ephemeral'));
    END IF;
END $$;

-- ── Datenschluessel je Benutzer ──────────────────────────────────────────────
-- Der eigentliche Schluessel (DEK) liegt nur eingewickelt hier. Im Modus
-- 'master' wickelt ihn ein aus DATA_MASTER_KEY abgeleiteter Schluessel aus,
-- im Modus 'passphrase' nur die Passphrase des Benutzers.
CREATE TABLE IF NOT EXISTS user_key (
    user_id     BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    wrapped_dek BYTEA NOT NULL,
    wrap_mode   TEXT  NOT NULL DEFAULT 'master'
                CHECK (wrap_mode IN ('master', 'passphrase')),
    kdf_salt    BYTEA NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
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
    -- Verschluesselt: {"personnel_number": "...", "wbs_elements": [...],
    --                  "reason_rules": {...}}
    payload_enc     BYTEA,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, version)
);
CREATE INDEX IF NOT EXISTS cats_config_user_idx ON cats_config (user_id, version DESC);

-- ── Hochgeladene Zeitnachweis-PDFs ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS uploads (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stored_path   TEXT   NOT NULL,
    -- Pruefsumme der hochgeladenen Datei; erkennt denselben Zeitnachweis wieder.
    sha256        TEXT,
    -- Verschluesselt: {"filename": "...", "personnel_number": "...",
    --                  "period_month": 7, "period_year": 2026, "days": [...],
    --                  "warnings": [...]}
    payload_enc   BYTEA,
    uploaded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    committed_at  TIMESTAMPTZ                -- NULL = Vorschau, noch nicht uebernommen
);
ALTER TABLE uploads ADD COLUMN IF NOT EXISTS sha256 TEXT;
CREATE INDEX IF NOT EXISTS uploads_user_idx ON uploads (user_id, uploaded_at DESC);

-- ── Validierte Tagesliste ────────────────────────────────────────────────────
-- Eine Zeile je User und Tag. Basis fuer die Verteilung.
CREATE TABLE IF NOT EXISTS workday (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    work_date   DATE   NOT NULL,
    -- Verschluesselt: {"hours": 7.83, "source": "pdf", "reason_text": "..."}
    payload_enc BYTEA,
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
    -- Verschluesselt: {"wbs_element": "...", "hours": 4.0}
    payload_enc  BYTEA,
    -- HMAC des WBS-Elements: haelt die Eindeutigkeit aufrecht, ohne den Wert
    -- preiszugeben. Ein verschluesseltes Feld taugt nicht fuer UNIQUE.
    wbs_hash     BYTEA,
    run_id       BIGINT REFERENCES distribution_run(id) ON DELETE SET NULL,
    -- Verweis auf den Export. Wird beim Loeschen des Exports genullt.
    export_id    BIGINT,                     -- FK weiter unten
    -- Der eigentliche Buchungsstatus. Bewusst NICHT am Fremdschluessel
    -- haengend: wer die Export-Historie aufraeumt, darf dadurch keine bereits
    -- in SAP gebuchten Zeilen wieder als offen sehen und doppelt einspielen.
    exported_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, work_date, wbs_hash)
);
CREATE INDEX IF NOT EXISTS cats_entry_user_date_idx ON cats_entry (user_id, work_date);

-- Bestandsdaten aus der Zeit, als der Status nur am Fremdschluessel hing.
ALTER TABLE cats_entry ADD COLUMN IF NOT EXISTS exported_at TIMESTAMPTZ;
UPDATE cats_entry SET exported_at = now()
 WHERE exported_at IS NULL AND export_id IS NOT NULL;

DROP INDEX IF EXISTS cats_entry_open_idx;
CREATE INDEX IF NOT EXISTS cats_entry_open_idx
    ON cats_entry (user_id) WHERE exported_at IS NULL;

-- ── Exporte ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS export (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stored_path TEXT   NOT NULL,
    date_from   DATE   NOT NULL,
    date_to     DATE   NOT NULL,
    -- Verschluesselt: {"filename": "...", "row_count": 71, "total_hours": 152.03}
    payload_enc BYTEA,
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
    payload_enc BYTEA,                       -- verschluesselte ersetzte Zeilen
    was_exported BOOLEAN NOT NULL DEFAULT FALSE,
    replaced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS entry_history_user_idx ON entry_history (user_id, work_date);
