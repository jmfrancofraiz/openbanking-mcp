from __future__ import annotations

from datetime import datetime, timezone

import pytest

from finmcp.analytics import tagging
from finmcp.analytics.categorization import apply_rules
from finmcp.db import queries
from finmcp.db.models import (
    TAG_EXCLUDED,
    TAG_MANUAL,
    TAG_RULE,
    CategoryRule,
    Tag,
    TransactionTag,
    category_rule_tags,
)


def _rule(session, pattern, category, tags=(), priority=100):
    rule = CategoryRule(pattern=pattern, category=category, priority=priority)
    rule.tags = [tagging.get_or_create_tag(session, t) for t in tags]
    session.add(rule)
    session.commit()
    return rule


def _sources(tx) -> dict[str, str]:
    return {l.tag.name: l.source for l in tx.tag_links}


def test_normalize_and_implicit_creation(make_tx, session):
    tx = make_tx(10.0)
    tagging.tag_transactions(session, [tx.id], ["  Tomiño ", "tomiño"])

    assert [t.name for t in session.query(Tag).all()] == ["tomiño"]
    assert tx.tag_names == ["tomiño"]
    with pytest.raises(ValueError):
        tagging.create_tag(session, "   ")


def test_tag_unknown_transaction_fails(session):
    with pytest.raises(ValueError):
        tagging.tag_transactions(session, ["nope"], ["x"])


def test_rule_tags_are_union_of_matching_rules(make_tx, session):
    tx = make_tx(50.0, description="Comercializadora Baser recibo luz")
    _rule(session, "baser", "Suministros - Electricidad", ["Tomiño"], priority=1)
    _rule(session, "recibo", "Recibos", ["casa"], priority=50)

    apply_rules(session)

    assert tx.my_category == "Suministros - Electricidad"
    assert _sources(tx) == {"tomiño": TAG_RULE, "casa": TAG_RULE}


def test_removing_rule_tag_drops_rule_links_but_keeps_manual(make_tx, session):
    tx1 = make_tx(50.0, description="baser")
    tx2 = make_tx(60.0, description="baser")
    rule = _rule(session, "baser", "Luz", ["tomiño"])
    apply_rules(session)
    tagging.tag_transactions(session, [tx2.id], ["tomiño"])

    tagging.remove_rule_tags(session, rule.id, ["tomiño"])
    apply_rules(session)

    assert tx1.tag_names == []
    assert _sources(tx2) == {"tomiño": TAG_MANUAL}


def test_manual_untag_survives_reapply(make_tx, session):
    tx = make_tx(50.0, description="baser")
    _rule(session, "baser", "Luz", ["tomiño"])
    apply_rules(session)

    tagging.untag_transactions(session, [tx.id], ["tomiño"])
    apply_rules(session)

    assert tx.tag_names == []
    assert _sources(tx) == {"tomiño": TAG_EXCLUDED}


def test_manual_tag_over_exclusion_becomes_manual(make_tx, session):
    tx = make_tx(50.0, description="baser")
    _rule(session, "baser", "Luz", ["tomiño"])
    apply_rules(session)
    tagging.untag_transactions(session, [tx.id], ["tomiño"])

    assert tagging.tag_transactions(session, [tx.id], ["tomiño"]) == 1
    assert _sources(tx) == {"tomiño": TAG_MANUAL}


def test_add_rule_tags_creates_and_dedupes(session):
    rule = _rule(session, "x", "X")
    assert tagging.add_rule_tags(session, rule.id, ["A", "a", "b"]) == ["a", "b"]
    with pytest.raises(ValueError):
        tagging.remove_rule_tags(session, rule.id, ["zzz"])


def test_delete_tag_in_use_by_transaction_fails(make_tx, session):
    tx = make_tx(10.0)
    tagging.tag_transactions(session, [tx.id], ["viaje"])

    with pytest.raises(ValueError, match="en uso: 1 transacciones, 0 reglas"):
        tagging.delete_tag(session, "viaje")
    assert session.query(Tag).count() == 1
    assert session.query(TransactionTag).count() == 1


def test_delete_tag_in_use_by_rule_fails(session):
    _rule(session, "x", "X", ["viaje"])

    with pytest.raises(ValueError, match="0 transacciones, 1 reglas"):
        tagging.delete_tag(session, "viaje")
    assert session.query(Tag).count() == 1
    assert session.execute(category_rule_tags.select()).all() != []


def test_delete_unused_tag(session):
    tagging.create_tag(session, "viaje")
    tagging.delete_tag(session, "viaje")
    assert session.query(Tag).count() == 0
    with pytest.raises(ValueError):
        tagging.delete_tag(session, "viaje")


def test_delete_tag_with_only_exclusions_purges_them(make_tx, session):
    tx = make_tx(10.0)
    tagging.tag_transactions(session, [tx.id], ["viaje"])
    tagging.untag_transactions(session, [tx.id], ["viaje"])

    tagging.delete_tag(session, "viaje")

    assert session.query(Tag).count() == 0
    assert session.query(TransactionTag).count() == 0
    assert tx.tag_links == []


def test_query_by_tag_ignores_exclusions_and_no_duplicates(make_tx, session):
    tx1 = make_tx(10.0)
    tx2 = make_tx(20.0)
    tagging.tag_transactions(session, [tx1.id, tx2.id], ["viaje", "ocio"])
    tagging.untag_transactions(session, [tx2.id], ["viaje"])

    rows = queries.query_transactions(session, tag="Viaje")
    assert [t.id for t in rows] == [tx1.id]
    assert len(queries.query_transactions(session, tag="ocio")) == 2


def test_spend_by_tag(make_tx, session):
    day = datetime(2026, 5, 1, tzinfo=timezone.utc)
    tx1 = make_tx(30.0, booked_at=day)
    tx2 = make_tx(20.0, booked_at=day)
    make_tx(5.0, booked_at=day)
    make_tx(100.0, type="credit", booked_at=day)
    tagging.tag_transactions(session, [tx1.id], ["viaje", "ocio"])
    tagging.tag_transactions(session, [tx2.id], ["viaje"])

    rows = {r["tag"]: (r["total"], r["count"]) for r in tagging.spend_by_tag(session)}

    assert rows == {
        "viaje": (50.0, 2),
        "ocio": (30.0, 1),
        tagging.UNTAGGED: (5.0, 1),
    }
