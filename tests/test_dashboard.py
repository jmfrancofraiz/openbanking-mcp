from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from finmcp.analytics import dashboard as d
from finmcp.analytics.summaries import monthly_summary
from finmcp.db.models import Account, SyncRun, Tag, TransactionTag

TODAY = date(2026, 4, 10)


def at(y: int, m: int, day: int) -> datetime:
    return datetime(y, m, day, tzinfo=timezone.utc)


@pytest.fixture
def ejemplo(make_tx):
    """Ejemplo de la especificación del panel (§10.1): hoy es el 10 de abril."""
    make_tx(120.00, merchant="Supermercado", booked_at=at(2026, 3, 5),
            my_category="Alimentación - Supermercado")
    make_tx(80.00, merchant="Tienda de ropa", booked_at=at(2026, 3, 12),
            my_category="Moda y complementos")
    make_tx(30.00, type="credit", merchant="Devolución tienda de ropa",
            booked_at=at(2026, 3, 15), my_category="Moda y complementos")
    make_tx(500.00, description="Traspaso a cuenta de ahorro", booked_at=at(2026, 3, 20),
            my_category="Traspasos internos", neutral=True)
    make_tx(900.00, description="Cuota hipoteca", booked_at=at(2026, 3, 27),
            my_category="Vivienda - Hipoteca", is_bill=True)
    make_tx(3000.00, type="credit", description="Nómina", booked_at=at(2026, 3, 28),
            my_category="Ingresos - Nómina")
    make_tx(60.00, merchant="Supermercado", booked_at=at(2026, 4, 2),
            my_category="Alimentación - Supermercado")
    make_tx(40.00, merchant="Restaurante", booked_at=at(2026, 4, 8),
            my_category="Restauración")


# --- clasificación ---------------------------------------------------------


@pytest.mark.parametrize(
    "category, income",
    [
        ("Ingresos - Nómina", True),
        ("Ingresos", True),
        ("Otros ingresos", True),
        ("Intereses", True),
        ("Vivienda - Hipoteca", False),
        ("Sin categoría", False),
    ],
)
def test_is_income_category(category, income):
    assert d.is_income_category(category) is income


@pytest.mark.parametrize(
    "category, group, label",
    [
        ("Vivienda - Hipoteca", "Vivienda", "Hipoteca"),
        ("Restauración", "Restauración", "General"),
        ("Ocio - Cine - Estrenos", "Ocio", "Cine - Estrenos"),
        ("Ingresos - Nómina", "Ingresos - Nómina", "General"),
        ("Otros ingresos", "Otros ingresos", "General"),
    ],
)
def test_group_and_subcategory_label(category, group, label):
    assert d.group_of(category) == group
    assert d.subcategory_label(category) == label


# --- breakdown ---------------------------------------------------------------


def test_breakdown_resta_devoluciones_y_excluye_neutrales(session, ejemplo):
    b = d.breakdown(session, date(2026, 3, 1), date(2026, 3, 31))

    assert b["from"] == "2026-03-01" and b["to"] == "2026-03-31"
    assert b["income"]["total"] == 3000.00
    assert b["expense"]["total"] == 1070.00
    assert b["net"] == 1930.00
    assert b["refunds"] == 30.00
    assert b["bills"] == 900.00
    assert b["count"] == 5  # el traspaso neutral no computa

    groups = [(g["group"], g["total"], g["count"]) for g in b["expense"]["groups"]]
    assert groups == [
        ("Vivienda", 900.0, 1),
        ("Alimentación", 120.0, 1),
        ("Moda y complementos", 50.0, 2),
    ]
    assert b["expense"]["groups"][0]["categories"] == [
        {"category": "Vivienda - Hipoteca", "label": "Hipoteca", "total": 900.0, "count": 1}
    ]
    assert b["expense"]["groups"][2]["categories"] == [
        {"category": "Moda y complementos", "label": "General", "total": 50.0, "count": 2}
    ]
    assert [g["group"] for g in b["income"]["groups"]] == ["Ingresos - Nómina"]
    assert b["income"]["count"] == 1 and b["expense"]["count"] == 4

    assert b["neutral"]["total"] == -500.00
    assert b["neutral"]["count"] == 1
    assert [g["group"] for g in b["neutral"]["groups"]] == ["Traspasos internos"]


