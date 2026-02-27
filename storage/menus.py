# storage/menus.py  — Multi-Menu & Versioning (Phase 10, Day 86+)
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from .drafts import db_connect, _now


# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------
VALID_MENU_TYPES = frozenset({
    "breakfast", "lunch", "dinner", "brunch", "happy_hour",
    "kids", "dessert", "drinks", "catering", "seasonal", "other",
})


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


# ------------------------------------------------------------
# Schema (idempotent; safe to call repeatedly)
# ------------------------------------------------------------
def _ensure_menu_schema() -> None:
    with db_connect() as conn:
        cur = conn.cursor()

        # -- Alter existing menus table: add new columns if missing ------
        def _col_exists(table: str, col: str) -> bool:
            return any(
                r[1].lower() == col
                for r in conn.execute(f"PRAGMA table_info({table});").fetchall()
            )

        if not _col_exists("menus", "menu_type"):
            cur.execute("ALTER TABLE menus ADD COLUMN menu_type TEXT;")
        if not _col_exists("menus", "description"):
            cur.execute("ALTER TABLE menus ADD COLUMN description TEXT;")
        if not _col_exists("menus", "updated_at"):
            cur.execute("ALTER TABLE menus ADD COLUMN updated_at TEXT;")

        # -- New tables ---------------------------------------------------

        # menu_versions: immutable snapshots of a menu at a point in time
        cur.execute("""
            CREATE TABLE IF NOT EXISTS menu_versions (
              id              INTEGER PRIMARY KEY AUTOINCREMENT,
              menu_id         INTEGER NOT NULL,
              version_number  INTEGER NOT NULL DEFAULT 1,
              label           TEXT,
              source_draft_id INTEGER,
              item_count      INTEGER NOT NULL DEFAULT 0,
              variant_count   INTEGER NOT NULL DEFAULT 0,
              notes           TEXT,
              created_by      TEXT,
              created_at      TEXT NOT NULL,
              FOREIGN KEY (menu_id) REFERENCES menus(id) ON DELETE CASCADE,
              FOREIGN KEY (source_draft_id) REFERENCES drafts(id) ON DELETE SET NULL,
              UNIQUE (menu_id, version_number)
            )
        """)

        # menu_version_items: snapshot of items (with category + position)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS menu_version_items (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              version_id  INTEGER NOT NULL,
              name        TEXT NOT NULL,
              description TEXT,
              price_cents INTEGER NOT NULL DEFAULT 0,
              category    TEXT,
              position    INTEGER,
              created_at  TEXT NOT NULL,
              FOREIGN KEY (version_id) REFERENCES menu_versions(id) ON DELETE CASCADE
            )
        """)

        # menu_version_item_variants: structured variant snapshot
        cur.execute("""
            CREATE TABLE IF NOT EXISTS menu_version_item_variants (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              item_id     INTEGER NOT NULL,
              label       TEXT NOT NULL,
              price_cents INTEGER NOT NULL DEFAULT 0,
              kind        TEXT DEFAULT 'size',
              position    INTEGER DEFAULT 0,
              created_at  TEXT NOT NULL,
              FOREIGN KEY (item_id) REFERENCES menu_version_items(id) ON DELETE CASCADE
            )
        """)

        # -- Indexes ------------------------------------------------------
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_menu_versions_menu "
            "ON menu_versions(menu_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_menu_versions_draft "
            "ON menu_versions(source_draft_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_mvi_version "
            "ON menu_version_items(version_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_mvi_version_cat "
            "ON menu_version_items(version_id, category)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_mviv_item "
            "ON menu_version_item_variants(item_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_menus_restaurant_active "
            "ON menus(restaurant_id, active)"
        )

        conn.commit()


_ensure_menu_schema()


# ====================================================================
# Menu CRUD
# ====================================================================

def create_menu(
    restaurant_id: int,
    name: str,
    *,
    menu_type: Optional[str] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new menu for a restaurant. Returns the full menu dict."""
    if menu_type and menu_type not in VALID_MENU_TYPES:
        menu_type = None
    now = _now()
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO menus (restaurant_id, name, menu_type, description,
                               active, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (restaurant_id, name, menu_type, description, now, now),
        )
        conn.commit()
        menu_id = int(cur.lastrowid)
    return {
        "id": menu_id,
        "restaurant_id": restaurant_id,
        "name": name,
        "menu_type": menu_type,
        "description": description,
        "active": 1,
        "created_at": now,
        "updated_at": now,
    }


