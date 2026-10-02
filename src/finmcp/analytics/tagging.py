from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy.orm import Session, selectinload

from finmcp.db import models
from finmcp.db.models import TAG_EXCLUDED, TAG_MANUAL, TAG_RULE
from finmcp.db.queries import query_transactions
from finmcp.util import normalize_tag

UNTAGGED = "Sin etiqueta"


def unique_tag_names(names: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(normalize_tag(n) for n in names))


def find_tag(session: Session, name: str) -> models.Tag | None:
    return (
        session.query(models.Tag).filter_by(name=normalize_tag(name)).one_or_none()
    )


def _require_tag(session: Session, name: str) -> models.Tag:
    tag = find_tag(session, name)
    if tag is None:
        raise ValueError(f"La etiqueta '{normalize_tag(name)}' no existe.")
    return tag


def get_or_create_tag(session: Session, name: str) -> models.Tag:
    tag = find_tag(session, name)
    if tag is None:
        tag = models.Tag(name=normalize_tag(name))
        session.add(tag)
        session.flush()
    return tag


def _require_tx(session: Session, tx_id: str) -> models.Transaction:
    tx = session.get(models.Transaction, tx_id)
    if tx is None:
        raise ValueError(f"Transacción '{tx_id}' no encontrada.")
    return tx


def _require_rule(session: Session, rule_id: int) -> models.CategoryRule:
    rule = session.get(models.CategoryRule, rule_id)
    if rule is None:
        raise ValueError(f"Regla {rule_id} no encontrada.")
    return rule


def _link(tx: models.Transaction, tag: models.Tag) -> models.TransactionTag | None:
    return next((l for l in tx.tag_links if l.tag is tag), None)


def list_tags(session: Session) -> list[dict]:
    """Catálogo de etiquetas con cuántos movimientos y reglas las usan."""
    tags = (
        session.query(models.Tag)
        .options(selectinload(models.Tag.links), selectinload(models.Tag.rules))
        .order_by(models.Tag.name.asc())
        .all()
    )
    return [
        {
            "name": t.name,
            "transactions": sum(1 for l in t.links if l.source != TAG_EXCLUDED),
            "rules": len(t.rules),
        }
        for t in tags
    ]


def create_tag(session: Session, name: str) -> str:
    tag = get_or_create_tag(session, name)
    session.commit()
    return tag.name


def delete_tag(session: Session, name: str) -> None:
    """Borra una etiqueta del catálogo. Falla si algún movimiento o regla la usa."""
    tag = _require_tag(session, name)
    n_tx = sum(1 for l in tag.links if l.source != TAG_EXCLUDED)
    n_rules = len(tag.rules)
    if n_tx or n_rules:
        raise ValueError(
            f"La etiqueta '{tag.name}' está en uso: {n_tx} transacciones, "
            f"{n_rules} reglas."
        )
    for link in list(tag.links):
        link.transaction.tag_links.remove(link)
        session.delete(link)
    session.delete(tag)
    session.commit()


def tag_transactions(
    session: Session, tx_ids: Iterable[str], names: Iterable[str]
) -> int:
    """Etiqueta a mano. Devuelve cuántos vínculos se crearon o pasaron a manual."""
    txs = [_require_tx(session, i) for i in tx_ids]
    tags = [get_or_create_tag(session, n) for n in unique_tag_names(names)]
    changed = 0
    for tx in txs:
        for tag in tags:
            link = _link(tx, tag)
            if link is None:
                tx.tag_links.append(models.TransactionTag(tag=tag, source=TAG_MANUAL))
                changed += 1
            elif link.source != TAG_MANUAL:
                link.source = TAG_MANUAL
                changed += 1
    session.commit()
    return changed


def untag_transactions(
    session: Session, tx_ids: Iterable[str], names: Iterable[str]
) -> int:
    """Quita etiquetas a mano; quedan excluidas para futuras aplicaciones de reglas."""
    txs = [_require_tx(session, i) for i in tx_ids]
    tags = [_require_tag(session, n) for n in unique_tag_names(names)]
    changed = 0
    for tx in txs:
        for tag in tags:
            link = _link(tx, tag)
            if link is not None and link.source != TAG_EXCLUDED:
                link.source = TAG_EXCLUDED
                changed += 1
    session.commit()
    return changed


def sync_rule_tags(tx: models.Transaction, tags: Iterable[models.Tag]) -> None:
    """Ajusta los vínculos de origen `rule` a `tags`; no toca manuales ni excluidos."""
    wanted = set(tags)
    for link in list(tx.tag_links):
        if link.source == TAG_RULE and link.tag not in wanted:
            tx.tag_links.remove(link)
    present = {l.tag for l in tx.tag_links}
    for tag in wanted - present:
        tx.tag_links.append(models.TransactionTag(tag=tag, source=TAG_RULE))


def add_rule_tags(session: Session, rule_id: int, names: Iterable[str]) -> list[str]:
    rule = _require_rule(session, rule_id)
    for name in unique_tag_names(names):
        tag = get_or_create_tag(session, name)
        if tag not in rule.tags:
            rule.tags.append(tag)
    session.commit()
    return sorted(t.name for t in rule.tags)


def remove_rule_tags(
    session: Session, rule_id: int, names: Iterable[str]
) -> list[str]:
    rule = _require_rule(session, rule_id)
    for name in unique_tag_names(names):
        tag = find_tag(session, name)
        if tag is None or tag not in rule.tags:
            raise ValueError(f"La regla {rule_id} no tiene la etiqueta '{name}'.")
        rule.tags.remove(tag)
    session.commit()
    return sorted(t.name for t in rule.tags)


def spend_by_tag(
    session: Session,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict]:
    """Gasto (débitos) por etiqueta. Un movimiento con varias etiquetas suma en todas."""
    txs = query_transactions(session, start=start, end=end, type="debit", neutral=False)
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for t in txs:
        for name in t.tag_names or [UNTAGGED]:
            totals[name] = totals.get(name, 0.0) + t.amount
            counts[name] = counts.get(name, 0) + 1
    rows = [
        {"tag": name, "total": round(total, 2), "count": counts[name]}
        for name, total in totals.items()
    ]
    rows.sort(key=lambda r: r["total"], reverse=True)
    return rows
