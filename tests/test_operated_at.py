"""Fecha de operación (`operated_at`) frente a fecha contable (`booked_at`).

Todo usa la de operación, también el Diario; el saldo del banco (por fecha contable)
se contrastan con las fotos del banco por fecha contable.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from finmcp.analytics import ledger
from finmcp.analytics.summaries import monthly_summary
from finmcp.db import models, queries
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


def test_diario_por_fecha_de_operacion(make_tx, session):
    # Foto del banco del 2-abr: 100 €. Cargo de 50 € hecho el 30-mar, contabilizado el 1-abr;
    # otro de 10 € el 28-mar, sin desfase.
    session.add(models.Balance(account_id="acc1", available=100.0, current=100.0,
                               currency="EUR",
                               snapshot_at=datetime(2026, 4, 2, 6, tzinfo=timezone.utc)))
    session.commit()
    make_tx(50.0, booked_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
            operated_at=datetime(2026, 3, 30, tzinfo=timezone.utc))
    make_tx(10.0, booked_at=datetime(2026, 3, 28, tzinfo=timezone.utc))
    cb = ledger.closing_balances(session, "acc1")
    assert cb["closing"] == {"2026-03-30": 100.0, "2026-03-28": 150.0}

    page = ledger.movements(session, account="acc1")
    rows = page["transactions"]
    assert [(r["date"], r["booked"]) for r in rows] == [("2026-03-30", "2026-04-01"),
                                                         ("2026-03-28", None)]
    # Filtro de fechas del Diario: también por fecha de operación.
    marzo = ledger.movements(session, account="acc1", start=date(2026, 3, 1),
                             end=date(2026, 3, 31))
    assert len(marzo["transactions"]) == 2


def _foto(session, value, when):
    session.add(models.Balance(account_id="acc1", available=value, current=value,
                               currency="EUR", snapshot_at=when))
    session.commit()


def test_foto_cuadra_por_fecha_valor_o_por_contable(make_tx, session):
    utc = timezone.utc
    # Cargo de 50 € con fecha valor 30-mar y contable 1-abr.
    make_tx(50.0, booked_at=datetime(2026, 4, 1, tzinfo=utc),
            operated_at=datetime(2026, 3, 30, tzinfo=utc))
    _foto(session, 150.0, datetime(2026, 3, 29, 20, tzinfo=utc))  # antes del cargo
    _foto(session, 100.0, datetime(2026, 3, 31, 20, tzinfo=utc))  # ya descontado (fecha valor)
    _foto(session, 100.0, datetime(2026, 4, 2, 6, tzinfo=utc))
    check = ledger.closing_balances(session, "acc1")["check"]
    assert check["bad"] == 0 and check["suspect_through"] is None


def test_foto_cuadra_si_el_banco_espera_a_la_contable(make_tx, session):
    utc = timezone.utc
    make_tx(50.0, booked_at=datetime(2026, 4, 1, tzinfo=utc),
            operated_at=datetime(2026, 3, 30, tzinfo=utc))
    _foto(session, 150.0, datetime(2026, 3, 31, 20, tzinfo=utc))  # aún sin descontar
    _foto(session, 100.0, datetime(2026, 4, 2, 6, tzinfo=utc))
    assert ledger.closing_balances(session, "acc1")["check"]["bad"] == 0


def test_descuadre_real_sigue_en_rojo(make_tx, session):
    utc = timezone.utc
    make_tx(50.0, booked_at=datetime(2026, 4, 1, tzinfo=utc),
            operated_at=datetime(2026, 3, 30, tzinfo=utc))
    _foto(session, 137.0, datetime(2026, 3, 31, 20, tzinfo=utc))  # no cuadra con ninguna
    _foto(session, 100.0, datetime(2026, 4, 2, 6, tzinfo=utc))
    check = ledger.closing_balances(session, "acc1")["check"]
    assert check["bad"] == 1 and check["suspect_through"] == "2026-03-31"
    assert check["last_bad"]["calc_day"] == 100.0  # cifra del Diario (fecha de operación)
