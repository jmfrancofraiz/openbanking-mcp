from __future__ import annotations

from datetime import datetime, timedelta, timezone

from finmcp.analytics.bills import _cadence, list_bills


def _ago(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def test_cadence_buckets():
    assert _cadence(30) == "mensual"
    assert _cadence(7) == "semanal"
    assert _cadence(14) == "quincenal"
    assert _cadence(90) == "trimestral"
    assert _cadence(365) == "anual"
    assert _cadence(3) == "~3d"


def test_lists_only_transactions_flagged_as_bill(make_tx, session):
    for i in range(3):
        make_tx(9.99, merchant="Netflix", booked_at=_ago(30 * i), is_bill=True)
    make_tx(5.0, merchant="Bar Pepe", booked_at=_ago(0))  # no es recibo

    bills = list_bills(session)

    assert len(bills) == 1
    assert bills[0]["merchant"] == "Netflix"
    assert bills[0]["occurrences"] == 3
    assert bills[0]["cadence"] == "mensual"
    assert bills[0]["amount"] == 9.99


def test_variable_amount_bill_is_still_listed(make_tx, session):
    # A diferencia de la heurística antigua, el importe variable no excluye
    # un recibo si la regla lo marcó como tal (p.ej. consumo eléctrico).
    make_tx(40.0, merchant="Iberdrola", booked_at=_ago(60), is_bill=True)
    make_tx(90.0, merchant="Iberdrola", booked_at=_ago(30), is_bill=True)
    make_tx(55.0, merchant="Iberdrola", booked_at=_ago(0), is_bill=True)

    bills = list_bills(session)

    assert len(bills) == 1
    assert bills[0]["occurrences"] == 3


def test_ignores_non_bill_transactions(make_tx, session):
    make_tx(9.99, merchant="Spotify", booked_at=_ago(0))  # sin is_bill
    assert list_bills(session) == []


def test_single_occurrence_bill_is_listed(make_tx, session):
    # Ya no se exige un mínimo de 2 cargos: el flag es la única fuente de verdad.
    make_tx(50.0, merchant="Seguro Hogar", booked_at=_ago(0), is_bill=True)

    bills = list_bills(session)

    assert len(bills) == 1
    assert bills[0]["cadence"] == "única"
