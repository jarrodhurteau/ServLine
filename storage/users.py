"""
storage/users.py — User Accounts & Authentication (Phase 13, Days 126-130)

Tables:
  users                      — email/password accounts
  user_restaurants            — many-to-many association (with role)
  email_verification_tokens   — token-based email verification (Day 130)
  password_reset_tokens       — token-based password reset (Day 130)

All CRUD goes through this module.  portal/app.py imports it as users_store.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from werkzeug.security import generate_password_hash, check_password_hash

from storage.drafts import db_connect, _now


# -------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------
VALID_ROLES = frozenset({"owner", "manager", "staff"})
VALID_TIERS = frozenset({"free", "lightning"})
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

        # Day 130: Token tables for email verification & password reset
        cur.execute("""
            CREATE TABLE IF NOT EXISTS email_verification_tokens (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                token_hash  TEXT NOT NULL UNIQUE,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                token_hash  TEXT NOT NULL UNIQUE,
                expires_at  TEXT NOT NULL,
                used        INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        conn.commit()


# -------------------------------------------------------------------
# Token helpers (Day 130)
# -------------------------------------------------------------------
TOKEN_BYTES = 32
RESET_TOKEN_HOURS = 1


def _hash_token(token: str) -> str:
    """SHA-256 hash a raw token for safe storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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


def delete_user(user_id: int) -> bool:
    """Hard-delete a user and all associated data so the email can be reused."""
    with db_connect() as conn:
        conn.execute("DELETE FROM email_verification_tokens WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM password_reset_tokens WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_restaurants WHERE user_id = ?", (user_id,))
        n = conn.execute("DELETE FROM users WHERE id = ?", (user_id,)).rowcount
        conn.commit()
    return n > 0


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


# -------------------------------------------------------------------
# Restaurant management (Day 128)
# -------------------------------------------------------------------
VALID_CUISINE_TYPES = frozenset({
    "american", "italian", "mexican", "chinese", "japanese", "thai",
    "indian", "mediterranean", "french", "korean", "vietnamese",
    "greek", "caribbean", "bbq", "seafood", "pizza", "burger",
    "deli", "bakery", "cafe", "bar", "other",
})


def get_restaurant(restaurant_id: int) -> Optional[Dict[str, Any]]:
    """Return a single active restaurant by id, or None."""
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM restaurants WHERE id = ? AND active = 1",
            (restaurant_id,),
        ).fetchone()
    return dict(row) if row else None


def update_restaurant(restaurant_id: int, **fields) -> bool:
    """
    Update restaurant fields.  Accepted: name, phone, address,
    cuisine_type, website.  Returns True if a row was updated.
    Raises ValueError if name is blank.
    """
    allowed = {"name", "phone", "address", "cuisine_type", "website"}
    to_set = {k: v for k, v in fields.items() if k in allowed}
    if not to_set:
        return False
    # Name must not be empty if provided
    if "name" in to_set:
        n = (to_set["name"] or "").strip()
        if not n:
            raise ValueError("Restaurant name cannot be empty")
        to_set["name"] = n
    # Sanitize optional text fields
    for fld in ("phone", "address", "website"):
        if fld in to_set:
            to_set[fld] = (to_set[fld] or "").strip() or None
    # Validate cuisine_type
    if "cuisine_type" in to_set:
        ct = (to_set["cuisine_type"] or "").strip().lower() or None
        if ct and ct not in VALID_CUISINE_TYPES:
            ct = "other"
        to_set["cuisine_type"] = ct
    to_set["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in to_set)
    vals = list(to_set.values()) + [restaurant_id]
    with db_connect() as conn:
        n = conn.execute(
            f"UPDATE restaurants SET {set_clause} WHERE id = ? AND active = 1",
            vals,
        ).rowcount
        conn.commit()
    return n > 0


def delete_restaurant(restaurant_id: int) -> bool:
    """Soft-delete a restaurant (sets active=0)."""
    with db_connect() as conn:
        n = conn.execute(
            "UPDATE restaurants SET active = 0, updated_at = ? WHERE id = ? AND active = 1",
            (_now(), restaurant_id),
        ).rowcount
        conn.commit()
    return n > 0


def get_restaurant_stats(restaurant_id: int) -> Dict[str, int]:
    """Return draft_count, menu_count, and item_count for a restaurant."""
    with db_connect() as conn:
        draft_count = conn.execute(
            "SELECT COUNT(*) FROM drafts WHERE restaurant_id = ?",
            (restaurant_id,),
        ).fetchone()[0]
        menu_count = conn.execute(
            "SELECT COUNT(*) FROM menus WHERE restaurant_id = ? AND active = 1",
            (restaurant_id,),
        ).fetchone()[0]
        item_count = conn.execute(
            """SELECT COUNT(*) FROM draft_items di
               JOIN drafts d ON d.id = di.draft_id
               WHERE d.restaurant_id = ?""",
            (restaurant_id,),
        ).fetchone()[0]
    return {
        "draft_count": draft_count,
        "menu_count": menu_count,
        "item_count": item_count,
    }


def _ensure_restaurant_columns() -> None:
    """Add cuisine_type, website, updated_at columns if missing (idempotent)."""
    with db_connect() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(restaurants)").fetchall()}
        if "cuisine_type" not in cols:
            conn.execute("ALTER TABLE restaurants ADD COLUMN cuisine_type TEXT")
        if "website" not in cols:
            conn.execute("ALTER TABLE restaurants ADD COLUMN website TEXT")
        if "updated_at" not in cols:
            conn.execute("ALTER TABLE restaurants ADD COLUMN updated_at TEXT")
        conn.commit()


def _ensure_tier_column() -> None:
    """Add account_tier column to users table if missing (idempotent, Day 131)."""
    with db_connect() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "account_tier" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN account_tier TEXT")
        conn.commit()


# -------------------------------------------------------------------
# Email Verification (Day 130)
# -------------------------------------------------------------------
def generate_verification_token(user_id: int) -> str:
    """Create a verification token for the user.  Returns the raw token.

    Any previous tokens for this user are deleted first (one active at a time).
    """
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    hashed = _hash_token(raw)
    now = _now()
    with db_connect() as conn:
        conn.execute("DELETE FROM email_verification_tokens WHERE user_id = ?",
                      (user_id,))
        conn.execute(
            """INSERT INTO email_verification_tokens (user_id, token_hash, created_at)
               VALUES (?, ?, ?)""",
            (user_id, hashed, now),
        )
        conn.commit()
    return raw


def verify_email_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify an email token.  Returns the user dict on success, None on failure.

    On success the token row is deleted and the user's email_verified is set to 1.
    """
    hashed = _hash_token(token)
    with db_connect() as conn:
        row = conn.execute(
            "SELECT user_id FROM email_verification_tokens WHERE token_hash = ?",
            (hashed,),
        ).fetchone()
        if not row:
            return None
        uid = row[0]
        conn.execute("DELETE FROM email_verification_tokens WHERE token_hash = ?",
                      (hashed,))
        conn.execute(
            "UPDATE users SET email_verified = 1, updated_at = ? WHERE id = ?",
            (_now(), uid),
        )
        conn.commit()
    return get_user_by_id(uid)


def get_verification_token_for_user(user_id: int) -> Optional[str]:
    """Return the token_hash for the user, if one exists (for testing/resend)."""
    with db_connect() as conn:
        row = conn.execute(
            "SELECT token_hash FROM email_verification_tokens WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return row[0] if row else None


# -------------------------------------------------------------------
# Password Reset (Day 130)
# -------------------------------------------------------------------
def generate_reset_token(email: str) -> Optional[str]:
    """Create a password-reset token.  Returns the raw token, or None if the
    email doesn't match any active user (caller should not reveal this).
    """
    user = get_user_by_email(email)
    if not user or not user["active"]:
        return None
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    hashed = _hash_token(raw)
    now = _now()
    expires = (datetime.fromisoformat(now) + timedelta(hours=RESET_TOKEN_HOURS)).isoformat(sep=" ", timespec="seconds")
    with db_connect() as conn:
        # Invalidate any existing tokens for this user
        conn.execute("DELETE FROM password_reset_tokens WHERE user_id = ?",
                      (user["id"],))
        conn.execute(
            """INSERT INTO password_reset_tokens
               (user_id, token_hash, expires_at, created_at)
               VALUES (?, ?, ?, ?)""",
            (user["id"], hashed, expires, now),
        )
        conn.commit()
    return raw


def validate_reset_token(token: str) -> Optional[int]:
    """Check a reset token.  Returns user_id if valid and not expired, else None."""
    hashed = _hash_token(token)
    now = _now()
    with db_connect() as conn:
        row = conn.execute(
            """SELECT user_id, expires_at, used
               FROM password_reset_tokens
               WHERE token_hash = ?""",
            (hashed,),
        ).fetchone()
    if not row:
        return None
    if row["used"]:
        return None
    if row["expires_at"] < now:
        return None
    return row["user_id"]


def consume_reset_token(token: str, new_password: str) -> bool:
    """Use a reset token to change the user's password.

    Validates the token, changes the password, marks the token as used.
    Returns True on success, False on invalid/expired token.
    Raises ValueError if the new password fails validation.
    """
    err = validate_password(new_password)
    if err:
        raise ValueError(err)
    hashed = _hash_token(token)
    now = _now()
    with db_connect() as conn:
        row = conn.execute(
            """SELECT user_id, expires_at, used
               FROM password_reset_tokens
               WHERE token_hash = ?""",
            (hashed,),
        ).fetchone()
        if not row or row["used"] or row["expires_at"] < now:
            return False
        uid = row["user_id"]
        pw_hash = generate_password_hash(new_password)
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (pw_hash, now, uid),
        )
        conn.execute(
            "UPDATE password_reset_tokens SET used = 1 WHERE token_hash = ?",
            (hashed,),
        )
        conn.commit()
    return True


# -------------------------------------------------------------------
# Account Tier (Day 131)
# -------------------------------------------------------------------
def set_user_tier(user_id: int, tier: str) -> bool:
    """Set the account tier for a user.  Returns True on success.

    Valid tiers: 'free', 'lightning'.
    Raises ValueError on invalid tier.
    """
    tier = (tier or "").strip().lower()
    if tier not in VALID_TIERS:
        raise ValueError(f"Invalid tier '{tier}'. Must be one of: {', '.join(sorted(VALID_TIERS))}")
    now = _now()
    with db_connect() as conn:
        n = conn.execute(
            "UPDATE users SET account_tier = ?, updated_at = ? WHERE id = ?",
            (tier, now, user_id),
        ).rowcount
        conn.commit()
    return n > 0


def get_user_tier(user_id: int) -> Optional[str]:
    """Return the account tier for a user, or None if not set."""
    with db_connect() as conn:
        row = conn.execute(
            "SELECT account_tier FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    if not row:
        return None
    return row["account_tier"] if row["account_tier"] else None


def check_feature_access(user_id: int, feature: str) -> bool:
    """Check if a user has access to a feature based on their tier.

    Features:
      'editor'          — free + lightning
      'save_menus'      — free + lightning
      'csv_json_import' — free + lightning
      'csv_json_export' — free + lightning
      'pos_export'      — free ($10) + lightning (first free)
      'ai_parse'        — lightning only
      'ocr_upload'      — lightning only
      'wizard'          — lightning only
    """
    FREE_FEATURES = {"editor", "save_menus", "csv_json_import", "csv_json_export", "pos_export"}
    LIGHTNING_FEATURES = FREE_FEATURES | {"ai_parse", "ocr_upload", "wizard"}
    tier = get_user_tier(user_id)
    if tier == "lightning":
        return feature in LIGHTNING_FEATURES
    # free tier (default for anyone with tier set)
    if tier == "free":
        return feature in FREE_FEATURES
    # No tier chosen yet — no gated features
    return False