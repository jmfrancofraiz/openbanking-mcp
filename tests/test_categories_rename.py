from __future__ import annotations

import pytest
from typer.testing import CliRunner

from finmcp import cli
from finmcp.analytics.categorization import apply_rules
from finmcp.db.models import CategoryRule, Transaction
from finmcp.mcp import server


@pytest.fixture
def tools(monkeypatch, Session, session):
    monkeypatch.setattr(server, "SessionLocal", Session)
    return server


def _rule(session, pattern: str, category: str, **kw) -> CategoryRule:
    rule = CategoryRule(pattern=pattern, category=category, field="any", priority=100, **kw)
    session.add(rule)
    session.commit()
    return rule


@pytest.fixture
def datos(session, make_tx):
    """Dos reglas de «Ocio nocturno», una de «Restauración» y un movimiento manual."""
    _rule(session, "othilio", "Ocio nocturno / Bares")
    _rule(session, "flamingo", "Ocio nocturno / Bares", is_bill=True)
    _rule(session, "abadia", "Restauración")
    make_tx(12.0, merchant="OTHILIO BAR")
    make_tx(30.0, merchant="FLAMINGO VIGO")
    make_tx(20.0, merchant="ABADIA DE NURIA")
    apply_rules(session)
    manual = make_tx(
        8.0,
        description="Copa",
        my_category="Ocio nocturno / Bares",
        skip_category_rules=True,
        neutral=True,
    )
    return manual


def _cats(session) -> dict[str, str]:
    session.expire_all()
    return {t.id: t.my_category for t in session.query(Transaction)}


def test_list_categories_counts_rules_txs_and_manual(tools, datos):
    rows = {r["category"]: r for r in tools.list_categories()}

    assert rows["Ocio nocturno / Bares"] == {
        "category": "Ocio nocturno / Bares",
        "rules": 2,
        "transactions": 3,
        "manual": 1,
    }
    assert rows["Restauración"]["rules"] == 1
    assert rows["Restauración"]["transactions"] == 1
    assert rows["Restauración"]["manual"] == 0


def test_rename_simulates_by_default(tools, datos, session):
    before = _cats(session)

    r = tools.rename_category("Ocio nocturno / Bares", "Ocio - Bares")

    assert r == {
        "old": "Ocio nocturno / Bares",
        "new": "Ocio - Bares",
        "rules": 2,
        "transactions": 3,
        "manual": 1,
        "merge": False,
        "applied": False,
    }
    assert _cats(session) == before
    assert session.query(CategoryRule).filter_by(category="Ocio - Bares").count() == 0


def test_rename_apply_updates_rules_and_txs_including_manual(tools, datos, session):
    manual = datos

    r = tools.rename_category("Ocio nocturno / Bares", "Ocio - Bares", apply=True)

    assert r["applied"] is True
    session.expire_all()
    assert session.query(CategoryRule).filter_by(category="Ocio - Bares").count() == 2
    assert session.query(CategoryRule).filter_by(category="Ocio nocturno / Bares").count() == 0
    cats = _cats(session)
    assert list(cats.values()).count("Ocio - Bares") == 3
    assert list(cats.values()).count("Restauración") == 1
    got = session.get(Transaction, manual.id)
    # el override manual y sus marcas se conservan
    assert (got.my_category, got.skip_category_rules, got.neutral) == ("Ocio - Bares", True, True)
    # el nombre no cambia qué regla gana: reaplicar no mueve nada
    assert apply_rules(session) == 0


def test_rename_into_existing_requires_merge(tools, datos, session):
    with pytest.raises(ValueError, match="--merge"):
        tools.rename_category("Ocio nocturno / Bares", "Restauración", apply=True)
    assert session.query(CategoryRule).filter_by(category="Restauración").count() == 1

    r = tools.rename_category("Ocio nocturno / Bares", "Restauración", merge=True, apply=True)

    assert r["merge"] is True
    assert list(_cats(session).values()).count("Restauración") == 4


def test_rename_existing_only_in_transactions_counts_as_merge(tools, datos, make_tx):
    make_tx(5.0, description="suelto", my_category="Huérfana")
    with pytest.raises(ValueError, match="ya existe"):
        tools.rename_category("Restauración", "Huérfana")


def test_rename_category_only_in_transactions(tools, make_tx, session):
    tx = make_tx(5.0, description="importado", my_category="Vieja")

    r = tools.rename_category("Vieja", "Nueva", apply=True)

    assert (r["rules"], r["transactions"]) == (0, 1)
    session.expire_all()
    assert session.get(Transaction, tx.id).my_category == "Nueva"


@pytest.mark.parametrize(
    ("old", "new", "msg"),
    [
        ("No existe", "Otra", "No existe la categoría"),
        ("Restauración", "Restauración", "igual"),
        ("Restauración", "  ", "vacía"),
    ],
)
def test_rename_rejects_invalid_input(tools, datos, old, new, msg):
    with pytest.raises(ValueError, match=msg):
        tools.rename_category(old, new)


def test_cli_rename_and_list(monkeypatch, tools, datos):
    from finmcp.db import session as db_session

    monkeypatch.setattr(db_session, "init_db", lambda: None)
    runner = CliRunner()

    sim = runner.invoke(cli.app, ["categories", "rename", "Ocio nocturno / Bares", "Ocio - Bares"])
    assert sim.exit_code == 0, sim.output
    assert "2 reglas · 3 movimientos (1 manuales)" in sim.output
    assert "Simulación" in sim.output

    bad = runner.invoke(cli.app, ["categories", "rename", "Ocio nocturno / Bares", "Restauración"])
    assert bad.exit_code == 2
    assert "--merge" in bad.output

    done = runner.invoke(
        cli.app, ["categories", "rename", "Ocio nocturno / Bares", "Ocio - Bares", "--apply"]
    )
    assert done.exit_code == 0, done.output
    assert "Hecho." in done.output

    listing = runner.invoke(cli.app, ["categories", "list"]).output
    assert "- Ocio - Bares · reglas 2 · movimientos 3 (1 manuales)" in listing
    assert "Ocio nocturno" not in listing
