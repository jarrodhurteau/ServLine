PRAGMA foreign_keys = ON;

-----------------------------------------------------------------------
-- Core: Restaurants / Menus / Items
-----------------------------------------------------------------------

-- Restaurants
CREATE TABLE IF NOT EXISTS restaurants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  phone TEXT,
  address TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Menus
CREATE TABLE IF NOT EXISTS menus (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  restaurant_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE CASCADE
);

-- Menu Items
CREATE TABLE IF NOT EXISTS menu_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  menu_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  description TEXT,
  price_cents INTEGER NOT NULL,
  is_available INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (menu_id) REFERENCES menus(id) ON DELETE CASCADE
);
-- helpful index for faster menu loads
CREATE INDEX IF NOT EXISTS idx_menu_items_menu ON menu_items(menu_id);

-----------------------------------------------------------------------
-- Day 8: Import Jobs (uploads + OCR pipeline)
-----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS import_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  restaurant_id INTEGER,                        -- optional association
  filename TEXT NOT NULL,                       -- sanitized uploaded filename
  source_path TEXT,                             -- original relative path if needed
  status TEXT NOT NULL DEFAULT 'pending',       -- pending|processing|done|failed|deleted|restored|published
  lifecycle TEXT NOT NULL DEFAULT 'active',     -- future use
  trashed_at TEXT,                              -- when moved into uploads/.trash
  draft_path TEXT,                              -- storage/drafts/*.json (legacy OCR output)
  error TEXT,                                   -- failure notes
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_import_jobs_status    ON import_jobs(status);
CREATE INDEX IF NOT EXISTS idx_import_jobs_lifecycle ON import_jobs(lifecycle);
CREATE INDEX IF NOT EXISTS idx_import_jobs_created   ON import_jobs(created_at);

-----------------------------------------------------------------------
-- Day 12+: Drafts (DB-backed editor)
-- Day 14 adds `source` (JSON string with provenance) and keeps a
-- unique link to import_jobs via source_job_id.
-----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS drafts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  restaurant_id INTEGER,
  title TEXT NOT NULL DEFAULT 'Untitled Draft',
  status TEXT NOT NULL DEFAULT 'editing',       -- editing|submitted|approved|rejected|archived
  source_job_id INTEGER,                        -- provenance link to import_jobs.id
  source TEXT,                                  -- NEW (Day 14): JSON blob (e.g., {"file":..., "ocr_engine":...})
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE SET NULL,
  FOREIGN KEY (source_job_id) REFERENCES import_jobs(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_drafts_status      ON drafts(status);
CREATE INDEX IF NOT EXISTS idx_drafts_restaurant  ON drafts(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_drafts_updated     ON drafts(updated_at);
-- Ensure at most one draft per import job (but allow NULLs)
CREATE UNIQUE INDEX IF NOT EXISTS uidx_drafts_source_job
  ON drafts(source_job_id)
  WHERE source_job_id IS NOT NULL;

-- Draft Items
CREATE TABLE IF NOT EXISTS draft_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  description TEXT,
  price_cents INTEGER NOT NULL DEFAULT 0,
  category TEXT,
  position INTEGER,                              -- display sort (NULLs last)
  confidence INTEGER,                            -- NEW (Day 14): OCR confidence (nullable)
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_draft_items_draft ON draft_items(draft_id);
CREATE INDEX IF NOT EXISTS idx_draft_items_cat   ON draft_items(draft_id, category);
CREATE INDEX IF NOT EXISTS idx_draft_items_pos   ON draft_items(draft_id, position);

-----------------------------------------------------------------------
-- Day 21+: Performance helpers and dedupe indices
-----------------------------------------------------------------------

-- Speeds up approve_draft_to_menu() which checks for existing menu items
CREATE INDEX IF NOT EXISTS idx_menu_items_dedupe
  ON menu_items(menu_id, name, price_cents);

-- Improves lookups and sorting for imports dashboard
CREATE INDEX IF NOT EXISTS idx_import_jobs_filename
  ON import_jobs(filename);

CREATE INDEX IF NOT EXISTS idx_import_jobs_updated
  ON import_jobs(updated_at);

-- Common compound filter for active job listings
CREATE INDEX IF NOT EXISTS idx_import_jobs_status_updated
  ON import_jobs(status, updated_at);

-----------------------------------------------------------------------
-- (Optional) helpers: simple updated_at bumpers
-----------------------------------------------------------------------
/* Example triggers if you want SQLite to auto-bump updated_at:
-- DRAFTS
CREATE TRIGGER IF NOT EXISTS trg_drafts_updated
AFTER UPDATE ON drafts
FOR EACH ROW
BEGIN
  UPDATE drafts SET updated_at = datetime('now') WHERE id = NEW.id;
END;

-- DRAFT ITEMS
CREATE TRIGGER IF NOT EXISTS trg_draft_items_updated
AFTER UPDATE ON draft_items
FOR EACH ROW
BEGIN
  UPDATE draft_items SET updated_at = datetime('now') WHERE id = NEW.id;
END;
*/
