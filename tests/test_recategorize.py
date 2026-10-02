from __future__ import annotations

import pytest

from finmcp.analytics.categorization import apply_rules
from finmcp.db.models import CategoryRule, Transaction
from finmcp.mcp import server


@pytest.fixture
def tools(monkeypatch, Session, session):
    monkeypatch.setattr(server, "SessionLocal", Session)
    return server


def test_recategorize_transaction(tools, make_tx, session):
    tx = make_tx(500.0, description="Traspaso a ahorro", my_category="Traspasos internos")

    r = tools.recategorize_transaction(tx.id, "Ahorro")

    assert r == {"id": tx.id, "category": "Ahorro", "skip_category_rules": True}
    session.expire_all()
    got = session.get(Transaction, tx.id)
    assert got.my_category == "Ahorro"
    assert got.skip_category_rules is True


def test_recategorize_unknown_id_raises(tools):
    with pytest.raises(ValueError, match="nope"):
        tools.recategorize_transaction("nope", "Cualquiera")


def test_set_transactions_skip_rules(tools, make_tx, session):
    a = make_tx(500.0, description="Traspaso")
    b = make_tx(20.0, merchant="Mercadona")

    assert tools.set_transactions_skip_rules([a.id])["changed"] == 1
    assert tools.set_transactions_skip_rules([a.id])["changed"] == 0  # ya lo estaba

    session.expire_all()
    assert session.get(Transaction, a.id).skip_category_rules is True
    assert session.get(Transaction, b.id).skip_category_rules is False

    assert tools.set_transactions_skip_rules([a.id], skip=False)["changed"] == 1


def test_set_transactions_skip_rules_unknown_id_raises(tools, make_tx):
    make_tx(10.0)
    with pytest.raises(ValueError, match="nope"):
        tools.set_transactions_skip_rules(["nope"])


def test_apply_rules_skips_frozen_transaction(make_tx, session):
    tx = make_tx(50.0, description="Cuota", skip_category_rules=True)
    session.add(CategoryRule(pattern="cuota", category="Cuotas", neutral=True))
    session.commit()

    assert apply_rules(session) == 0
    got = session.get(Transaction, tx.id)
    assert got.my_category is None  # ni categoría…
    assert got.neutral is False     # …ni neutral: la tx está congelada


def test_recategorize_survives_apply_rules(tools, make_tx, session):
    tx = make_tx(500.0, description="Traspaso a ahorro")
    session.add(CategoryRule(pattern="traspaso", category="Traspasos internos"))
    session.commit()
    apply_rules(session)
    assert session.get(Transaction, tx.id).my_category == "Traspasos internos"

    tools.recategorize_transaction(tx.id, "Ahorro")
    session.expire_all()

    assert apply_rules(session) == 0  # el override aguanta la reaplicación
    assert session.get(Transaction, tx.id).my_category == "Ahorro"


def test_skip_rules_off_returns_to_rules(tools, make_tx, session):
    tx = make_tx(500.0, description="Traspaso a ahorro")
    session.add(CategoryRule(pattern="traspaso", category="Traspasos internos"))
    session.commit()
    tools.recategorize_transaction(tx.id, "Ahorro")
    session.expire_all()
    assert apply_rules(session) == 0

    assert tools.set_transactions_skip_rules([tx.id], skip=False)["changed"] == 1
    session.expire_all()

    assert apply_rules(session) == 1  # la regla vuelve a ganar
    assert session.get(Transaction, tx.id).my_category == "Traspasos internos"
