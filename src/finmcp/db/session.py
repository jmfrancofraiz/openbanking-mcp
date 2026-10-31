from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from finmcp.config import settings
from finmcp.db.models import Base

engine = create_engine(settings.db_url, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _ensure_schema_upgrades() -> None:
    """Añade columnas nuevas a tablas existentes (create_all no las migra)."""
    additions = {
        "category_rules": {
            "amount_min": "FLOAT",
            "amount_max": "FLOAT",
            "neutral": "BOOLEAN NOT NULL DEFAULT 0",
            "is_bill": "BOOLEAN NOT NULL DEFAULT 0",
            "account_id": "VARCHAR",
        },
        "transactions": {
            "neutral": "BOOLEAN NOT NULL DEFAULT 0",
            "skip_category_rules": "BOOLEAN NOT NULL DEFAULT 0",
            "is_bill": "BOOLEAN NOT NULL DEFAULT 0",
            "operated_at": "DATETIME",
        },
    }
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    added_operated_at = False
    with engine.begin() as conn:
        for table, cols in additions.items():
            if table not in tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in cols.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    if (table, name) == ("transactions", "operated_at"):
                        added_operated_at = True
        if added_operated_at:
            _backfill_operated_at(conn)


def _backfill_operated_at(conn) -> None:
    """Relleno único de `operated_at` al crear la columna (retirar tras aplicarlo).

    Operación → valor → contable desde `raw_json`; si no trae fechas, `booked_at`.
    """
    import json

    from finmcp.providers.enablebanking.mapper import _parse_date

    rows = conn.execute(text("SELECT id, raw_json FROM transactions")).fetchall()
    for tx_id, raw in rows:
        try:
            data = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            data = {}
        value = None
        if isinstance(data, dict):
            value = (data.get("transaction_date") or data.get("value_date")
                     or data.get("booking_date"))
        if value:
            dt = _parse_date(value).replace(tzinfo=None)
            conn.execute(text("UPDATE transactions SET operated_at = :d WHERE id = :i"),
                         {"d": dt.strftime("%Y-%m-%d %H:%M:%S.%f"), "i": tx_id})
    conn.execute(text("UPDATE transactions SET operated_at = booked_at WHERE operated_at IS NULL"))


def init_db() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    _ensure_schema_upgrades()