def test_breakdown_cuadra_con_monthly_summary(session, ejemplo):
    b = d.breakdown(session, date(2026, 3, 1), date(2026, 3, 31))
    s = monthly_summary(session, 2026, 3)

    assert b["net"] == s["net"]
    # Bruto del CLI = gasto neto + devoluciones (no hay cargos en categorías de ingreso).
    assert s["expense"] == round(b["expense"]["total"] + b["refunds"], 2)


def test_breakdown_por_rango_de_dias(session, ejemplo):
    b = d.breakdown(session, date(2026, 3, 1), date(2026, 3, 10))

    assert b["expense"]["total"] == 120.0
    assert [g["group"] for g in b["expense"]["groups"]] == ["Alimentación"]
    assert b["income"] == {"total": 0.0, "count": 0, "groups": []}


def test_breakdown_categoria_con_saldo_negativo_queda_al_final(session, make_tx):
    make_tx(20.0, booked_at=at(2026, 3, 2), my_category="Ocio")
    make_tx(50.0, type="credit", booked_at=at(2026, 3, 3), my_category="Moda")
    make_tx(10.0, booked_at=at(2026, 3, 4), my_category="Moda")

    b = d.breakdown(session, date(2026, 3, 1), date(2026, 3, 31))

    assert [(g["group"], g["total"]) for g in b["expense"]["groups"]] == [
        ("Ocio", 20.0),
        ("Moda", -40.0),
    ]
    assert b["expense"]["total"] == -20.0


def test_cargo_en_categoria_de_ingreso_resta_ingresos(session, make_tx):
    make_tx(1000.0, type="credit", booked_at=at(2026, 3, 1), my_category="Ingresos - Nómina")
    make_tx(100.0, booked_at=at(2026, 3, 2), my_category="Ingresos - Nómina")

    b = d.breakdown(session, date(2026, 3, 1), date(2026, 3, 31))

    assert b["income"]["total"] == 900.0
    assert b["expense"]["total"] == 0.0
    assert b["net"] == 900.0


# --- monthly -----------------------------------------------------------------


def test_monthly_desde_el_primer_mes_hasta_el_actual(session, ejemplo):
    rows = d.monthly(session, today=TODAY)

    assert [r["month"] for r in rows] == ["2026-03", "2026-04"]
    mar, abr = rows
    assert (mar["income"], mar["expense"], mar["net"]) == (3000.0, 1070.0, 1930.0)
    assert (mar["bills"], mar["refunds"], mar["count"]) == (900.0, 30.0, 5)
    assert mar["partial"] is False and mar["cut_day"] is None
    assert (abr["income"], abr["expense"], abr["net"]) == (0.0, 100.0, -100.0)
    assert abr["partial"] is True


def test_monthly_a_dia_d_recorta_cada_mes(session, ejemplo):
    mar, abr = d.monthly(session, today=TODAY, day=10)

    assert (mar["income"], mar["expense"], mar["count"]) == (0.0, 120.0, 1)
    assert mar["cut_day"] == 10
    assert abr["expense"] == 100.0


def test_monthly_dia_de_corte_en_mes_corto(session, make_tx):
    make_tx(10.0, booked_at=at(2026, 2, 28), my_category="Ocio")

    feb = d.monthly(session, today=date(2026, 3, 31), day=31)[0]

    assert feb["expense"] == 10.0 and feb["cut_day"] == 28


def test_monthly_incluye_meses_sin_movimientos(session, make_tx):
    make_tx(10.0, booked_at=at(2026, 1, 5), my_category="Ocio")

    rows = d.monthly(session, today=date(2026, 3, 2))

    assert [r["month"] for r in rows] == ["2026-01", "2026-02", "2026-03"]
    assert rows[1]["expense"] == 0.0 and rows[1]["count"] == 0


def test_monthly_sin_datos_devuelve_el_mes_actual(session):
    rows = d.monthly(session, today=TODAY)

    assert [r["month"] for r in rows] == ["2026-04"]
    assert rows[0]["net"] == 0.0


# --- series ------------------------------------------------------------------


def test_series_por_categoria_y_mes(session, ejemplo):
    s = d.series(session, "2026-02", "2026-04")

    assert s["months"] == ["2026-02", "2026-03", "2026-04"]
    assert s["expense"]["Alimentación"] == [0.0, 120.0, 60.0]
    assert s["expense"]["Moda y complementos"] == [0.0, 50.0, 0.0]
    assert s["expense"]["Restauración"] == [0.0, 0.0, 40.0]
    assert s["income"] == {"Ingresos - Nómina": [0.0, 3000.0, 0.0]}
    assert "Traspasos internos" not in s["expense"]


