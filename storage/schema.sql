PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS restaurants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  phone TEXT,
  address TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS menus (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  restaurant_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE CASCADE
);

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

-- Day 8: Import Jobs (track uploaded menu files + OCR results)
CREATE TABLE IF NOT EXISTS import_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  restaurant_id INTEGER,                    -- optional link to restaurant
  filename TEXT NOT NULL,                   -- uploaded file name
  status TEXT NOT NULL DEFAULT 'pending',   -- pending | processing | done | failed
  draft_path TEXT,                          -- relative path to storage/drafts/*.json
  error TEXT,                               -- error notes if OCR fails
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_import_jobs_status ON import_jobs(status);
CREATE INDEX IF NOT EXISTS idx_import_jobs_created ON import_jobs(created_at);
