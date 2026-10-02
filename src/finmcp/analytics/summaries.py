from __future__ import annotations

from sqlalchemy.orm import Session

from finmcp.analytics.categories import category_of, spend_by_category
from finmcp.analytics.subscriptions import _merchant_key
from finmcp.db.queries import query_transactions
from finmcp.util import month_bounds


def monthly_summary(session: Session, year: int, month: int) -> dict:
    """Resumen del mes: ingresos, gastos, neto, top comercios y categorías.

    Los movimientos `neutral` no computan en ingresos/gastos; van aparte en `neutral`.
    """
    start, end = month_bounds(year, month)
    all_txs = query_transactions(session, start=start, end=end)
    txs = [t for t in all_txs if not t.neutral]
    neutral_txs = [t for t in all_txs if t.neutral]

    income = sum(t.amount for t in txs if t.type == "credit")
    expense = sum(t.amount for t in txs if t.type == "debit")

    merchant_totals: dict[str, float] = {}
    for t in txs:
        if t.type != "debit":
            continue
        name = t.merchant_name or t.description
        merchant_totals[name] = merchant_totals.get(name, 0.0) + t.amount
    top_merchants = sorted(
        ({"merchant": k, "total": round(v, 2)} for k, v in merchant_totals.items()),
        key=lambda r: r["total"],
        reverse=True,
    )[:10]

    return {
        "period": f"{year}-{month:02d}",
        "income": round(income, 2),
        "expense": round(expense, 2),
        "net": round(income - expense, 2),
        "transactions": len(txs),
        "by_category": spend_by_category(session, start, end),
        "top_merchants": top_merchants,
        "neutral": {
            "in": round(sum(t.amount for t in neutral_txs if t.type == "credit"), 2),
            "out": round(sum(t.amount for t in neutral_txs if t.type == "debit"), 2),
            "transactions": [
                {
                    "date": t.booked_at.date().isoformat(),
                    "type": t.type,
                    "amount": t.amount,
                    "currency": t.currency,
                    "merchant": t.merchant_name or t.description,
                    "category": category_of(t),
                }
                for t in sorted(neutral_txs, key=lambda t: t.booked_at)
            ],
        },
    }