def list_menus(
    restaurant_id: int,
    *,
    include_inactive: bool = False,
) -> List[Dict[str, Any]]:
    """List menus for a restaurant with version counts. Active only by default."""
    with db_connect() as conn:
        qs = """
            SELECT m.*,
                   COALESCE(vc.cnt, 0) AS version_count
            FROM menus m
            LEFT JOIN (
                SELECT menu_id, COUNT(*) AS cnt
                FROM menu_versions
                GROUP BY menu_id
            ) vc ON vc.menu_id = m.id
            WHERE m.restaurant_id = ?
        """
        args: List[Any] = [restaurant_id]
        if not include_inactive:
            qs += " AND m.active = 1"
        qs += " ORDER BY m.created_at ASC, m.id ASC"
        rows = conn.execute(qs, args).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_menu(menu_id: int) -> Optional[Dict[str, Any]]:
    """Get a single menu by id. Returns None if not found."""
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM menus WHERE id = ?", (menu_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None


def update_menu(
    menu_id: int,
    *,
    name: Optional[str] = None,
    menu_type: Optional[str] = None,
    description: Optional[str] = None,
) -> bool:
    """Update menu metadata. Returns True if a row was updated."""
    sets: List[str] = []
    args: List[Any] = []
    if name is not None:
        sets.append("name=?")
        args.append(name)
    if menu_type is not None:
        if menu_type not in VALID_MENU_TYPES:
            menu_type = None
        sets.append("menu_type=?")
        args.append(menu_type)
    if description is not None:
        sets.append("description=?")
        args.append(description)
    if not sets:
        return False
    sets.append("updated_at=?")
    args.append(_now())
    args.append(menu_id)
    with db_connect() as conn:
        cur = conn.execute(
            f"UPDATE menus SET {', '.join(sets)} WHERE id=?", args
        )
        conn.commit()
        return cur.rowcount > 0


def delete_menu(menu_id: int) -> bool:
    """Soft-delete a menu (set active=0). Returns True if a row was updated."""
    with db_connect() as conn:
        cur = conn.execute(
            "UPDATE menus SET active=0, updated_at=? WHERE id=? AND active=1",
            (_now(), menu_id),
        )
        conn.commit()
        return cur.rowcount > 0


# ====================================================================
# Menu Version CRUD
# ====================================================================

