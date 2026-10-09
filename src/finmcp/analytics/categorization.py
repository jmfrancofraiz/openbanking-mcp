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
    """Asigna `my_category`, `neutral` e `is_bill` según las reglas. Gana la de menor `priority`.

    Cada regla puede limitar además por importe (amount_min/amount_max, opcionales).
    Las etiquetas son la unión de las de todas las reglas que casan.
    Las transacciones con `skip_category_rules=True` (categoría fijada a mano, p.ej.
    vía `finmcp recategorize`) se saltan por completo: no se toca su categoría,
    ni su `neutral`, ni su `is_bill`, ni sus etiquetas de reglas.
    Sin reglas configuradas no se asigna nada, pero las etiquetas de origen `rule`
    se retiran igualmente.
    Devuelve cuántas transacciones cambiaron de categoría, `neutral` o `is_bill`.
    """
    rules = (
        session.query(models.CategoryRule)
        .options(selectinload(models.CategoryRule.tags))
        .order_by(models.CategoryRule.priority.asc(), models.CategoryRule.id.asc())
        .all()
    )

    q = session.query(models.Transaction).options(
        selectinload(models.Transaction.tag_links).selectinload(
            models.TransactionTag.tag
        )
    )
    if only_uncategorized:
        q = q.filter(models.Transaction.my_category.is_(None))

    changed = 0
    for tx in q.all():
        if tx.skip_category_rules:
            # Override manual (`finmcp recategorize`): las reglas no tocan esta tx.
            continue
        matched = [r for r in rules if _match(tx, r)]
        if matched:
            winner = matched[0]
            if (
                winner.category != tx.my_category
                or winner.neutral != tx.neutral
                or winner.is_bill != tx.is_bill
            ):
                tx.my_category = winner.category
                tx.neutral = winner.neutral
                tx.is_bill = winner.is_bill
                changed += 1
        sync_rule_tags(tx, (t for r in matched for t in r.tags))
    session.commit()
    return changed
