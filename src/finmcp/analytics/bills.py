from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from finmcp.db.queries import query_transactions
from finmcp.util import merchant_key


def _cadence(median_days: float) -> str:
    """Etiqueta descriptiva de periodicidad a partir de la mediana de intervalos.

    Puramente informativa: no decide si una transacción es recibo (eso lo
    marca `is_bill`, heredado de la regla ganadora), solo describe el ritmo
    observado de cargos ya confirmados como recibo.
    """
    if 25 <= median_days <= 35:
        return "mensual"
    if 6 <= median_days <= 8:
        return "semanal"
    if 13 <= median_days <= 16:
        return "quincenal"
    if 85 <= median_days <= 95:
        return "trimestral"
    if 350 <= median_days <= 380:
        return "anual"
    return f"~{round(median_days)}d"


def list_bills(session: Session, lookback_months: int = 6) -> list[dict]:
    """Recibos / pagos recurrentes: transacciones marcadas `is_bill=True`.

    A diferencia de la antigua heurística (agrupar por comercio + exigir
    importe estable y una cadencia regular), el recibo lo decide la regla de
    categorización que gana (`CategoryRule.is_bill`, propagado a
    `Transaction.is_bill` por `apply_rules`). Esto evita falsos negativos con
    comercios enmascarados o importes variables, y falsos positivos de
    compras puntuales repetidas por casualidad.
    """
    start = datetime.now(timezone.utc) - timedelta(days=30 * lookback_months)
    txs = query_transactions(session, start=start, type="debit", is_bill=True)

    groups: dict[str, list] = {}
    for t in txs:
        groups.setdefault(merchant_key(t), []).append(t)

    bills: list[dict] = []
    for items in groups.values():
        amounts = [i.amount for i in items]
        dates = sorted(i.booked_at for i in items)
        diffs = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        cadence = _cadence(statistics.median(diffs)) if diffs else "única"
        bills.append(
            {
                "merchant": items[0].merchant_name or items[0].description,
                "amount": round(sum(amounts) / len(amounts), 2),
                "currency": items[0].currency,
                "occurrences": len(items),
                "cadence": cadence,
                "last_charge": dates[-1].date().isoformat(),
            }
        )
    bills.sort(key=lambda b: b["amount"], reverse=True)
    return bills
