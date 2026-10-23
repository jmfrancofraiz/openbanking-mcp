"""Cuentas manuales: alta, saldo a mano, importación por IBAN y su estado en el Diario."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from finmcp.analytics import ledger
from finmcp.db.models import Account, Balance, SyncRun, Transaction, is_manual_account
from finmcp.db.queries import latest_balances
from finmcp.importers.csv_import import import_file
from finmcp.mcp import server
from finmcp.util import iban_is_valid, normalize_iban

NARANJA = "ES91 2100 0418 4502 0005 1332"
NARANJA_N = "ES9121000418450200051332"


@pytest.fixture
def tools(monkeypatch, Session, session):
    monkeypatch.setattr(server, "SessionLocal", Session)
    return server


def test_normalize_and_validate_iban():
    assert normalize_iban(NARANJA) == NARANJA_N
    assert normalize_iban(" es91-2100 ") == "ES912100"
    assert normalize_iban("") is None and normalize_iban(None) is None
    assert iban_is_valid(NARANJA_N)
    assert iban_is_valid("ES3214650100961703280894")
    assert not iban_is_valid("ES9221000418450200051332")  # un dígito de control cambiado
    assert not iban_is_valid("ES61")


def test_add_manual_account(tools, session):
    a = tools.add_manual_account("ING", "Cuenta Naranja", NARANJA, account_type="svgs")

    assert a["bank_id"] == "ING" and a["name"] == "Cuenta Naranja"
    assert a["iban"] == NARANJA_N and a["type"] == "SVGS" and a["currency"] == "EUR"
    assert a["manual"] is True
    session.expire_all()
    acc = session.get(Account, a["id"])
    assert acc is not None and is_manual_account(acc)
    assert not is_manual_account(session.get(Account, "acc1"))  # las sincronizadas no
    listed = {x["id"]: x for x in tools.list_accounts()}
    assert listed[a["id"]]["manual"] is True and listed["acc1"]["manual"] is False


def test_add_manual_account_rechaza_iban_invalido_o_repetido(tools):
    with pytest.raises(ValueError, match="no es válido"):
        tools.add_manual_account("ING", "Mala", "ES92 2100 0418 4502 0005 1332")
    tools.add_manual_account("ING", "Cuenta Naranja", NARANJA)
    with pytest.raises(ValueError, match="Ya existe"):
        tools.add_manual_account("ING", "Otra", NARANJA_N)
    with pytest.raises(ValueError, match="obligatorios"):
        tools.add_manual_account(" ", "Sin banco")


def test_set_manual_balance(tools, session):
    a = tools.add_manual_account("ING", "Cuenta Naranja", NARANJA)

    r = tools.set_manual_balance(a["id"], 1234.567, at="2026-10-23")

    assert r["balance"] == 1234.57 and r["as_of"] == "2026-10-23T21:00:00"
    session.expire_all()
    snap = {b.account_id: b for b in latest_balances(session)}[a["id"]]
    assert snap.current == snap.available == 1234.57 and snap.currency == "EUR"
    # Un saldo posterior pasa a ser el último; con hora ISO en UTC se guarda en UTC sin zona.
    r2 = tools.set_manual_balance(a["id"], 14100, at="2026-10-30T09:00:00+01:00")
    assert r2["as_of"] == "2026-10-30T08:00:00"
    session.expire_all()
    assert {b.account_id: b for b in latest_balances(session)}[a["id"]].current == 14100.0


def test_set_manual_balance_solo_cuentas_manuales(tools):
    with pytest.raises(ValueError, match="se sincroniza"):
        tools.set_manual_balance("acc1", 10.0)
    with pytest.raises(ValueError, match="No existe"):
        tools.set_manual_balance("nada", 10.0)


def test_remove_manual_account_simula_y_retira(tools, session):
    a = tools.add_manual_account("Bonusly", "Bonusly", account_type="OTHR")
    tools.set_manual_balance(a["id"], 1643)

    sim = tools.remove_manual_account(a["id"])
    assert (sim["removed"], sim["balances"]) == (False, 1)
    assert session.get(Account, a["id"]) is not None  # la simulación no toca nada

    done = tools.remove_manual_account(a["id"], apply=True)
    session.expire_all()
    assert done["removed"] is True
    assert session.get(Account, a["id"]) is None
    assert session.query(Balance).filter(Balance.account_id == a["id"]).count() == 0


def test_remove_manual_account_rechaza_sincronizadas_y_con_movimientos(tools, session, make_tx):
    with pytest.raises(ValueError, match="se sincroniza"):
        tools.remove_manual_account("acc1")  # la cuenta de prueba no es manual
    a = tools.add_manual_account("ING", "Naranja", account_type="SVGS")
    session.add(Transaction(id="t-man", account_id=a["id"], amount=5.0, currency="EUR",
                            booked_at=datetime(2026, 10, 1), description="x", type="debit"))
    session.commit()
    with pytest.raises(ValueError, match="1 movimientos"):
        tools.remove_manual_account(a["id"], apply=True)


def test_import_csv_encuentra_la_cuenta_por_iban_con_espacios(tools, session, tmp_path):
    a = tools.add_manual_account("ING", "Cuenta Naranja", NARANJA)
    f = tmp_path / "naranja.csv"
    f.write_text("Fecha;Concepto;Importe\n15/06/2026;Transferencia recibida;10000,00\n", encoding="utf-8")

    added, _ = import_file(session, str(f), iban=NARANJA)

    assert added == 1
    session.expire_all()
    assert session.get(Account, a["id"]).transactions[0].amount == 10000.0


def test_ledger_cuenta_manual_no_depende_del_sync(tools, session):
    session.query(Account).filter_by(id="acc1").update({"bank_id": "ING"})
    session.commit()
    a = tools.add_manual_account("ING", "Cuenta Naranja", NARANJA)
    now = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
    old = datetime(2026, 10, 23, 21, 0)  # casi un mes antes
    session.add(Balance(account_id=a["id"], current=1234.57, available=1234.57,
                        currency="EUR", snapshot_at=old))
    session.add(Balance(account_id="acc1", current=500.0, currency="EUR", snapshot_at=old))
    # El último sync de ING falló: afecta a la cuenta sincronizada, no a la manual.
    session.add(SyncRun(bank_id="ING", started_at=now - timedelta(hours=1),
                        finished_at=now - timedelta(hours=1), status="error", detail="429"))
    session.commit()

    rows = {r["id"]: r for r in ledger.accounts(session, now)}

    naranja = rows[a["id"]]
    assert naranja["manual"] is True and naranja["sync"] is None
    assert naranja["state"] == "ok" and naranja["iban_tail"] == "1332"
    assert naranja["balance"] == {"value": 1234.57, "kind": "actual", "at": "2026-10-23T21:00:00Z"}
    assert rows["acc1"]["manual"] is False and rows["acc1"]["state"] == "stale"
    total = ledger.accounts_total(list(rows.values()))
    assert total["value"] == 1734.57 and total["oldest"] == "2026-10-23T21:00:00Z"


def test_ledger_cuenta_manual_sin_saldo_es_stale(tools, session):
    a = tools.add_manual_account("ING", "Cuenta Naranja", NARANJA)

    row = {r["id"]: r for r in ledger.accounts(session, datetime.now(timezone.utc))}[a["id"]]

    assert row["balance"] is None and row["state"] == "stale" and row["manual"] is True
