"""Analítica del panel web (solo lectura).

Semántica «devoluciones restadas», común a todas las vistas del panel:

- Categoría de ingreso: empieza por «Ingresos» o es «Otros ingresos» / «Intereses».
  Cualquier otra es de gasto. Se clasifica por categoría, no por el signo.
- Ingresos = abonos − cargos de las categorías de ingreso.
- Gastos = cargos − abonos del resto: una devolución reduce el gasto de su categoría.
- Ahorro (`net`) = Σ abonos − Σ cargos; cuadra con `monthly_summary` / `finmcp summary`.
- Los movimientos `neutral` (traspasos, liquidaciones de tarjeta...) no computan.

Jerarquía: la «categoría» del panel (`group`) es el texto anterior al primer « - »
(«Vivienda - Hipoteca» → «Vivienda»); en ingresos es la categoría completa. La
«subcategoría» es la categoría efectiva (`category_of`), con su `label` corto.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from finmcp.analytics.categories import category_of
from finmcp.db import models
from finmcp.db.queries import latest_sync_runs, query_transactions

INCOME = "income"
EXPENSE = "expense"
KINDS = (INCOME, EXPENSE)

INCOME_PREFIX = "Ingresos"
EXTRA_INCOME_CATEGORIES = frozenset({"Otros ingresos", "Intereses"})
GENERAL = "General"
SEP = " - "


# --- clasificación ---------------------------------------------------------


def is_income_category(category: str) -> bool:
    return category.startswith(INCOME_PREFIX) or category in EXTRA_INCOME_CATEGORIES


def kind_of(category: str) -> str:
    return INCOME if is_income_category(category) else EXPENSE


def group_of(category: str) -> str:
    """Categoría del panel: prefijo antes de « - » (o la categoría completa si es de ingreso)."""
    if is_income_category(category):
        return category
    return category.split(SEP, 1)[0]


def subcategory_label(category: str) -> str:
    """Nombre corto de la subcategoría: lo que sigue a «<categoría> - », o «General»."""
    group = group_of(category)
    prefix = group + SEP
    if category.startswith(prefix):
        return category[len(prefix):]
    return GENERAL


def value_of(tx: models.Transaction, category: str | None = None) -> float:
    """Importe con el signo de su tipo (ingreso o gasto): positivo si suma, negativo si resta."""
    cat = category or category_of(tx)
    credit = tx.type == "credit"
    if is_income_category(cat):
        return tx.amount if credit else -tx.amount
    return -tx.amount if credit else tx.amount


# --- fechas ------------------------------------------------------------------


def month_key(value: date | datetime) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def parse_month(value: str) -> date:
    return datetime.strptime(value, "%Y-%m").date()


def days_in(value: date) -> int:
    return calendar.monthrange(value.year, value.month)[1]


def month_range(first: date, last: date) -> list[str]:
    """Meses de `first` a `last`, ambos incluidos, como 'AAAA-MM'."""
    out = []
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _day_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _day_end(value: date) -> datetime:
    # Mismo criterio que util.month_bounds (23:59:59), para cuadrar con el CLI.
    return datetime.combine(value, time(23, 59, 59), tzinfo=timezone.utc)


def _round(value: float) -> float:
    return round(value, 2) + 0.0  # + 0.0 evita «-0.0»


def _computable(session: Session, start: date, end: date) -> list[models.Transaction]:
    return query_transactions(session, start=_day_start(start), end=_day_end(end), neutral=False)


def _first_booked(session: Session) -> date | None:
    first = session.query(func.min(models.Transaction.booked_at)).scalar()
    return first.date() if first else None


# --- agregados ---------------------------------------------------------------


def _aggregate(rows: list[tuple[str, float]]) -> dict:
    """Agrupa (categoría, valor) en categorías del panel y subcategorías, de mayor a menor."""
    groups: dict[str, dict] = {}
    for cat, val in rows:
        g = groups.setdefault(group_of(cat), {"total": 0.0, "count": 0, "cats": {}})
        g["total"] += val
        g["count"] += 1
        c = g["cats"].setdefault(cat, [0.0, 0])
        c[0] += val
        c[1] += 1
    out = []
    for name, g in groups.items():
        cats = [
            {"category": c, "label": subcategory_label(c), "total": _round(v[0]), "count": v[1]}
            for c, v in g["cats"].items()
        ]
        cats.sort(key=lambda r: (-r["total"], r["category"]))
        out.append({"group": name, "total": _round(g["total"]), "count": g["count"],
                    "categories": cats})
    out.sort(key=lambda r: (-r["total"], r["group"]))
    return {
        "total": _round(sum(val for _, val in rows)),
        "count": len(rows),
        "groups": out,
    }


def breakdown(session: Session, start: date, end: date) -> dict:
    """Ingresos y gastos por categoría y subcategoría entre dos fechas (ambas incluidas)."""
    txs = _computable(session, start, end)
    split: dict[str, list[tuple[str, float]]] = {INCOME: [], EXPENSE: []}
    refunds = bills = 0.0
    for t in txs:
        cat = category_of(t)
        kind = kind_of(cat)
        val = value_of(t, cat)
        split[kind].append((cat, val))
        if kind == EXPENSE:
            if t.type == "credit":
                refunds += t.amount
            if t.is_bill:
                bills += val
    credits = sum(t.amount for t in txs if t.type == "credit")
    debits = sum(t.amount for t in txs if t.type == "debit")
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "income": _aggregate(split[INCOME]),
        "expense": _aggregate(split[EXPENSE]),
        "net": _round(credits - debits),
        "refunds": _round(refunds),
        "bills": _round(bills),
        "count": len(txs),
    }


def monthly(session: Session, today: date, day: int | None = None) -> list[dict]:
    """Totales de cada mes, del primero con movimientos al actual.

    Con `day`, cada mes se recorta del día 1 al `day` (o a su último día si es más
    corto): es la comparación «a la misma fecha» con el mes en curso.
    """
    first = _first_booked(session) or today
    months = month_range(first, today)
    current = month_key(today)
    acc = {m: {"credit": 0.0, "debit": 0.0, "income": 0.0, "expense": 0.0,
               "bills": 0.0, "refunds": 0.0, "count": 0} for m in months}
    start = date(first.year, first.month, 1)
    end = date(today.year, today.month, days_in(today))
    for t in _computable(session, start, end):
        booked = t.booked_at.date()
        row = acc.get(month_key(booked))
        if row is None or (day is not None and booked.day > day):
            continue
        cat = category_of(t)
        kind = kind_of(cat)
        val = value_of(t, cat)
        row[t.type] = row.get(t.type, 0.0) + t.amount
        row[kind] += val
        row["count"] += 1
        if kind == EXPENSE:
            if t.type == "credit":
                row["refunds"] += t.amount
            if t.is_bill:
                row["bills"] += val
    out = []
    for m in months:
        r = acc[m]
        out.append({
            "month": m,
            "income": _round(r["income"]),
            "expense": _round(r["expense"]),
            "net": _round(r["credit"] - r["debit"]),
            "bills": _round(r["bills"]),
            "refunds": _round(r["refunds"]),
            "count": r["count"],
            "partial": m == current,
            "cut_day": min(day, days_in(parse_month(m))) if day is not None else None,
        })
    return out


def series(session: Session, start_month: str, end_month: str) -> dict:
    """Total de cada categoría del panel por mes, alineado con `months` (ingresos y gastos)."""
    first, last = parse_month(start_month), parse_month(end_month)
    months = month_range(first, last)
    index = {m: i for i, m in enumerate(months)}
    out: dict[str, dict[str, list[float]]] = {INCOME: {}, EXPENSE: {}}
    for t in _computable(session, first, date(last.year, last.month, days_in(last))):
        cat = category_of(t)
        values = out[kind_of(cat)].setdefault(group_of(cat), [0.0] * len(months))
        values[index[month_key(t.booked_at)]] += value_of(t, cat)
    return {
        "months": months,
        INCOME: {g: [_round(v) for v in vals] for g, vals in out[INCOME].items()},
        EXPENSE: {g: [_round(v) for v in vals] for g, vals in out[EXPENSE].items()},
    }


def transactions(
    session: Session,
    start: date,
    end: date,
    kind: str,
    group: str | None = None,
    category: str | None = None,
) -> list[dict]:
    """Movimientos computables de un tipo (y opcionalmente de una categoría o subcategoría)."""
    banks = dict(session.query(models.Account.id, models.Account.bank_id).all())
    rows = []
    for t in _computable(session, start, end):
        cat = category_of(t)
        if kind_of(cat) != kind:
            continue
        if category is not None and cat != category:
            continue
        if group is not None and group_of(cat) != group:
            continue
        rows.append({
            "id": t.id,
            "date": t.booked_at.date().isoformat(),
            "amount": t.amount if t.type == "credit" else -t.amount,
            "category": cat,
            "group": group_of(cat),
            "label": subcategory_label(cat),
            "merchant": t.merchant_name,
            "description": t.description,
            "bank": banks.get(t.account_id, ""),
            "tags": t.tag_names,
            "is_bill": bool(t.is_bill),
        })
    rows.sort(key=lambda r: (r["date"], r["id"]))
    return rows


def meta(session: Session, today: date) -> dict:
    """Contexto del panel: meses disponibles, mes en curso, cobertura y última sincronización."""
    first = _first_booked(session) or today
    coverage_rows = (
        session.query(models.Account.bank_id, func.min(models.Transaction.booked_at))
        .join(models.Transaction, models.Transaction.account_id == models.Account.id)
        .group_by(models.Account.bank_id)
        .all()
    )
    coverage = sorted(
        ({"bank": bank, "first_month": month_key(ts)} for bank, ts in coverage_rows if ts),
        key=lambda r: (r["first_month"], r["bank"]),
    )
    last_sync = sorted(
        (
            {
                "bank": r.bank_id,
                "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in latest_sync_runs(session)
        ),
        key=lambda r: r["bank"],
    )
    return {
        "today": today.isoformat(),
        "current_month": month_key(today),
        "cut_day": today.day,
        "days_in_month": days_in(today),
        "months": month_range(first, today),
        "coverage": coverage,
        "last_sync": last_sync,
    }
