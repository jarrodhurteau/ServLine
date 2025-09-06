import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "servline.db"

def cents_to_dollars(cents: int) -> str:
    return f"${cents/100:.2f}"

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print("\n--- Restaurants ---")
    for row in cur.execute("SELECT * FROM restaurants"):
        print(dict(row))

    print("\n--- Menus ---")
    for row in cur.execute("SELECT * FROM menus"):
        print(dict(row))

    print("\n--- Menu Items ---")
    for row in cur.execute(
        "SELECT id, menu_id, name, description, price_cents, is_available FROM menu_items"
    ):
        row_dict = dict(row)
        row_dict["price"] = cents_to_dollars(row_dict.pop("price_cents"))
        print(row_dict)

    conn.close()

if __name__ == "__main__":
    main()
