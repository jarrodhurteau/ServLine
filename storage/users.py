"""
storage/users.py — User Accounts & Authentication (Phase 13, Day 126)

Tables:
  users              — email/password accounts
  user_restaurants   — many-to-many association (with role)

All CRUD goes through this module.  portal/app.py imports it as users_store.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from typing import Optional, Dict, Any, List

from werkzeug.security import generate_password_hash, check_password_hash

from storage.drafts import db_connect, _now


# -------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------
VALID_ROLES = frozenset({"owner", "manager", "staff"})
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8


# -------------------------------------------------------------------
# Schema (idempotent)
# -------------------------------------------------------------------
def _ensure_users_schema() -> None:
    with db_connect() as conn:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                email           TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash   TEXT NOT NULL,
                display_name    TEXT,
                email_verified  INTEGER NOT NULL DEFAULT 0,
                active          INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_restaurants (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                restaurant_id   INTEGER NOT NULL,
                role            TEXT NOT NULL DEFAULT 'owner',
                created_at      TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE CASCADE,
                UNIQUE(user_id, restaurant_id)
            )
        """)

        conn.commit()


# -------------------------------------------------------------------
# Validation helpers
# -------------------------------------------------------------------
def validate_email(email: str) -> Optional[str]:
    """Return error message or None if valid."""
    if not email or not email.strip():
        return "Email is required"
    if not EMAIL_RE.match(email.strip()):
        return "Invalid email format"
    return None


def validate_password(password: str) -> Optional[str]:
    """Return error message or None if valid."""
    if not password:
        return "Password is required"
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    return None


# -------------------------------------------------------------------
# User CRUD
# -------------------------------------------------------------------
def create_user(email: str, password: str,
                display_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Create a new user account.  Returns {"id": int, "email": str, ...}.
    Raises ValueError on validation failure or duplicate email.
    """
    email = email.strip().lower()
    err = validate_email(email)
    if err:
        raise ValueError(err)
    err = validate_password(password)
    if err:
        raise ValueError(err)

    now = _now()
    pw_hash = generate_password_hash(password)

    with db_connect() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO users (email, password_hash, display_name,
                                      created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (email, pw_hash, display_name, now, now),
            )
            conn.commit()
            return {
                "id": cur.lastrowid,
                "email": email,
                "display_name": display_name,
                "email_verified": False,
                "active": True,
                "created_at": now,
            }
        except sqlite3.IntegrityError:
            raise ValueError("An account with this email already exists")


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Look up a user by email (case-insensitive). Returns dict or None."""
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ? COLLATE NOCASE",
            (email.strip().lower(),),
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    """Look up a user by id. Returns dict or None."""
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def verify_password(email: str, password: str) -> Optional[Dict[str, Any]]:
    """
    Authenticate a user.  Returns user dict on success, None on failure.
    Never reveals whether the email exists (constant-ish timing not guaranteed,
    but functional behaviour is safe).
    """
    user = get_user_by_email(email)
    if user is None:
        # run hash anyway to avoid trivial timing leak
        generate_password_hash("dummy")
        return None
    if not user["active"]:
        return None
    if check_password_hash(user["password_hash"], password):
        return user
    return None


def update_user(user_id: int, **fields) -> bool:
    """
    Update user fields.  Accepted keys: display_name, email_verified, active.
    Returns True if a row was updated.
    """
    allowed = {"display_name", "email_verified", "active"}
    to_set = {k: v for k, v in fields.items() if k in allowed}
    if not to_set:
        return False
    to_set["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in to_set)
    vals = list(to_set.values()) + [user_id]
    with db_connect() as conn:
        n = conn.execute(
            f"UPDATE users SET {set_clause} WHERE id = ?", vals
        ).rowcount
        conn.commit()
    return n > 0


def change_password(user_id: int, new_password: str) -> bool:
    """Change a user's password. Returns True on success."""
    err = validate_password(new_password)
    if err:
        raise ValueError(err)
    now = _now()
    pw_hash = generate_password_hash(new_password)
    with db_connect() as conn:
        n = conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (pw_hash, now, user_id),
        ).rowcount
        conn.commit()
    return n > 0


def deactivate_user(user_id: int) -> bool:
    """Soft-delete a user (sets active=0)."""
    return update_user(user_id, active=0)


def list_users(active_only: bool = True) -> List[Dict[str, Any]]:
    """Return all users (optionally only active)."""
    q = "SELECT id, email, display_name, email_verified, active, created_at FROM users"
    if active_only:
        q += " WHERE active = 1"
    q += " ORDER BY id"
    with db_connect() as conn:
        rows = conn.execute(q).fetchall()
    return [dict(r) for r in rows]


# -------------------------------------------------------------------
# User ↔ Restaurant association
# -------------------------------------------------------------------
def link_user_restaurant(user_id: int, restaurant_id: int,
                         role: str = "owner") -> Dict[str, Any]:
    """
    Associate a user with a restaurant.
    Raises ValueError on invalid role or duplicate link.
    """
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role '{role}'. Must be one of: {', '.join(sorted(VALID_ROLES))}")
    now = _now()
    with db_connect() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO user_restaurants (user_id, restaurant_id, role, created_at)
                   VALUES (?, ?, ?, ?)""",
                (user_id, restaurant_id, role, now),
            )
            conn.commit()
            return {"id": cur.lastrowid, "user_id": user_id,
                    "restaurant_id": restaurant_id, "role": role}
        except sqlite3.IntegrityError:
            raise ValueError("User is already linked to this restaurant")


def unlink_user_restaurant(user_id: int, restaurant_id: int) -> bool:
    """Remove a user↔restaurant link. Returns True if a row was deleted."""
    with db_connect() as conn:
        n = conn.execute(
            "DELETE FROM user_restaurants WHERE user_id = ? AND restaurant_id = ?",
            (user_id, restaurant_id),
        ).rowcount
        conn.commit()
    return n > 0


def get_user_restaurants(user_id: int) -> List[Dict[str, Any]]:
    """Return all restaurants a user is linked to (with role)."""
    with db_connect() as conn:
        rows = conn.execute(
            """SELECT ur.restaurant_id, ur.role, r.name AS restaurant_name
               FROM user_restaurants ur
               JOIN restaurants r ON r.id = ur.restaurant_id AND r.active = 1
               WHERE ur.user_id = ?
               ORDER BY ur.restaurant_id""",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_restaurant_users(restaurant_id: int) -> List[Dict[str, Any]]:
    """Return all users linked to a restaurant (with role)."""
    with db_connect() as conn:
        rows = conn.execute(
            """SELECT ur.user_id, ur.role, u.email, u.display_name
               FROM user_restaurants ur
               JOIN users u ON u.id = ur.user_id AND u.active = 1
               WHERE ur.restaurant_id = ?
               ORDER BY ur.user_id""",
            (restaurant_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def user_owns_restaurant(user_id: int, restaurant_id: int) -> bool:
    """Check if a user has any role on a restaurant."""
    with db_connect() as conn:
        row = conn.execute(
            """SELECT 1 FROM user_restaurants
               WHERE user_id = ? AND restaurant_id = ?""",
            (user_id, restaurant_id),
        ).fetchone()
    return row is not None


def update_user_role(user_id: int, restaurant_id: int, role: str) -> bool:
    """Change a user's role on a restaurant."""
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role '{role}'")
    with db_connect() as conn:
        n = conn.execute(
            """UPDATE user_restaurants SET role = ?
               WHERE user_id = ? AND restaurant_id = ?""",
            (role, user_id, restaurant_id),
        ).rowcount
        conn.commit()
    return n > 0