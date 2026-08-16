"""Short-id lookup for saved cards, backed by SQLite.

This is a cache of lookups, not a source of truth: every row's share_code
is itself a fully self-contained card (see momir/codec.py), so the whole
database could be deleted and no card already decoded from its share_code
becomes unreconstructible -- the only thing lost is the short id mapping.

The generator/corpus are trained fresh at process start and never touch
disk for state, so this is the one piece of the project that persists
anything across runs. Kept to a single table, stdlib-only.
"""
from __future__ import annotations

import contextlib
import datetime
import hashlib
import pathlib
import sqlite3

from .models import Card

DB_PATH = pathlib.Path(__file__).parent.parent / "data" / "saved_cards.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS saved_cards (
    id          TEXT PRIMARY KEY,
    share_code  TEXT NOT NULL,
    name        TEXT NOT NULL,
    mana_value  INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);
"""


@contextlib.contextmanager
def _connect():
    # A fresh connection per call rather than one shared, long-lived
    # connection -- FastAPI runs sync routes in a thread pool, so requests
    # can genuinely overlap, and SQLite's own file locking (with WAL mode)
    # handles that correctly without any locking of our own to get wrong.
    # The DB file is small and local, so the per-call open/close cost is
    # negligible -- same tradeoff the generator already makes by being
    # cheap enough to run fresh on every request.
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create the DB file/table if they don't exist yet. Safe to call every
    startup -- see api.py, called next to the eager get_generator()."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(SCHEMA)


def _card_id(share_code: str) -> str:
    # Content-addressed rather than random: saving the same card twice
    # (two people sharing the same generated card, or one person re-sharing)
    # naturally lands on the same id, so INSERT OR IGNORE below is all the
    # de-duplication needed -- no check-then-insert race to worry about.
    return hashlib.sha256(share_code.encode("utf-8")).hexdigest()[:10]


def save_card(card: Card) -> str:
    """Persist a card's share_code under a short id, returning that id."""
    if card.share_code is None:
        raise ValueError("card has no share_code to save")
    card_id = _card_id(card.share_code)
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO saved_cards (id, share_code, name, mana_value, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                card_id,
                card.share_code,
                card.name,
                card.mana_value,
                datetime.datetime.now(datetime.UTC).isoformat(),
            ),
        )
    return card_id


def get_share_code(card_id: str) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT share_code FROM saved_cards WHERE id = ?", (card_id,)
        ).fetchone()
    return row[0] if row else None
