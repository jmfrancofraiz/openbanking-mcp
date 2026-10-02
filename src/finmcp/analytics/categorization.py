from __future__ import annotations

from sqlalchemy.orm import Session, selectinload

from finmcp.analytics.tagging import sync_rule_tags
from finmcp.db import models


def _haystack(tx: models.Transaction, field: str) -> str:
    if field == "merchant":
        return (tx.merchant_name or "").lower()
    if field == "description":
        return (tx.description or "").lower()
    # "any": comercio + concepto
    return f"{tx.merchant_name or ''} {tx.description or ''}".lower()


def _match(tx: models.Transaction, rule: models.CategoryRule) -> bool:
    if rule.amount_min is not None and tx.amount < rule.amount_min:
        return False
    if rule.amount_max is not None and tx.amount > rule.amount_max:
        return False
    return rule.pattern.lower() in _haystack(tx, rule.field)


def apply_rules(session: Session, only_uncategorized: bool = False) -> int:
    """Asigna `my_category` según las reglas. Gana la de menor `priority`.

    Cada regla puede limitar además por importe (amount_min/amount_max, opcionales).
    Las etiquetas son la unión de las de todas las reglas que casan.
    Devuelve cuántas transacciones cambiaron de categoría.
    """
    rules = (
        session.query(models.CategoryRule)
        .options(selectinload(models.CategoryRule.tags))
        .order_by(models.CategoryRule.priority.asc(), models.CategoryRule.id.asc())
        .all()
    )
    if not rules:
        return 0

    q = session.query(models.Transaction).options(
        selectinload(models.Transaction.tag_links).selectinload(
            models.TransactionTag.tag
        )
    )
    if only_uncategorized:
        q = q.filter(models.Transaction.my_category.is_(None))

    changed = 0
    for tx in q.all():
        matched = [r for r in rules if _match(tx, r)]
        new_cat = matched[0].category if matched else None
        if new_cat is not None and new_cat != tx.my_category:
            tx.my_category = new_cat
            changed += 1
        sync_rule_tags(tx, (t for r in matched for t in r.tags))
    session.commit()
    return changed
