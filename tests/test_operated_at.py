"""Fecha de operación (`operated_at`) frente a fecha contable (`booked_at`).

Los cálculos (resúmenes, gasto, filtros por fecha) usan la de operación; los saldos
del Diario se reconstruyen con la contable, que es la que usa el banco.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine, text

from finmcp.analytics import ledger
from finmcp.analytics.summaries import monthly_summary
from finmcp.db import models, queries, session as db_session
from finmcp.db.repo import upsert_transaction
from finmcp.providers.enablebanking import mapper
from finmcp.providers.types import Transaction as ProviderTx


def _raw(**dates):
    return {
        "entry_reference": "e1",
        "transaction_amount": {"amount": "10.00", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        **dates,
    }


def test_mapper_operacion_primero():
    tx = mapper.to_transaction(_raw(transaction_date="2026-03-29", value_date="2026-03-30",
                                    booking_date="2026-04-01"))
    assert tx.operated_at.date().isoformat() == "2026-03-29"
    assert tx.booked_at.date().isoformat() == "2026-04-01"


def test_mapper_sin_operacion_usa_valor():  # caso BBVA
    tx = mapper.to_transaction(_raw(value_date="2026-03-30", booking_date="2026-04-01"))
    assert tx.operated_at.date().isoformat() == "2026-03-30"
    assert tx.booked_at.date().isoformat() == "2026-04-01"


def test_mapper_solo_contable():
    tx = mapper.to_transaction(_raw(booking_date="2026-04-01"))
    assert tx.operated_at == tx.booked_at


def test_upsert_sin_operated_at_hereda_la_contable(session):
    booked = datetime(2026, 4, 1, tzinfo=timezone.utc)
    upsert_transaction(session, "acc1", ProviderTx(
        provider_id="p1", amount=1.0, currency="EUR", booked_at=booked,
        description="x", type="debit"))
    session.commit()
    t = session.get(models.Transaction, "p1")
    assert t.operated_at.date() == booked.date()


def test_orm_sin_operated_at_hereda_la_contable(session):
    booked = datetime(2026, 4, 1, tzinfo=timezone.utc)
    session.add(models.Transaction(id="o1", account_id="acc1", amount=1.0, currency="EUR",
                                   booked_at=booked, type="debit"))
    session.commit()
    assert session.get(models.Transaction, "o1").operated_at.date() == booked.date()


def test_resumen_y_filtros_por_fecha_de_operacion(make_tx, session):
    # Gasto hecho el 30-mar y contabilizado el 1-abr: cuenta en marzo.
    make_tx(50.0, merchant="Cena", booked_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
            operated_at=datetime(2026, 3, 30, tzinfo=timezone.utc))
    assert monthly_summary(session, 2026, 3)["expense"] == 50.0
    assert monthly_summary(session, 2026, 4)["expense"] == 0.0
    marzo = queries.query_transactions(
        session, start=datetime(2026, 3, 1), end=datetime(2026, 3, 31, 23, 59))
    assert [t.merchant_name for t in marzo] == ["Cena"]


def test_saldos_del_diario_siguen_la_fecha_contable(make_tx, session):
    session.add(models.Balance(account_id="acc1", available=100.0, current=100.0,
                               currency="EUR",
                               snapshot_at=datetime(2026, 4, 2, 6, tzinfo=timezone.utc)))
    session.commit()
    make_tx(50.0, booked_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
            operated_at=datetime(2026, 3, 30, tzinfo=timezone.utc))
    cb = ledger.closing_balances(session, "acc1")
    assert list(cb["closing"]) == ["2026-04-01"]


def test_migracion_rellena_operated_at(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}", future=True)
    with engine.begin() as c:
        c.execute(text("CREATE TABLE transactions (id VARCHAR PRIMARY KEY, booked_at DATETIME,"
                       " raw_json JSON)"))
        rows = [
            ("ing", "2026-04-01 00:00:00.000000",
             '{"transaction_date": "2026-03-29", "booking_date": "2026-04-01"}'),
            ("bbva", "2026-04-01 00:00:00.000000",
             '{"value_date": "2026-03-30", "booking_date": "2026-04-01"}'),
            ("bonusly", "2026-04-01 00:00:00.000000", '{"id": "b"}'),
            ("csv", "2026-04-01 00:00:00.000000", '{"source": "csv"}'),
            ("vacio", "2026-04-01 00:00:00.000000", None),
        ]
        for r in rows:
            c.execute(text("INSERT INTO transactions VALUES (:a, :b, :c)"),
                      {"a": r[0], "b": r[1], "c": r[2]})
    monkeypatch.setattr(db_session, "engine", engine)
    db_session._ensure_schema_upgrades()
    with engine.connect() as c:
        got = dict(c.execute(text("SELECT id, operated_at FROM transactions")).fetchall())
    assert got == {
        "ing": "2026-03-29 00:00:00.000000",
        "bbva": "2026-03-30 00:00:00.000000",
        "bonusly": "2026-04-01 00:00:00.000000",
        "csv": "2026-04-01 00:00:00.000000",
        "vacio": "2026-04-01 00:00:00.000000",
    }
    db_session._ensure_schema_upgrades()  # segunda vez: no hace nada
