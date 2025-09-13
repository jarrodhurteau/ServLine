import sqlite3

DB = "storage/servline.db"
con = sqlite3.connect(DB)
cur = con.cursor()

for table in ("drafts", "import_jobs"):
    cur.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]
    print(f"{table}: {cols}")

con.close()
