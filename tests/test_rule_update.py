from __future__ import annotations

import pytest

from finmcp.db.models import CategoryRule, Transaction
from finmcp.mcp import server


@pytest.fixture
def tools(monkeypatch, Session, session):
    monkeypatch.setattr(server, "SessionLocal", Session)
    return server


def _add_rule(session, **kw) -> CategoryRule:
    defaults: dict = {
        "pattern": "recibo paypal",
        "category": "Pagos",
        "field": "any",
        "priority": 100,
    }
    defaults.update(kw)
    rule = CategoryRule(**defaults)
    session.add(rule)
    session.commit()
    return rule


def test_update_rule_category_and_reapply(tools, make_tx, session):
    tx = make_tx(9.99, description="Recibo PayPal (Europe) S.a.r.l.")
    rule = _add_rule(session, category="Pagos digitales / Suscripciones online")

    assert tools.categorize()["recategorized"] == 1

    r = tools.update_category_rule(rule.id, category="Pagos digitales")

    assert r["category"] == "Pagos digitales"
    assert r["recategorized"] == 1
    session.expire_all()
    assert session.get(Transaction, tx.id).my_category == "Pagos digitales"


def test_update_rule_pattern_matches_new_tx(tools, make_tx, session):
    make_tx(9.99, description="Recibo PayPal (Europe)")
    rule = _add_rule(session, pattern="no-casa-nada", category="Pagos")

    assert tools.categorize()["recategorized"] == 0

    r = tools.update_category_rule(rule.id, pattern="recibo paypal")

    assert r["pattern"] == "recibo paypal"
    assert r["recategorized"] == 1
    session.expire_all()
    assert session.query(Transaction).one().my_category == "Pagos"


def test_update_rule_priority_changes_winner(tools, make_tx, session):
    make_tx(10.0, merchant="Amazon Prime")
    _add_rule(session, pattern="amazon", category="Compras online", priority=100)
    specific = _add_rule(
        session,
        pattern="amazon prime",
        category="Suscripciones online - Amazon Prime",
        priority=200,
    )

    tools.categorize()
    session.expire_all()
    assert session.query(Transaction).one().my_category == "Compras online"

    r = tools.update_category_rule(specific.id, priority=50)

    assert r["priority"] == 50
    assert r["recategorized"] == 1
    session.expire_all()
    assert (
        session.query(Transaction).one().my_category
        == "Suscripciones online - Amazon Prime"
    )


def test_update_rule_amount_bounds_and_clear(tools, make_tx, session):
    make_tx(60.0, description="Transferencia emitida periódica")
    make_tx(120.0, description="Transferencia emitida periódica")
    rule = _add_rule(
        session,
        pattern="transferencia emitida periódica",
        category="Comercializadora",
        amount_min=100,
    )

    assert tools.categorize()["recategorized"] == 1  # solo el de 120 €

    r = tools.update_category_rule(rule.id, clear_amount_min=True)

    assert r["amount_min"] is None
    assert r["recategorized"] == 1  # el de 60 € entra ahora
    session.expire_all()
    cats = {t.amount: t.my_category for t in session.query(Transaction).all()}
    assert cats[60.0] == "Comercializadora"
    assert cats[120.0] == "Comercializadora"


def test_update_rule_neutral_flag(tools, make_tx, session):
    tx = make_tx(500.0, description="Traspaso a ahorro")
    rule = _add_rule(session, pattern="traspaso", category="Traspasos internos")
    tools.categorize()

    r = tools.update_category_rule(rule.id, neutral=True)
    assert r["neutral"] is True
    assert r["recategorized"] == 1
    session.expire_all()
    assert session.get(Transaction, tx.id).neutral is True

    r2 = tools.update_category_rule(rule.id, neutral=False)
    assert r2["neutral"] is False
    assert r2["recategorized"] == 1
    session.expire_all()
    assert session.get(Transaction, tx.id).neutral is False


def test_update_rule_unknown_id(tools):
    with pytest.raises(ValueError, match="No existe la regla"):
        tools.update_category_rule(999, category="X")


def test_update_rule_requires_at_least_one_field(tools, make_tx, session):
    rule = _add_rule(session)
    with pytest.raises(ValueError, match="Nada que actualizar"):
        tools.update_category_rule(rule.id)


def test_update_rule_validations_leave_rule_untouched(tools, make_tx, session):
    rule = _add_rule(session)

    with pytest.raises(ValueError, match="field debe ser"):
        tools.update_category_rule(rule.id, field="merchantx")
    with pytest.raises(ValueError, match="vacío"):
        tools.update_category_rule(rule.id, pattern="   ")
    with pytest.raises(ValueError, match="vacía"):
        tools.update_category_rule(rule.id, category="")
    with pytest.raises(ValueError, match="No combines"):
        tools.update_category_rule(rule.id, amount_min=5, clear_amount_min=True)
    with pytest.raises(ValueError, match="No combines"):
        tools.update_category_rule(rule.id, amount_max=5, clear_amount_max=True)
    with pytest.raises(ValueError, match="Rango de importe inválido"):
        tools.update_category_rule(rule.id, amount_min=10, amount_max=5)

    session.expire_all()
    fresh = session.get(CategoryRule, rule.id)
    assert fresh.pattern == "recibo paypal"
    assert fresh.category == "Pagos"
    assert fresh.amount_min is None
    assert fresh.amount_max is None
    assert fresh.neutral is False
