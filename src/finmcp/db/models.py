from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Table
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


TAG_MANUAL = "manual"
TAG_RULE = "rule"
# Quitada a mano: se conserva para que las reglas no la vuelvan a poner.
TAG_EXCLUDED = "excluded"

category_rule_tags = Table(
    "category_rule_tags",
    Base.metadata,
    Column("rule_id", ForeignKey("category_rules.id"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id"), primary_key=True),
)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # provider_account_id
    bank_id: Mapped[str] = mapped_column(String, default="default")
    name: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)
    currency: Mapped[str] = mapped_column(String)
    iban: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_json: Mapped[dict] = mapped_column(JSON, default=dict)

    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="account"
    )


class Balance(Base):
    __tablename__ = "balances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    available: Mapped[float | None] = mapped_column(Float, nullable=True)
    current: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime)


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # provider tx id
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String)
    booked_at: Mapped[datetime] = mapped_column(DateTime)
    description: Mapped[str] = mapped_column(String, default="")
    type: Mapped[str] = mapped_column(String)  # debit | credit
    merchant_name: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_category: Mapped[str | None] = mapped_column(String, nullable=True)
    my_category: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_json: Mapped[dict] = mapped_column(JSON, default=dict)

    account: Mapped["Account"] = relationship(back_populates="transactions")
    tag_links: Mapped[list["TransactionTag"]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )

    @property
    def tag_names(self) -> list[str]:
        return sorted(l.tag.name for l in self.tag_links if l.source != TAG_EXCLUDED)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True)  # normalizado (strip+lower)

    links: Mapped[list["TransactionTag"]] = relationship(back_populates="tag")
    rules: Mapped[list["CategoryRule"]] = relationship(
        secondary=category_rule_tags, back_populates="tags"
    )


class TransactionTag(Base):
    __tablename__ = "transaction_tags"

    transaction_id: Mapped[str] = mapped_column(
        ForeignKey("transactions.id"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id"), primary_key=True)
    source: Mapped[str] = mapped_column(String, default=TAG_MANUAL)  # manual|rule|excluded

    transaction: Mapped["Transaction"] = relationship(back_populates="tag_links")
    tag: Mapped["Tag"] = relationship(back_populates="links")


class CategoryRule(Base):
    __tablename__ = "category_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pattern: Mapped[str] = mapped_column(String)  # subcadena, case-insensitive
    category: Mapped[str] = mapped_column(String)
    field: Mapped[str] = mapped_column(String, default="any")  # merchant|description|any
    priority: Mapped[int] = mapped_column(Integer, default=100)  # menor = antes
    # Condición opcional de importe; None = sin límite (el importe siempre es positivo).
    amount_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount_max: Mapped[float | None] = mapped_column(Float, nullable=True)

    tags: Mapped[list["Tag"]] = relationship(
        secondary=category_rule_tags, back_populates="rules"
    )


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bank_id: Mapped[str] = mapped_column(String, default="default")
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String, default="running")
    accounts_synced: Mapped[int] = mapped_column(Integer, default=0)
    tx_added: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str | None] = mapped_column(String, nullable=True)
