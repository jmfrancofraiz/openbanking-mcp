from __future__ import annotations

from datetime import datetime

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from finmcp.db import models
from finmcp.db.queries import query_transactions

UNCATEGORIZED = "Sin categoría"


def category_of(tx) -> str:
    return tx.my_category or tx.provider_category or UNCATEGORIZED


def spend_by_category(
    session: Session,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict]:
    """Gasto (débitos) agregado por categoría, de mayor a menor."""
    txs = query_transactions(session, start=start, end=end, type="debit", neutral=False)
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for t in txs:
        cat = category_of(t)
        totals[cat] = totals.get(cat, 0.0) + t.amount
        counts[cat] = counts.get(cat, 0) + 1
    rows = [
        {"category": cat, "total": round(total, 2), "count": counts[cat]}
        for cat, total in totals.items()
    ]
    rows.sort(key=lambda r: r["total"], reverse=True)
    return rows


def list_categories(session: Session) -> list[dict]:
    """Categorías en uso (`my_category` y reglas): nº de reglas, de movimientos y de manuales."""
    Rule, Tx = models.CategoryRule, models.Transaction
    rules = dict(
        session.query(Rule.category, func.count(Rule.id)).group_by(Rule.category).all()
    )
    txs = {
        cat: (n, manual or 0)
        for cat, n, manual in session.query(
            Tx.my_category,
            func.count(Tx.id),
            func.sum(case((Tx.skip_category_rules.is_(True), 1), else_=0)),
        )
        .filter(Tx.my_category.is_not(None), Tx.my_category != "")
        .group_by(Tx.my_category)
        .all()
    }
    names = sorted(set(rules) | set(txs), key=str.lower)
    return [
        {
            "category": cat,
            "rules": rules.get(cat, 0),
            "transactions": txs.get(cat, (0, 0))[0],
            "manual": txs.get(cat, (0, 0))[1],
        }
        for cat in names
    ]


def rename_category(
    session: Session, old: str, new: str, *, merge: bool = False, apply: bool = False
) -> dict:
    """Renombra una categoría en sus reglas y en sus movimientos (también los manuales).

    No reaplica las reglas: cambiar el nombre no cambia qué regla gana. Los
    movimientos fijados a mano conservan su `skip_category_rules`. Si `new` ya
    existe, exige `merge=True` (fusionar no se deshace renombrando). Sin
    `apply` solo cuenta lo que cambiaría.
    """
    Rule, Tx = models.CategoryRule, models.Transaction
    old, new = (old or "").strip(), (new or "").strip()
    if not old or not new:
        raise ValueError("La categoría no puede estar vacía.")
    if old == new:
        raise ValueError("La categoría nueva es igual a la actual.")

    n_rules = session.query(func.count(Rule.id)).filter(Rule.category == old).scalar()
    n_tx = session.query(func.count(Tx.id)).filter(Tx.my_category == old).scalar()
    if not n_rules and not n_tx:
        raise ValueError(f"No existe la categoría «{old}» (ni en reglas ni en movimientos).")
    n_manual = (
        session.query(func.count(Tx.id))
        .filter(Tx.my_category == old, Tx.skip_category_rules.is_(True))
        .scalar()
    )
    exists = (
        session.query(Rule.id).filter(Rule.category == new).first() is not None
        or session.query(Tx.id).filter(Tx.my_category == new).first() is not None
    )
    if exists and not merge:
        raise ValueError(
            f"La categoría «{new}» ya existe; usa --merge para fusionar «{old}» con ella."
        )

    if apply:
        session.query(Rule).filter(Rule.category == old).update(
            {Rule.category: new}, synchronize_session=False
        )
        session.query(Tx).filter(Tx.my_category == old).update(
            {Tx.my_category: new}, synchronize_session=False
        )
        session.commit()
    return {
        "old": old,
        "new": new,
        "rules": n_rules,
        "transactions": n_tx,
        "manual": n_manual,
        "merge": exists,
        "applied": apply,
    }
