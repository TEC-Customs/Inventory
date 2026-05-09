"""SQLite storage for the flip-inventory tracker."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode       TEXT,
    sku           TEXT UNIQUE,
    title         TEXT NOT NULL,
    description   TEXT,
    brand         TEXT,
    model         TEXT,
    category      TEXT,
    condition     TEXT DEFAULT 'Used',
    cost          REAL DEFAULT 0,
    price         REAL DEFAULT 0,
    quantity      INTEGER DEFAULT 1,
    weight_oz     REAL,
    location      TEXT,
    notes         TEXT,
    image_url     TEXT,
    image_path    TEXT,
    source        TEXT,
    status        TEXT DEFAULT 'In Stock',
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT,
    sold_at       TEXT,
    sold_price    REAL
);

CREATE INDEX IF NOT EXISTS idx_items_barcode ON items(barcode);
CREATE INDEX IF NOT EXISTS idx_items_status  ON items(status);
"""

EDITABLE_FIELDS = (
    "barcode", "sku", "title", "description", "brand", "model", "category",
    "condition", "cost", "price", "quantity", "weight_oz", "location",
    "notes", "image_url", "image_path", "source", "status",
)


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def add_item(self, data: dict[str, Any]) -> int:
        fields = [k for k in EDITABLE_FIELDS if k in data]
        placeholders = ",".join("?" for _ in fields)
        cols = ",".join(fields)
        values = [data[k] for k in fields]
        with self._connect() as conn:
            cur = conn.execute(
                f"INSERT INTO items ({cols}) VALUES ({placeholders})",
                values,
            )
            new_id = cur.lastrowid
            if not data.get("sku"):
                sku = f"FLIP-{new_id:05d}"
                conn.execute("UPDATE items SET sku = ? WHERE id = ?", (sku, new_id))
            return new_id

    def update_item(self, item_id: int, data: dict[str, Any]) -> None:
        fields = [k for k in EDITABLE_FIELDS if k in data]
        if not fields:
            return
        assignments = ",".join(f"{k} = ?" for k in fields)
        values = [data[k] for k in fields]
        values.append(datetime.utcnow().isoformat(timespec="seconds"))
        values.append(item_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE items SET {assignments}, updated_at = ? WHERE id = ?",
                values,
            )

    def mark_sold(self, item_id: int, sold_price: float) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE items
                   SET status = 'Sold', sold_price = ?, sold_at = datetime('now')
                   WHERE id = ?""",
                (sold_price, item_id),
            )

    def delete_item(self, item_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM items WHERE id = ?", (item_id,))

    def get_item(self, item_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            return dict(row) if row else None

    def find_by_barcode(self, barcode: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM items WHERE barcode = ? ORDER BY id DESC LIMIT 1",
                (barcode,),
            ).fetchone()
            return dict(row) if row else None

    def list_items(
        self,
        search: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM items WHERE 1=1"
        params: list[Any] = []
        if search:
            sql += " AND (title LIKE ? OR brand LIKE ? OR barcode LIKE ? OR sku LIKE ?)"
            like = f"%{search}%"
            params.extend([like, like, like, like])
        if status and status != "All":
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def all_for_export(self, ids: Iterable[int] | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if ids:
                ids = list(ids)
                placeholders = ",".join("?" for _ in ids)
                rows = conn.execute(
                    f"SELECT * FROM items WHERE id IN ({placeholders})", ids,
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM items").fetchall()
            return [dict(r) for r in rows]
