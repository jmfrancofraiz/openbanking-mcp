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
        },
        "transactions": {
            "neutral": "BOOLEAN NOT NULL DEFAULT 0",
            "skip_category_rules": "BOOLEAN NOT NULL DEFAULT 0",
            "is_bill": "BOOLEAN NOT NULL DEFAULT 0",
        },
    }
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, cols in additions.items():
            if table not in tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in cols.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def init_db() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    _ensure_schema_upgrades()
