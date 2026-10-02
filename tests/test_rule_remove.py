from __future__ import annotations

import pytest

from finmcp.analytics.categorization import apply_rules
from finmcp.db.models import CategoryRule, Tag, Transaction, TransactionTag
from finmcp.mcp import server


@pytest.fixture
def tools(monkeypatch, Session, session):
    monkeypatch.setattr(server, "SessionLocal", Session)
    return server


def _add_rule(session, **kw) -> CategoryRule:
    defaults: dict = {
        "pattern": "paypal",
        "category": "Pagos",
        "field": "any",
        "priority": 100,
    }
    defaults.update(kw)
    rule = CategoryRule(**defaults)
    session.add(rule)
    session.commit()
    return rule


def test_remove_rule_reapplies_to_fallback_rule(tools, make_tx, session):
    tx = make_tx(10.0, merchant="Amazon Prime")
    _add_rule(session, pattern="amazon", category="Compras online", priority=100)
    specific = _add_rule(session, pattern="amazon prime", category="Prime", priority=50)
    rid = specific.id
    tools.categorize()
    session.expire_all()
    assert session.get(Transaction, tx.id).my_category == "Prime"

    r = tools.remove_category_rules([rid])

    assert r["recategorized"] == 1
    assert [d["id"] for d in r["removed"]] == [rid]
    assert r["removed"][0]["category"] == "Prime"
    session.expire_all()
    assert session.get(Transaction, tx.id).my_category == "Compras online"
    assert session.query(CategoryRule).filter_by(id=rid).count() == 0


def test_remove_rule_leaves_orphaned_tx_untouched(tools, make_tx, session):
    tx = make_tx(9.99, description="Recibo PayPal")
    rule = _add_rule(session, pattern="recibo paypal", category="Pagos digitales")
    rid = rule.id
    tools.categorize()
    session.expire_all()
    assert session.get(Transaction, tx.id).my_category == "Pagos digitales"

    r = tools.remove_category_rules([rid])

    assert r["recategorized"] == 0
    session.expire_all()
    assert session.get(Transaction, tx.id).my_category == "Pagos digitales"
    assert session.query(CategoryRule).filter_by(id=rid).count() == 0


def test_remove_rule_unknown_id(tools, make_tx, session):
    with pytest.raises(ValueError, match="No existen las reglas: 999"):
        tools.remove_category_rules([999])


def test_remove_rules_all_or_nothing(tools, make_tx, session):
    rule = _add_rule(session)

    with pytest.raises(ValueError, match="No existen las reglas: 999"):
        tools.remove_category_rules([rule.id, 999])

    session.expire_all()
    assert session.get(CategoryRule, rule.id) is not None  # no se borró nada


def test_remove_rules_multiple_and_dedup(tools, make_tx, session):
    a = _add_rule(session, pattern="aaa")
    b = _add_rule(session, pattern="bbb")
    ra, rb = a.id, b.id

    r = tools.remove_category_rules([ra, rb, ra])

    assert [d["id"] for d in r["removed"]] == [ra, rb]
    session.expire_all()
    assert session.query(CategoryRule).filter_by(id=ra).count() == 0
    assert session.query(CategoryRule).filter_by(id=rb).count() == 0


def test_remove_rule_cleans_rule_tags_keeps_catalog(tools, make_tx, session):
    tx = make_tx(9.99, description="Recibo Netflix")
    r0 = tools.add_category_rule("recibo netflix", "Ocio", tags=["casa"])
    session.expire_all()
    assert "casa" in session.get(Transaction, tx.id).tag_names

    r = tools.remove_category_rules([r0["id"]])

    assert r["recategorized"] == 0
    session.expire_all()
    assert session.get(Transaction, tx.id).tag_names == []
    assert session.query(Tag).filter_by(name="casa").one() is not None


def test_remove_rule_keeps_manual_tags(tools, make_tx, session):
    tx = make_tx(5.0, description="Recibo Netflix")
    r0 = tools.add_category_rule("recibo netflix", "Ocio", tags=["casa"])
    tools.tag_transactions([tx.id], ["verde"])
    session.expire_all()
    assert session.get(Transaction, tx.id).tag_names == ["casa", "verde"]

    tools.remove_category_rules([r0["id"]])

    session.expire_all()
    assert session.get(Transaction, tx.id).tag_names == ["verde"]


def test_apply_rules_without_rules_cleans_rule_tags(make_tx, session):
    tx = make_tx(10.0)
    tag = Tag(name="casa")
    session.add(tag)
    session.flush()
    tx.tag_links.append(TransactionTag(tag=tag, source="rule"))
    session.commit()

    assert apply_rules(session) == 0

    session.expire_all()
    assert session.get(Transaction, tx.id).tag_names == []
