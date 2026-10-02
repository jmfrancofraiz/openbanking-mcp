from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from finmcp.config import settings
from finmcp.db.models import Base

engine = create_engine(settings.db_url, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _ensure_schema_upgrades() -> None:
    """Añade columnas nuevas a tablas existentes (create_all no las migra)."""
    inspector = inspect(engine)
    if "category_rules" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("category_rules")}
    additions = {
        "amount_min": "ALTER TABLE category_rules ADD COLUMN amount_min FLOAT",
        "amount_max": "ALTER TABLE category_rules ADD COLUMN amount_max FLOAT",
    }
    with engine.begin() as conn:
        for name, ddl in additions.items():
            if name not in columns:
                conn.execute(text(ddl))


def init_db() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    _ensure_schema_upgrades()