def create_menu_version(
    menu_id: int,
    *,
    source_draft_id: Optional[int] = None,
    label: Optional[str] = None,
    notes: Optional[str] = None,
    created_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new version, optionally snapshotting items from a draft.

    Auto-increments version_number per menu. If source_draft_id is provided,
    copies all draft items + variants into the version snapshot.
    Returns the version dict.
    """
    now = _now()
    with db_connect() as conn:
        cur = conn.cursor()

        # Compute next version number for this menu
        row = conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) AS mx "
            "FROM menu_versions WHERE menu_id=?",
            (menu_id,),
        ).fetchone()
        version_number = row["mx"] + 1

        if label is None:
            label = f"v{version_number}"

        item_count = 0
        variant_count = 0

        # Snapshot draft items if draft specified
        draft_items: List[Dict[str, Any]] = []
        if source_draft_id is not None:
            # Import here to avoid circular dependency at module level
            from .drafts import get_draft_items
            draft_items = get_draft_items(source_draft_id, include_variants=True)
            item_count = len(draft_items)
            variant_count = sum(len(it.get("variants", [])) for it in draft_items)

        # Insert version row
        cur.execute(
            """
            INSERT INTO menu_versions (
                menu_id, version_number, label, source_draft_id,
                item_count, variant_count, notes, created_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (menu_id, version_number, label, source_draft_id,
             item_count, variant_count, notes, created_by, now),
        )
        version_id = int(cur.lastrowid)

        # Copy items + variants
        for pos, it in enumerate(draft_items):
            cur.execute(
                """
                INSERT INTO menu_version_items
                    (version_id, name, description, price_cents,
                     category, position, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    it.get("name", ""),
                    it.get("description"),
                    it.get("price_cents", 0),
                    it.get("category"),
                    it.get("position", pos),
                    now,
                ),
            )
            vi_id = int(cur.lastrowid)
            for vpos, v in enumerate(it.get("variants", [])):
                cur.execute(
                    """
                    INSERT INTO menu_version_item_variants
                        (item_id, label, price_cents, kind, position, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        vi_id,
                        v.get("label", ""),
                        v.get("price_cents", 0),
                        v.get("kind", "size"),
                        v.get("position", vpos),
                        now,
                    ),
                )

        conn.commit()

    return {
        "id": version_id,
        "menu_id": menu_id,
        "version_number": version_number,
        "label": label,
        "source_draft_id": source_draft_id,
        "item_count": item_count,
        "variant_count": variant_count,
        "notes": notes,
        "created_by": created_by,
        "created_at": now,
    }


def list_menu_versions(
    menu_id: int,
    *,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """List versions for a menu, newest first."""
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM menu_versions WHERE menu_id=? "
            "ORDER BY version_number DESC LIMIT ? OFFSET ?",
            (menu_id, limit, offset),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_menu_version(
    version_id: int,
    *,
    include_items: bool = True,
) -> Optional[Dict[str, Any]]:
    """Get a version by id. If include_items=True, includes items with variants."""
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM menu_versions WHERE id=?", (version_id,)
        ).fetchone()
        if not row:
            return None
        result = _row_to_dict(row)
        if not include_items:
            return result

        # LEFT JOIN items + variants (same pattern as drafts.get_draft_items)
        rows = conn.execute(
            """
            SELECT
                i.id            AS item_id,
                i.name          AS item_name,
                i.description   AS item_description,
                i.price_cents   AS item_price_cents,
                i.category      AS item_category,
                i.position      AS item_position,
                v.id            AS var_id,
                v.label         AS var_label,
                v.price_cents   AS var_price_cents,
                v.kind          AS var_kind,
                v.position      AS var_position
            FROM menu_version_items i
            LEFT JOIN menu_version_item_variants v ON v.item_id = i.id
            WHERE i.version_id = ?
            ORDER BY i.position ASC, i.id ASC, v.position ASC, v.id ASC
            """,
            (version_id,),
        ).fetchall()

        items: List[Dict[str, Any]] = []
        seen: Dict[int, Dict[str, Any]] = {}
        for r in rows:
            iid = r["item_id"]
            if iid not in seen:
                item = {
                    "id": iid,
                    "name": r["item_name"],
                    "description": r["item_description"],
                    "price_cents": r["item_price_cents"],
                    "category": r["item_category"],
                    "position": r["item_position"],
                    "variants": [],
                }
                seen[iid] = item
                items.append(item)
            if r["var_id"] is not None:
                seen[iid]["variants"].append({
                    "id": r["var_id"],
                    "label": r["var_label"],
                    "price_cents": r["var_price_cents"],
                    "kind": r["var_kind"],
                    "position": r["var_position"],
                })
        result["items"] = items
        return result


def get_current_version(
    menu_id: int,
    *,
    include_items: bool = False,
) -> Optional[Dict[str, Any]]:
    """Get the latest version (highest version_number) for a menu."""
    with db_connect() as conn:
        row = conn.execute(
            "SELECT id FROM menu_versions WHERE menu_id=? "
            "ORDER BY version_number DESC LIMIT 1",
            (menu_id,),
        ).fetchone()
        if not row:
            return None
        return get_menu_version(row["id"], include_items=include_items)


# ====================================================================
# Migration: backfill existing menus into versioned model
# ====================================================================

def migrate_existing_menus() -> Dict[str, int]:
    """Migrate existing published menus to the versioned model.

    For each active menu that has menu_items but no menu_versions entry,
    creates a v1 version and copies existing menu_items into
    menu_version_items.

    Returns: {"menus_migrated": N, "items_copied": N}
    Idempotent: skips menus that already have versions.
    """
    now = _now()
    menus_migrated = 0
    items_copied = 0
    with db_connect() as conn:
        cur = conn.cursor()
        # Find menus with items but no versions
        menus = conn.execute("""
            SELECT m.id
            FROM menus m
            WHERE EXISTS (SELECT 1 FROM menu_items mi WHERE mi.menu_id = m.id)
              AND NOT EXISTS (SELECT 1 FROM menu_versions mv WHERE mv.menu_id = m.id)
        """).fetchall()

        for m in menus:
            mid = m["id"]
            # Get existing menu_items
            old_items = conn.execute(
                "SELECT * FROM menu_items WHERE menu_id=? ORDER BY id ASC",
                (mid,),
            ).fetchall()
            if not old_items:
                continue

            # Create v1 version
            cur.execute(
                """
                INSERT INTO menu_versions
                    (menu_id, version_number, label, item_count,
                     variant_count, notes, created_at)
                VALUES (?, 1, 'v1 (migrated)', ?, 0, 'Auto-migrated from legacy menu_items', ?)
                """,
                (mid, len(old_items), now),
            )
            version_id = int(cur.lastrowid)

            # Copy items
            for pos, oi in enumerate(old_items):
                cur.execute(
                    """
                    INSERT INTO menu_version_items
                        (version_id, name, description, price_cents,
                         category, position, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        version_id,
                        oi["name"],
                        oi["description"],
                        oi["price_cents"],
                        None,  # old menu_items has no category
                        pos,
                        now,
                    ),
                )
                items_copied += 1

            menus_migrated += 1

        conn.commit()

    return {"menus_migrated": menus_migrated, "items_copied": items_copied}
