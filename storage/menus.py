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
# Version Comparison / Diff Engine (Day 89)
# ====================================================================

_ITEM_DIFF_FIELDS = ("name", "description", "price_cents", "category", "position")
_VARIANT_DIFF_FIELDS = ("label", "price_cents", "kind", "position")


def _normalize_for_match(val: Any) -> str:
    """Lowercase + strip for matching names/labels."""
    return (val or "").strip().lower()


def _price_direction(old_cents, new_cents) -> Optional[str]:
    """Return 'increase', 'decrease', or None for price changes."""
    old_v = old_cents or 0
    new_v = new_cents or 0
    if new_v > old_v:
        return "increase"
    elif new_v < old_v:
        return "decrease"
    return None


def _diff_item_fields(
    item_a: Dict[str, Any],
    item_b: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Compare two matched items field-by-field.

    Returns list of ``{field, old, new}`` for fields that differ.
    None and empty string are treated as equivalent for description.
    Price changes include ``price_direction`` ('increase'/'decrease').
    """
    changes: List[Dict[str, Any]] = []
    for f in _ITEM_DIFF_FIELDS:
        old = item_a.get(f)
        new = item_b.get(f)
        # Treat None and "" as equivalent for text fields
        if f in ("description", "category"):
            old = old or ""
            new = new or ""
        if old != new:
            entry: Dict[str, Any] = {"field": f, "old": old, "new": new}
            if f == "price_cents":
                entry["price_direction"] = _price_direction(old, new)
            changes.append(entry)
    return changes


def _diff_variants(
    variants_a: List[Dict[str, Any]],
    variants_b: List[Dict[str, Any]],
) -> Dict[str, list]:
    """Compare two variant lists by normalized label.

    Returns ``{added, removed, modified, unchanged}`` where each value
    is a list.  ``modified`` entries are dicts with ``variant_a``,
    ``variant_b``, and ``field_changes``.
    """
    result: Dict[str, list] = {
        "added": [], "removed": [], "modified": [], "unchanged": [],
    }

    # Build lookup by normalized label
    lookup_a: Dict[str, List[Dict]] = {}
    for v in variants_a:
        key = _normalize_for_match(v.get("label"))
        lookup_a.setdefault(key, []).append(v)

    lookup_b: Dict[str, List[Dict]] = {}
    for v in variants_b:
        key = _normalize_for_match(v.get("label"))
        lookup_b.setdefault(key, []).append(v)

    matched_a: set = set()
    matched_b: set = set()
    all_keys = set(lookup_a) | set(lookup_b)

    for key in all_keys:
        a_list = lookup_a.get(key, [])
        b_list = lookup_b.get(key, [])
        pairs = min(len(a_list), len(b_list))
        for i in range(pairs):
            va, vb = a_list[i], b_list[i]
            matched_a.add(id(va))
            matched_b.add(id(vb))
            # Compare fields
            fc: List[Dict[str, Any]] = []
            for f in _VARIANT_DIFF_FIELDS:
                old = va.get(f)
                new = vb.get(f)
                if old != new:
                    entry: Dict[str, Any] = {"field": f, "old": old, "new": new}
                    if f == "price_cents":
                        entry["price_direction"] = _price_direction(old, new)
                    fc.append(entry)
            if fc:
                result["modified"].append({
                    "variant_a": va, "variant_b": vb, "field_changes": fc,
                })
            else:
                result["unchanged"].append(vb)
        # Excess unmatched go directly to removed/added
        for va in a_list[pairs:]:
            result["removed"].append(va)
        for vb in b_list[pairs:]:
            result["added"].append(vb)

    return result


def compare_menu_versions(
    version_id_a: int,
    version_id_b: int,
) -> Optional[Dict[str, Any]]:
    """Compare two menu versions and return a structured diff.

    Returns None if either version is not found or they belong to
    different menus.

    The ``changes`` list is sorted: modified first, then added,
    then removed, then unchanged.
    """
    va = get_menu_version(version_id_a, include_items=True)
    vb = get_menu_version(version_id_b, include_items=True)
    if va is None or vb is None:
        return None
    if va["menu_id"] != vb["menu_id"]:
        return None

    items_a = va.get("items", [])
    items_b = vb.get("items", [])

    # ---- Match items by normalized name ---------------------
    def _build_lookup(items):
        by_name: Dict[str, List[Dict]] = {}
        for it in items:
            key = _normalize_for_match(it.get("name"))
            by_name.setdefault(key, []).append(it)
        return by_name

    lookup_a = _build_lookup(items_a)
    lookup_b = _build_lookup(items_b)

    # Pair items: unique name match first, then name+category for dupes
    paired: List[tuple] = []  # (item_a, item_b)
    used_a: set = set()
    used_b: set = set()

    all_names = set(lookup_a) | set(lookup_b)
    for name_key in all_names:
        a_list = lookup_a.get(name_key, [])
        b_list = lookup_b.get(name_key, [])

        if len(a_list) == 1 and len(b_list) == 1:
            # Unique match
            paired.append((a_list[0], b_list[0]))
            used_a.add(id(a_list[0]))
            used_b.add(id(b_list[0]))
        elif a_list and b_list:
            # Disambiguate by (name, category)
            b_by_cat: Dict[str, List[Dict]] = {}
            for it in b_list:
                cat_key = _normalize_for_match(it.get("category"))
                b_by_cat.setdefault(cat_key, []).append(it)

            for ia in a_list:
                cat_key = _normalize_for_match(ia.get("category"))
                candidates = b_by_cat.get(cat_key, [])
                match = None
                for c in candidates:
                    if id(c) not in used_b:
                        match = c
                        break
                if match:
                    paired.append((ia, match))
                    used_a.add(id(ia))
                    used_b.add(id(match))

    # Build changes list
    _empty_vc = {"added": [], "removed": [], "modified": [], "unchanged": []}
    changes: List[Dict[str, Any]] = []

    # Matched pairs → modified or unchanged
    for ia, ib in paired:
        fc = _diff_item_fields(ia, ib)
        vc = _diff_variants(
            ia.get("variants", []),
            ib.get("variants", []),
        )
        has_variant_changes = (
            vc["added"] or vc["removed"] or vc["modified"]
        )
        if fc or has_variant_changes:
            status = "modified"
        else:
            status = "unchanged"
        changes.append({
            "status": status,
            "item_a": ia,
            "item_b": ib,
            "field_changes": fc,
            "variant_changes": vc,
        })

    # Unmatched A → removed
    for it in items_a:
        if id(it) not in used_a:
            changes.append({
                "status": "removed",
                "item_a": it,
                "item_b": None,
                "field_changes": [],
                "variant_changes": dict(_empty_vc),
            })

    # Unmatched B → added
    for it in items_b:
        if id(it) not in used_b:
            changes.append({
                "status": "added",
                "item_a": None,
                "item_b": it,
                "field_changes": [],
                "variant_changes": dict(_empty_vc),
            })

    # Sort: modified > added > removed > unchanged
    _status_order = {"modified": 0, "added": 1, "removed": 2, "unchanged": 3}
    changes.sort(key=lambda c: (
        _status_order.get(c["status"], 9),
        (c.get("item_b") or c.get("item_a") or {}).get("position", 0),
    ))

    # Summary
    counts = {"added": 0, "removed": 0, "modified": 0, "unchanged": 0}
    for c in changes:
        counts[c["status"]] = counts.get(c["status"], 0) + 1

    return {
        "version_a": {
            "id": va["id"],
            "version_number": va["version_number"],
            "label": va.get("label"),
            "item_count": va.get("item_count", 0),
            "variant_count": va.get("variant_count", 0),
        },
        "version_b": {
            "id": vb["id"],
            "version_number": vb["version_number"],
            "label": vb.get("label"),
            "item_count": vb.get("item_count", 0),
            "variant_count": vb.get("variant_count", 0),
        },
        "menu_id": va["menu_id"],
        "summary": {
            **counts,
            "total_a": len(items_a),
            "total_b": len(items_b),
        },
        "changes": changes,
    }


# ====================================================================
# Restore version → draft (Day 90)
# ====================================================================

def restore_version_to_draft(version_id: int) -> Optional[Dict[str, Any]]:
    """Create a new draft from a historical menu version.

    Copies all items and variants from the version into a new draft
    in "editing" status, linked to the same menu.

    Returns dict with ``draft_id``, ``version_id``, ``item_count``,
    ``variant_count``, and ``version_label``.  Returns None if the
    version is not found.
    """
    from .drafts import _insert_draft, _insert_items_bulk, insert_variants

    version = get_menu_version(version_id, include_items=True)
    if version is None:
        return None

    menu = get_menu(version["menu_id"])
    restaurant_id = menu["restaurant_id"] if menu else None
    version_label = version.get("label") or f"v{version.get('version_number', '?')}"

    # Create shell draft
    draft_id = _insert_draft(
        title=f"Restored from {version_label}",
        restaurant_id=restaurant_id,
        status="editing",
        source="version_restore",
        menu_id=version["menu_id"],
    )

    items = version.get("items", [])
    item_count = 0
    variant_count = 0

    for it in items:
        inserted_ids = _insert_items_bulk(
            draft_id,
            [
                {
                    "name": it.get("name"),
                    "description": it.get("description"),
                    "price_cents": it.get("price_cents", 0),
                    "category": it.get("category"),
                    "position": it.get("position"),
                }
            ],
        )
        item_count += 1

        variants = it.get("variants") or []
        if inserted_ids and variants:
            new_item_id = inserted_ids[0]
            insert_variants(
                new_item_id,
                [
                    {
                        "label": v.get("label"),
                        "price_cents": v.get("price_cents", 0),
                        "kind": v.get("kind", "size"),
                        "position": v.get("position", 0),
                    }
                    for v in variants
                ],
            )
            variant_count += len(variants)

    return {
        "draft_id": draft_id,
        "version_id": version_id,
        "version_label": version_label,
        "item_count": item_count,
        "variant_count": variant_count,
    }


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
