from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, selectinload

from finmcp.db import models
from finmcp.util import normalize_tag


def list_accounts(session: Session) -> list[models.Account]:
    return session.query(models.Account).all()


def latest_sync_runs(session: Session) -> list[models.SyncRun]:
    """Último SyncRun por bank_id."""
    subq = (
        session.query(
            models.SyncRun.bank_id,
            func.max(models.SyncRun.started_at).label("ts"),
        )
        .group_by(models.SyncRun.bank_id)
        .subquery()
    )
    return (
        session.query(models.SyncRun)
        .join(
            subq,
            (models.SyncRun.bank_id == subq.c.bank_id)
            & (models.SyncRun.started_at == subq.c.ts),
        )
        .all()
    )


def latest_balances(session: Session) -> list[models.Balance]:
    """Último snapshot de saldo por cuenta."""
    subq = (
        session.query(
            models.Balance.account_id,
            func.max(models.Balance.snapshot_at).label("ts"),
        )
        .group_by(models.Balance.account_id)
        .subquery()
    )
    return (
        session.query(models.Balance)
        .join(
            subq,
            (models.Balance.account_id == subq.c.account_id)
            & (models.Balance.snapshot_at == subq.c.ts),
        )
        .all()
    )


def query_transactions(
    session: Session,
    account_id: str | None = None,
    bank_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    type: str | None = None,
    text: str | None = None,
    tag: str | None = None,
    limit: int | None = None,
) -> list[models.Transaction]:
    q = session.query(models.Transaction).options(
        selectinload(models.Transaction.tag_links).selectinload(
            models.TransactionTag.tag
        )
    )
    if account_id:
        q = q.filter(models.Transaction.account_id == account_id)
    if bank_id:
        q = q.join(models.Account).filter(models.Account.bank_id == bank_id)
    if start:
        q = q.filter(models.Transaction.booked_at >= start)
    if end:
        q = q.filter(models.Transaction.booked_at <= end)
    if type:
        q = q.filter(models.Transaction.type == type)
    if text:
        like = f"%{text.lower()}%"
        q = q.filter(
            or_(
                func.lower(models.Transaction.description).like(like),
                func.lower(func.coalesce(models.Transaction.merchant_name, "")).like(
                    like
                ),
            )
        )
    if tag:
        q = q.filter(
            models.Transaction.tag_links.any(
                and_(
                    models.TransactionTag.source != models.TAG_EXCLUDED,
                    models.TransactionTag.tag.has(
                        models.Tag.name == normalize_tag(tag)
                    ),
                )
            )
        )
    q = q.order_by(models.Transaction.booked_at.desc())
    if limit:
        q = q.limit(limit)
    return q.all()