def test_series_incluye_neutral(session, ejemplo):
    s = d.series(session, "2026-03", "2026-04")

    assert s["neutral"]["Traspasos internos"] == [-500.00, 0.0]


# --- transactions ------------------------------------------------------------


def test_transactions_de_una_categoria_ordenadas_por_fecha(session, ejemplo):
    rows = d.transactions(session, date(2026, 3, 1), date(2026, 3, 31), d.EXPENSE,
                          group="Moda y complementos")

    assert [(r["date"], r["amount"]) for r in rows] == [
        ("2026-03-12", -80.0),
        ("2026-03-15", 30.0),
    ]
    first = rows[0]
    assert first["category"] == "Moda y complementos"
    assert first["group"] == "Moda y complementos" and first["label"] == "General"
    assert first["merchant"] == "Tienda de ropa"
    assert first["bank"] == "default" and first["tags"] == [] and first["is_bill"] is False
    assert set(first) == {"id", "date", "amount", "category", "group", "label", "merchant",
                          "description", "bank", "tags", "is_bill"}


def test_transactions_por_subcategoria_y_por_tipo(session, ejemplo):
    super_rows = d.transactions(session, date(2026, 3, 1), date(2026, 4, 30), d.EXPENSE,
                                category="Alimentación - Supermercado")
    income_rows = d.transactions(session, date(2026, 3, 1), date(2026, 3, 31), d.INCOME)
    expense_rows = d.transactions(session, date(2026, 3, 1), date(2026, 3, 31), d.EXPENSE)

    assert [r["date"] for r in super_rows] == ["2026-03-05", "2026-04-02"]
    assert [(r["category"], r["amount"]) for r in income_rows] == [("Ingresos - Nómina", 3000.0)]
    assert len(expense_rows) == 4
    assert all(r["category"] != "Traspasos internos" for r in expense_rows)


def test_transactions_neutral(session, ejemplo):
    rows = d.transactions(session, date(2026, 3, 1), date(2026, 3, 31), d.NEUTRAL)

    assert len(rows) == 1
    assert rows[0]["category"] == "Traspasos internos"
    assert rows[0]["amount"] == -500.00


def test_transactions_incluye_etiquetas_activas_y_banco(session, make_tx):
    session.add(Account(id="acc2", bank_id="Banco X", name="X", type="TRANSACTION",
                        currency="EUR"))
    session.commit()
    tx = make_tx(15.0, booked_at=at(2026, 3, 3), my_category="Ocio", account_id="acc2")
    session.add_all([
        TransactionTag(transaction=tx, tag=Tag(name="viaje"), source="manual"),
        TransactionTag(transaction=tx, tag=Tag(name="quitada"), source="excluded"),
    ])
    session.commit()

    row = d.transactions(session, date(2026, 3, 1), date(2026, 3, 31), d.EXPENSE)[0]

    assert row["tags"] == ["viaje"]
    assert row["bank"] == "Banco X"


# --- meta --------------------------------------------------------------------


def test_meta(session, make_tx):
    session.add(Account(id="acc2", bank_id="Banco X", name="X", type="TRANSACTION",
                        currency="EUR"))
    session.add(SyncRun(bank_id="default", started_at=datetime(2026, 4, 10, 5, 30),
                        finished_at=datetime(2026, 4, 10, 5, 31), status="ok"))
    session.commit()
    make_tx(10.0, booked_at=at(2026, 1, 5), my_category="Ocio")
    make_tx(10.0, booked_at=at(2026, 3, 7), my_category="Ocio", account_id="acc2")

    m = d.meta(session, today=TODAY)

    assert m["today"] == "2026-04-10"
    assert m["current_month"] == "2026-04"
    assert (m["cut_day"], m["days_in_month"]) == (10, 30)
    assert m["months"] == ["2026-01", "2026-02", "2026-03", "2026-04"]
    assert m["coverage"] == [
        {"bank": "default", "first_month": "2026-01"},
        {"bank": "Banco X", "first_month": "2026-03"},
    ]
    assert m["last_sync"] == [{"bank": "default", "status": "ok",
                               "started_at": "2026-04-10T05:30:00",
                               "finished_at": "2026-04-10T05:31:00"}]


def test_meta_sin_datos(session):
    m = d.meta(session, today=TODAY)

    assert m["months"] == ["2026-04"]
    assert m["coverage"] == [] and m["last_sync"] == []
