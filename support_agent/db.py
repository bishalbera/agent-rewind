
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

_DEFAULT_PATH = Path(__file__).parent / "data" / "support.db"


def db_path() -> Path:
    return Path(os.getenv("SUPPORT_DB_PATH", str(_DEFAULT_PATH)))


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


# (order_id, customer, item, amount, status, max_refundable)
_SEED: list[tuple[str, str, str, float, str, float]] = [
    ("ORD-1001", "Ada Lovelace", "Mechanical keyboard", 120.00, "delivered", 120.00),
    ("ORD-1002", "Alan Turing", "USB-C hub", 45.50, "shipped", 45.50),
    ("ORD-1003", "Grace Hopper", "Laptop stand", 60.00, "processing", 60.00),
    ("ORD-1004", "Katherine Johnson", "Webcam", 89.99, "delivered", 89.99),
    ("ORD-1005", "Margaret Hamilton", "Noise-cancelling headphones", 250.00, "shipped", 250.00),
    ("ORD-1006", "Dennis Ritchie", "Ergonomic mouse", 39.99, "cancelled", 0.00),
    # Partial-refund rule: restocking fee means only 80% is refundable.
    ("ORD-1007", "Ken Thompson", "4K monitor", 400.00, "delivered", 320.00),
    ("ORD-1008", "Barbara Liskov", "Desk mat", 25.00, "delivered", 25.00),
]


def seed(reset: bool = True) -> None:
    """Create the schema and load the fixed order set."""
    with _connect() as conn:
        if reset:
            conn.executescript("DROP TABLE IF EXISTS orders; DROP TABLE IF EXISTS refunds;")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS orders (
                   order_id TEXT PRIMARY KEY,
                   customer TEXT NOT NULL,
                   item TEXT NOT NULL,
                   amount REAL NOT NULL,
                   status TEXT NOT NULL,
                   max_refundable REAL NOT NULL,
                   refunded REAL NOT NULL DEFAULT 0
               )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS refunds (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   order_id TEXT NOT NULL,
                   amount REAL NOT NULL,
                   created_at TEXT NOT NULL DEFAULT (datetime('now'))
               )"""
        )
        conn.executemany(
            "INSERT OR REPLACE INTO orders "
            "(order_id, customer, item, amount, status, max_refundable) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            _SEED,
        )
        conn.commit()


def get_order(order_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT order_id, customer, item, amount, status, max_refundable, refunded "
            "FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        return dict(row) if row else None


def record_refund(order_id: str, amount: float) -> dict:
    """Apply a refund. Returns a confirmation dict (or an error dict)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT max_refundable, refunded FROM orders WHERE order_id = ?", (order_id,)
        ).fetchone()
        if row is None:
            return {"ok": False, "error": f"order {order_id} not found"}
        remaining = row["max_refundable"] - row["refunded"]
        if amount > remaining + 1e-9:
            return {
                "ok": False,
                "error": f"refund {amount:.2f} exceeds remaining refundable {remaining:.2f}",
                "max_refundable": row["max_refundable"],
                "already_refunded": row["refunded"],
            }
        conn.execute(
            "UPDATE orders SET refunded = refunded + ? WHERE order_id = ?", (amount, order_id)
        )
        conn.execute("INSERT INTO refunds (order_id, amount) VALUES (?, ?)", (order_id, amount))
        conn.commit()
        return {
            "ok": True,
            "order_id": order_id,
            "refunded_amount": round(amount, 2),
            "confirmation": f"REF-{order_id[-4:]}-{int(amount * 100)}",
        }


if __name__ == "__main__":
    seed()
    print(f"seeded {db_path()}")
