"""Pestaña «Diario» del panel: saldos por cuenta, lista plana, saldo al cierre calculado y detalle."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from finmcp.analytics import ledger
from finmcp.db.models import Account, Balance, SyncRun, Tag, TransactionTag

NOW = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)


def at(y: int, m: int, d: int) -> datetime:
    # booked_at se guarda sin zona (fecha contable a medianoche), como en la BD real.
    return datetime(y, m, d)


def utc(y: int, m: int, d: int, h: int = 5, mi: int = 30) -> datetime:
    # Las fotos de saldo se guardan en UTC sin zona.
    return datetime(y, m, d, h, mi)


def add_balance(session, account_id, when, current=None, available=None):
    session.add(Balance(account_id=account_id, current=current, available=available,
                        currency="EUR", snapshot_at=when))
    session.commit()


def add_run(session, bank, started, status="ok", detail=None):
    session.add(SyncRun(bank_id=bank, started_at=started, finished_at=started + timedelta(seconds=5),
                        status=status, detail=detail))
    session.commit()


def tag(session, tx, name, source="manual"):
    t = session.query(Tag).filter_by(name=name).one_or_none()
    if t is None:
        t = Tag(name=name)
        session.add(t)
        session.flush()
    session.add(TransactionTag(transaction_id=tx.id, tag_id=t.id, source=source))
    session.commit()


@pytest.fixture
def cuentas(session):
    session.query(Account).filter_by(id="acc1").update({"bank_id": "ING", "iban": "ES3214650100961703280894"})
    session.add(Account(id="acc2", bank_id="BBVA", name="Cuenta BBVA", type="CACC", currency="EUR",
                        iban="ES1701825334950202875816"))
    session.add(Account(id="acc3", bank_id="Revolut", name="Cuenta", type="CACC", currency="EUR"))
    session.commit()


# --- cuentas -------------------------------------------------------------------


def test_accounts_estado_saldo_y_orden(session, cuentas, make_tx):
    make_tx(10, booked_at=at(2026, 10, 20))
    make_tx(5, booked_at=at(2026, 10, 1), account_id="acc2")
    add_balance(session, "acc1", utc(2026, 10, 20), current=900.0)
    add_balance(session, "acc1", utc(2026, 10, 21), current=1000.0, available=None)
    add_balance(session, "acc2", utc(2026, 10, 20), current=50.0)
    add_balance(session, "acc3", utc(2026, 10, 18), current=None, available=9.75)
    add_run(session, "ING", utc(2026, 10, 21))
    add_run(session, "BBVA", utc(2026, 10, 20))
    add_run(session, "BBVA", utc(2026, 10, 21), status="error", detail="Client error '429 Too Many Requests'\nmore")
    add_run(session, "Revolut", utc(2026, 10, 21))

    rows = {a["bank"]: a for a in ledger.accounts(session, NOW)}

    assert [a["bank"] for a in ledger.accounts(session, NOW)] == ["BBVA", "ING", "Revolut"]
    ing, bbva, rev = rows["ING"], rows["BBVA"], rows["Revolut"]
    assert ing["balance"] == {"value": 1000.0, "kind": "actual", "at": "2026-10-21T05:30:00Z"}
    assert ing["state"] == "ok" and ing["iban_tail"] == "0894" and ing["count"] == 1
    assert ing["last"] == "2026-10-20"
    # El último intento de BBVA falló: se ve el saldo de la foto anterior, en ámbar y con el motivo.
    assert bbva["state"] == "warn"
    assert bbva["sync"]["error"] == "el banco ha limitado los accesos (429)"
    assert bbva["sync"]["last_ok"] == "2026-10-20T05:30:05Z"
    # Sin saldo contable se usa el disponible; foto de hace más de 36 h → desactualizada.
    assert rev["balance"]["kind"] == "disponible" and rev["balance"]["value"] == 9.75
    assert rev["state"] == "stale" and rev["iban_tail"] is None


def test_accounts_sin_saldo_es_stale(session, cuentas):
    rows = {a["id"]: a for a in ledger.accounts(session, NOW)}

    assert rows["acc2"]["balance"] is None and rows["acc2"]["state"] == "stale"
    assert rows["acc2"]["sync"] is None and rows["acc2"]["count"] == 0


def _acc(bank, value, kind="actual", state="ok", at="2026-10-21T05:30:00Z", currency="EUR"):
    """Una fila de `accounts()` con lo único que mira `accounts_total`."""
    balance = None if value is None else {"value": value, "kind": kind, "at": at}
    return {"bank": bank, "currency": currency, "balance": balance, "state": state}


def test_accounts_total_suma_los_ultimos_saldos(session, cuentas):
    add_balance(session, "acc1", utc(2026, 10, 21), current=8075.99)
    add_balance(session, "acc2", utc(2026, 10, 21), current=9380.35)
    add_balance(session, "acc3", utc(2026, 10, 21), current=None, available=9.75)
    for bank in ("ING", "BBVA", "Revolut"):
        add_run(session, bank, utc(2026, 10, 21))

    total = ledger.accounts_total(ledger.accounts(session, NOW))

    assert total == {
        "value": 17466.09, "currency": "EUR", "count": 3, "missing": [],
        # Revolut no da saldo contable: aporta el disponible, y se avisa.
        "disponible": ["Revolut"], "outdated": [], "oldest": "2026-10-21T05:30:00Z", "state": "ok",
    }


def test_accounts_total_en_centimos_sin_errores_de_coma_flotante():
    # 0,1 + 0,2 en coma flotante da 0,30000000000000004.
    total = ledger.accounts_total([_acc("A", 0.1), _acc("B", 0.2), _acc("C", 1234.56)])

    assert total["value"] == 1234.86


def test_accounts_total_cuenta_sin_saldo_no_suma_pero_se_avisa():
    total = ledger.accounts_total([_acc("A", 100.0), _acc("B", None, state="stale")])

    assert total["value"] == 100.0 and total["count"] == 1
    assert total["missing"] == ["B"] and total["outdated"] == []
    assert total["state"] == "stale"  # el peor estado cuenta aunque esa cuenta no sume


def test_accounts_total_saldo_viejo_o_con_sync_fallida_suma_pero_se_avisa():
    total = ledger.accounts_total([
        _acc("A", 100.0), _acc("B", 50.0, state="warn"),
        _acc("C", 25.0, state="stale", at="2026-10-18T05:30:00Z"),
    ])

    assert total["value"] == 175.0 and total["count"] == 3 and total["missing"] == []
    assert total["outdated"] == ["B", "C"] and total["state"] == "stale"
    assert total["oldest"] == "2026-10-18T05:30:00Z"


def test_accounts_total_compara_las_fotos_como_fechas_no_como_texto():
    # «…05:30:00Z» y «…05:30:00.5Z»: como texto la segunda sería la menor; como fecha es la posterior.
    total = ledger.accounts_total([_acc("A", 1.0, at="2026-10-21T05:30:00Z"),
                                   _acc("B", 1.0, at="2026-10-21T05:30:00.5Z")])

    assert total["oldest"] == "2026-10-21T05:30:00Z"


def test_accounts_total_un_banco_con_dos_cuentas_se_nombra_una_vez():
    total = ledger.accounts_total([_acc("BBVA", 10.0, kind="disponible"), _acc("BBVA", 5.0, kind="disponible")])

    assert total["disponible"] == ["BBVA"] and total["count"] == 2


def test_accounts_total_sin_saldos_o_con_monedas_distintas_no_hay_total():
    assert ledger.accounts_total([]) is None
    assert ledger.accounts_total([_acc("A", None, state="stale")]) is None
    assert ledger.accounts_total([_acc("A", 10.0), _acc("B", 10.0, currency="USD")]) is None


def test_tags_in_use_sin_exclusiones(session, make_tx):
    a, b = make_tx(1), make_tx(2)
    tag(session, a, "tomiño")
    tag(session, b, "tomiño", source="rule")
    tag(session, b, "viaje", source="excluded")

    assert ledger.tags_in_use(session) == [{"name": "tomiño", "count": 2}]


# --- lista plana ---------------------------------------------------------------


@pytest.fixture
def lista(session, cuentas, make_tx):
    txs = [
        make_tx(12.5, merchant="CAFETERÍA TOMIÑO", description="Pago en CAFETERÍA TOMIÑO",
                booked_at=at(2026, 10, 20), my_category="Restauración"),
        make_tx(500, type="credit", description="Traspaso desde BBVA", booked_at=at(2026, 10, 20),
                my_category="Traspasos internos", neutral=True),
        make_tx(60, description="Transferencia emitida periódica", booked_at=at(2026, 10, 1),
                my_category="Vivienda - Garaje", is_bill=True, account_id="acc2"),
        make_tx(8.99, merchant="1703280894", description="Recibo PayPal", booked_at=at(2026, 10, 19)),
        make_tx(30, description="Dentista", booked_at=at(2026, 9, 30), my_category="Salud - Dentista",
                skip_category_rules=True),
    ]
    tag(session, txs[0], "tomiño")
    return txs


def ids(page):
    return [t["id"] for t in page["transactions"]]


def test_movements_todo_por_fecha_descendente_con_no_computables(session, lista):
    page = ledger.movements(session)

    # Mismo día: orden estable por id descendente (el banco no da la hora).
    assert ids(page) == ["tx2", "tx1", "tx4", "tx3", "tx5"]
    assert page["next"] is None and page["closing"] is None
    first = page["transactions"][0]
    assert first == {
        "id": "tx2", "date": "2026-10-20", "amount": 500.0, "currency": "EUR", "merchant": None,
        "description": "Traspaso desde BBVA", "category": "Traspasos internos", "uncategorized": False,
        "bank": "ING", "account": "acc1", "tags": [], "is_bill": False, "neutral": True, "manual": False,
    }
    by_id = {t["id"]: t for t in page["transactions"]}
    assert by_id["tx1"]["amount"] == -12.5 and by_id["tx1"]["tags"] == ["tomiño"]
    assert by_id["tx4"]["uncategorized"] is True and by_id["tx4"]["category"] == "Sin categoría"


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"account": "acc2"}, ["tx3"]),
        ({"kind": "credit"}, ["tx2"]),
        ({"mark": "uncategorized"}, ["tx4"]),
        ({"mark": "bill"}, ["tx3"]),
        ({"mark": "neutral"}, ["tx2"]),
        ({"mark": "manual"}, ["tx5"]),
        ({"tag": "Tomiño"}, ["tx1"]),
        ({"start": datetime(2026, 10, 1).date(), "end": datetime(2026, 10, 19).date()}, ["tx4", "tx3"]),
        ({"end": datetime(2026, 9, 30).date()}, ["tx5"]),
        ({"text": "tomino"}, ["tx1"]),            # sin acentos ni mayúsculas
        ({"text": "cafeteria  restauracion"}, ["tx1"]),  # todas las palabras, también la categoría
        ({"text": "8,99"}, ["tx4"]),              # importe con coma decimal
        ({"text": "bbva garaje"}, ["tx3"]),       # banco + categoría
        ({"text": "nada parecido"}, []),
    ],
)
def test_movements_filtros(session, lista, kwargs, expected):
    assert ids(ledger.movements(session, **kwargs)) == expected


@pytest.mark.parametrize("text", [None, "e"])
def test_movements_paginacion_sin_huecos_ni_repeticiones(session, lista, text):
    seen, cursor, pages = [], None, 0
    while True:
        page = ledger.movements(session, limit=2, cursor=cursor, text=text)
        seen += ids(page)
        pages += 1
        if not page["next"]:
            break
        at_, tx_id = page["next"].split("|", 1)
        cursor = (datetime.fromisoformat(at_), tx_id)

    expected = ids(ledger.movements(session, text=text))
    assert seen == expected and len(set(seen)) == len(seen)
    assert pages == -(-len(expected) // 2)  # páginas de 2, sin una última vacía


def test_movements_cursor_corta_en_el_mismo_dia(session, lista):
    page = ledger.movements(session, limit=1)

    assert ids(page) == ["tx2"] and page["next"] == "2026-10-20T00:00:00|tx2"
    nxt = ledger.movements(session, limit=1, cursor=(datetime(2026, 10, 20), "tx2"))
    assert ids(nxt) == ["tx1"]


# --- saldo al cierre de cada día (calculado) ------------------------------------------


@pytest.fixture
def saldos(session, cuentas, make_tx):
    # acc1: movimientos del 18, 19 (dos) y 20 de octubre.
    make_tx(100, type="credit", booked_at=at(2026, 10, 18))
    make_tx(20, booked_at=at(2026, 10, 19))
    make_tx(5, booked_at=at(2026, 10, 19))
    make_tx(40, booked_at=at(2026, 10, 20))
    # Fotos (07:30 hora local): la del 19 ya recoge uno de los dos cargos de ese día.
    add_balance(session, "acc1", utc(2026, 10, 19), current=1045.0)
    add_balance(session, "acc1", utc(2026, 10, 21), current=1000.0)


def test_closing_balances_reconstruye_hacia_atras(session, saldos):
    cb = ledger.closing_balances(session, "acc1")

    assert cb["anchor"] == {"value": 1000.0, "kind": "actual", "at": "2026-10-21T05:30:00Z"}
    assert cb["closing"] == {"2026-10-20": 1000.0, "2026-10-19": 1040.0, "2026-10-18": 1065.0}
    # La foto del 19 a las 07:30 = cierre del 18 (1065) − 20 → cuadra con parte del día.
    assert cb["check"] == {"snapshots": 2, "ok": 2, "unknown": 0, "bad": 0, "since": "2026-10-19",
                           "last_bad": None, "suspect_through": None}


def test_closing_balances_detecta_un_hueco(session, saldos):
    # Una foto que ningún subconjunto de movimientos explica: falta o sobra un movimiento.
    add_balance(session, "acc1", utc(2026, 10, 20), current=1234.56)

    check = ledger.closing_balances(session, "acc1")["check"]

    assert check["bad"] == 1 and check["ok"] == 2
    assert check["last_bad"] == {"at": "2026-10-20T05:30:00Z", "date": "2026-10-20", "bank_value": 1234.56,
                                 "calc_prev": 1040.0, "calc_day": 1000.0}
    assert check["suspect_through"] == "2026-10-20"


def test_closing_balances_usa_el_disponible_y_none_sin_fotos(session, cuentas, make_tx):
    make_tx(1, booked_at=at(2026, 10, 1), account_id="acc3")
    assert ledger.closing_balances(session, "acc3") is None

    add_balance(session, "acc3", utc(2026, 10, 2), available=9.75)
    cb = ledger.closing_balances(session, "acc3")
    assert cb["anchor"]["kind"] == "disponible" and cb["closing"] == {"2026-10-01": 9.75}


def test_movements_con_cuenta_trae_el_saldo_de_los_dias_de_la_pagina(session, saldos):
    page = ledger.movements(session, account="acc1", limit=2)

    assert [t["date"] for t in page["transactions"]] == ["2026-10-20", "2026-10-19"]
    assert page["closing"] == {"2026-10-20": 1000.0, "2026-10-19": 1040.0}
    assert page["balance"]["check"]["ok"] == 2


# --- detalle ------------------------------------------------------------------------


def test_movement_con_datos_del_banco(session, cuentas, make_tx):
    tx = make_tx(45, merchant="*****", description="BIZUM // OTROS // ENVIADO: limpieza", booked_at=at(2026, 10, 16))
    tx.raw_json = {
        "entry_reference": "1400", "status": "BOOK", "booking_date": "2026-10-16", "value_date": "2026-10-15",
        "creditor": {"name": "*****"}, "debtor": {"name": "JOSE MARIA;FRANCO;FRAIZ"},
        "debtor_account": {"iban": "ES17", "other": None},
        "bank_transaction_code": {"description": "pmnt", "code": "irct", "sub_code": None},
        "remittance_information": ["BIZUM // OTROS // ENVIADO: limpieza"],
        "debtor_account_additional_identification": [{"identification": "5399xxxx7670", "scheme_name": "CPAN", "issuer": "MASTERCARD"}],
        "exchange_rate": {"unit_currency": "EUR", "exchange_rate": "1.1191",
                          "instructed_amount": {"amount": "5.00", "currency": "USD"}},
    }
    session.commit()

    d = ledger.movement(session, tx.id)

    assert d["account_info"] == {"id": "acc1", "bank": "ING", "name": "Test", "iban_tail": "0894"}
    assert d["source"] == "feed" and d["raw"]["entry_reference"] == "1400"
    fields = {f["label"]: f["value"] for f in d["bank_data"]}
    assert fields == {
        "Beneficiario": "*****", "Ordenante": "JOSE MARIA;FRANCO;FRAIZ", "Cuenta del ordenante": "ES17",
        "Concepto del banco": "BIZUM // OTROS // ENVIADO: limpieza", "Tipo de operación": "pmnt · irct",
        "Tarjeta": "MASTERCARD ··7670", "Tipo de cambio": "1.1191 (EUR) · importe original 5.00 USD",
        "Fecha contable": "16/10/2026", "Fecha valor": "15/10/2026", "Estado": "contabilizado", "Referencia": "1400",
    }


def test_movement_importado_de_csv_y_inexistente(session, make_tx):
    tx = make_tx(10, description="Compra")
    tx.raw_json = {"source": "csv"}
    session.commit()

    d = ledger.movement(session, tx.id)
    assert d["source"] == "csv" and d["bank_data"] == [] and d["raw"] is None
    assert ledger.movement(session, "no-existe") is None


def test_fold():
    assert ledger.fold("Tomiño CAFÉ") == "tomino cafe"
