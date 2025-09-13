import sqlite3

DB = "storage/servline.db"
con = sqlite3.connect(DB)
cur = con.cursor()

def ensure_column(table, column, coldef):
    cur.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]
    if column not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {coldef}")
        print(f"Added {table}.{column}")
    else:
        print(f"OK: {table}.{column} already exists")

ensure_column("drafts", "source_job_id", "INTEGER")
ensure_column("import_jobs", "draft_id", "INTEGER")

con.commit()
con.close()
print("Done.")
