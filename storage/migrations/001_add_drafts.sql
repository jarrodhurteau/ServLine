-- 001_add_drafts.sql
-- Day 12: Drafts & Draft Items schema

-- drafts table
CREATE TABLE IF NOT EXISTS drafts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  restaurant_id INTEGER NOT NULL,
  menu_id       INTEGER,             -- nullable until promotion
  title         TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'editing', -- editing|submitted|approved|rejected|archived
  source        TEXT,                -- ocr|pdf|manual|import
  author        TEXT,                -- optional (email/username)
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (restaurant_id) REFERENCES restaurants(id),
  FOREIGN KEY (menu_id) REFERENCES menus(id)
);

-- draft_items table
CREATE TABLE IF NOT EXISTS draft_items (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id      INTEGER NOT NULL,
  name          TEXT NOT NULL,
  description   TEXT,
  price_cents   INTEGER NOT NULL DEFAULT 0,
  category      TEXT,
  position      INTEGER,
  raw_json      TEXT,                -- original OCR/PDF fragment for traceability
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE CASCADE
);

-- indexes
CREATE INDEX IF NOT EXISTS idx_drafts_status     ON drafts(status);
CREATE INDEX IF NOT EXISTS idx_drafts_restaurant ON drafts(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_draft_items_draft ON draft_items(draft_id);

-- update triggers
CREATE TRIGGER IF NOT EXISTS trg_drafts_updated
AFTER UPDATE ON drafts
FOR EACH ROW BEGIN
  UPDATE drafts SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_draft_items_updated
AFTER UPDATE ON draft_items
FOR EACH ROW BEGIN
  UPDATE draft_items SET updated_at = datetime('now') WHERE id = NEW.id;
END;
