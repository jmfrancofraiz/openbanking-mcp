from __future__ import annotations

from datetime import datetime, timezone

import pytest

from finmcp.analytics.categorization import apply_rules
from finmcp.db.models import Account, CategoryRule, Transaction
from finmcp.mcp import server


@pytest.fixture
def tools(monkeypatch, Session, session):
    monkeypatch.setattr(server, "SessionLocal", Session)
    return server


@pytest.fixture
def acc2(session) -> Account:
    """Segunda cuenta de prueba (la fixture `session` ya trae `acc1`)."""
    a = Account(id="acc2", bank_id="ING", name="Test ING", type="TRANSACTION", currency="EUR")
    session.add(a)
    session.commit()
    return a


def _tx(session, account_id: str, amount: float, **kw) -> Transaction:
    tx = Transaction(
        id=f"tx-{account_id}-{amount}-{kw.get('description', '')}",
        account_id=account_id,
        amount=amount,
        currency="EUR",
        booked_at=kw.pop("booked_at", None) or datetime.now(timezone.utc),
        description=kw.pop("description", ""),
        type=kw.pop("type", "debit"),
        merchant_name=kw.pop("merchant", None),
        **kw,
    )
    session.add(tx)
    session.commit()
    return tx


def test_apply_rules_account_scope_filters(session, acc2, make_tx):
    tx1 = make_tx(10.0, merchant="Mercadona")  # acc1
    tx2 = _tx(session, "acc2", 10.0, merchant="Mercadona")

    session.add(
        CategoryRule(
            pattern="mercadona",
            category="Supermercado BBVA",
            account_id="acc1",
        )
    )
    session.commit()

    changed = apply_rules(session)

    assert changed == 1  # solo la tx de acc1 casa
    session.expire_all()
    assert session.get(Transaction, tx1.id).my_category == "Supermercado BBVA"
    assert session.get(Transaction, tx2.id).my_category is None


def test_apply_rules_without_account_scope_matches_all(session, acc2, make_tx):
    tx1 = make_tx(10.0, merchant="Mercadona")
    tx2 = _tx(session, "acc2", 10.0, merchant="Mercadona")

    session.add(CategoryRule(pattern="mercadona", category="Supermercado"))
    session.commit()

    changed = apply_rules(session)

    assert changed == 2
    session.expire_all()
    assert session.get(Transaction, tx1.id).my_category == "Supermercado"
    assert session.get(Transaction, tx2.id).my_category == "Supermercado"


def test_add_category_rule_scoped_to_account(tools, session, acc2, make_tx):
    tx1 = make_tx(20.0, merchant="Vodafone")
    tx2 = _tx(session, "acc2", 20.0, merchant="Vodafone")

    r = tools.add_category_rule("vodafone", "Telefonía", account_id="acc1")

    assert r["recategorized"] == 1
    assert r["account_id"] == "acc1"
    assert r["account_label"] == "default"  # bank_id por defecto de la cuenta de prueba
    session.expire_all()
    assert session.get(Transaction, tx1.id).my_category == "Telefonía"
    assert session.get(Transaction, tx2.id).my_category is None


def test_add_category_rule_rejects_unknown_account(tools):
    with pytest.raises(ValueError, match="No existe la cuenta"):
        tools.add_category_rule("vodafone", "Telefonía", account_id="no-existe")


def test_update_category_rule_sets_and_clears_account(tools, session, acc2, make_tx):
    tx1 = make_tx(15.0, merchant="Netflix")
    tx2 = _tx(session, "acc2", 15.0, merchant="Netflix")
    rule = CategoryRule(pattern="netflix", category="Ocio")
    session.add(rule)
    session.commit()
    tools.categorize()
    session.expire_all()
    assert session.get(Transaction, tx1.id).my_category == "Ocio"
    assert session.get(Transaction, tx2.id).my_category == "Ocio"

    r = tools.update_category_rule(rule.id, account_id="acc2")

    assert r["account_id"] == "acc2"
    # Igual que al eliminar/estrechar cualquier regla (Pitfall 9): lo ya
    # categorizado no se toca cuando deja de casar ninguna regla.
    assert r["recategorized"] == 0
    session.expire_all()
    assert session.get(Transaction, tx1.id).my_category == "Ocio"
    assert session.get(Transaction, tx2.id).my_category == "Ocio"

    # Pero la restricción sí aplica a partir de ahora: una tx nueva de acc1 no casa.
    tx3 = _tx(session, "acc1", 15.0, merchant="Netflix")
    tools.categorize()
    session.expire_all()
    assert session.get(Transaction, tx3.id).my_category is None

    r2 = tools.update_category_rule(rule.id, clear_account=True)

    assert r2["account_id"] is None
    assert r2["recategorized"] == 1  # tx3 ahora sí casa
    session.expire_all()
    assert session.get(Transaction, tx3.id).my_category == "Ocio"


def test_update_category_rule_rejects_unknown_account(tools):
    rule_id = tools.add_category_rule("x", "Y")["id"]
    with pytest.raises(ValueError, match="No existe la cuenta"):
        tools.update_category_rule(rule_id, account_id="no-existe")


def test_update_category_rule_rejects_account_and_clear_account(tools, session):
    rule = CategoryRule(pattern="x", category="Y")
    session.add(rule)
    session.commit()
    with pytest.raises(ValueError, match="No combines --account con --clear-account"):
        tools.update_category_rule(rule.id, account_id="acc1", clear_account=True)


def test_list_category_rules_includes_account_label(tools, session, acc2):
    session.add(CategoryRule(pattern="x", category="Y", account_id="acc2"))
    session.add(CategoryRule(pattern="z", category="W"))
    session.commit()

    rows = tools.list_category_rules()

    by_pattern = {r["pattern"]: r for r in rows}
    assert by_pattern["x"]["account_id"] == "acc2"
    assert by_pattern["x"]["account_label"] == "ING"
    assert by_pattern["z"]["account_id"] is None
    assert by_pattern["z"]["account_label"] is None
