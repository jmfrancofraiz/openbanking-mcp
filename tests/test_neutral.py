from __future__ import annotations

from datetime import datetime, timezone

import pytest

from finmcp.analytics.categories import spend_by_category
from finmcp.db.models import CategoryRule, Transaction
from finmcp.mcp import server


@pytest.fixture
def tools(monkeypatch, Session, session):
    monkeypatch.setattr(server, "SessionLocal", Session)
    return server


def test_set_transactions_neutral(tools, make_tx, session):
    a = make_tx(500.0, description="Traspaso")
    b = make_tx(20.0, merchant="Mercadona")

    assert tools.set_transactions_neutral([a.id])["changed"] == 1
    assert tools.set_transactions_neutral([a.id])["changed"] == 0  # ya lo era

    session.expire_all()
    assert session.get(Transaction, a.id).neutral is True
    assert session.get(Transaction, b.id).neutral is False

    assert tools.set_transactions_neutral([a.id], neutral=False)["changed"] == 1


def test_set_transactions_neutral_unknown_id(tools, make_tx):
    make_tx(10.0)
    with pytest.raises(ValueError, match="nope"):
        tools.set_transactions_neutral(["nope"])


def test_set_rule_neutral_reapplies(tools, make_tx, session):
    tx = make_tx(500.0, description="Traspaso a ahorro")
    rule = CategoryRule(pattern="traspaso", category="Traspasos internos")
    session.add(rule)
    session.commit()

    r = tools.set_rule_neutral(rule.id)

    assert r == {"id": rule.id, "neutral": True, "recategorized": 1}
    session.expire_all()
    assert session.get(Transaction, tx.id).neutral is True


def test_set_rule_neutral_unknown_rule(tools):
    with pytest.raises(ValueError):
        tools.set_rule_neutral(999)


def test_spend_by_category_excludes_neutral(make_tx, session):
    when = datetime(2026, 3, 5, tzinfo=timezone.utc)
    make_tx(50.0, my_category="Super", booked_at=when)
    make_tx(300.0, my_category="Tarjeta", booked_at=when, neutral=True)

    rows = spend_by_category(session)

    assert [r["category"] for r in rows] == ["Super"]
