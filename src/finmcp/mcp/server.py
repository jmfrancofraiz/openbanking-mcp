from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from finmcp.analytics import tagging
from finmcp.analytics.anomalies import detect_unusual_charges
from finmcp.analytics.categories import category_of, spend_by_category
from finmcp.analytics.subscriptions import detect_subscriptions
from finmcp.analytics.summaries import monthly_summary
from finmcp.db import models, queries
from finmcp.db.session import SessionLocal, init_db
from finmcp.util import parse_date

mcp = FastMCP("openbanking-mcp")


def _tx_dict(t: models.Transaction) -> dict:
    return {
        "id": t.id,
        "account_id": t.account_id,
        "amount": t.amount,
        "currency": t.currency,
        "date": t.booked_at.date().isoformat(),
        "description": t.description,
        "type": t.type,
        "merchant": t.merchant_name,
        "category": category_of(t),
        "neutral": t.neutral,
        "skip_category_rules": t.skip_category_rules,
        "tags": t.tag_names,
    }


@mcp.tool()
def list_accounts() -> list[dict]:
    """Lista las cuentas bancarias sincronizadas."""
    with SessionLocal() as s:
        return [
            {
                "id": a.id,
                "bank_id": a.bank_id,
                "name": a.name,
                "type": a.type,
                "currency": a.currency,
                "iban": a.iban,
            }
            for a in queries.list_accounts(s)
        ]


@mcp.tool()
def get_balances() -> list[dict]:
    """Saldo más reciente de cada cuenta."""
    with SessionLocal() as s:
        return [
            {
                "account_id": b.account_id,
                "available": b.available,
                "current": b.current,
                "currency": b.currency,
                "as_of": b.snapshot_at.isoformat(),
            }
            for b in queries.latest_balances(s)
        ]


@mcp.tool()
def get_transactions(
    account_id: str | None = None,
    bank_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
    type: str | None = None,
    tag: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Movimientos filtrados por cuenta, banco, fechas (YYYY-MM-DD), tipo (debit/credit) y etiqueta."""
    with SessionLocal() as s:
        txs = queries.query_transactions(
            s,
            account_id=account_id,
            bank_id=bank_id,
            start=parse_date(start),
            end=parse_date(end),
            type=type,
            tag=tag,
            limit=limit,
        )
        return [_tx_dict(t) for t in txs]


@mcp.tool()
def search_transactions(
    query: str, bank_id: str | None = None, tag: str | None = None, limit: int = 50
) -> list[dict]:
    """Busca movimientos por texto en el comercio o el concepto (opcionalmente por etiqueta)."""
    with SessionLocal() as s:
        txs = queries.query_transactions(
            s, bank_id=bank_id, text=query, tag=tag, limit=limit
        )
        return [_tx_dict(t) for t in txs]


@mcp.tool()
def spend_by_category_tool(
    start: str | None = None, end: str | None = None
) -> list[dict]:
    """Gasto agregado por categoría en un periodo (fechas YYYY-MM-DD)."""
    with SessionLocal() as s:
        return spend_by_category(s, parse_date(start), parse_date(end))


@mcp.tool()
def list_subscriptions(lookback_months: int = 6) -> list[dict]:
    """Cargos recurrentes detectados (suscripciones / domiciliaciones)."""
    with SessionLocal() as s:
        return detect_subscriptions(s, lookback_months=lookback_months)


@mcp.tool()
def unusual_charges(
    start: str | None = None, end: str | None = None
) -> list[dict]:
    """Cargos atípicos respecto al histórico de cada comercio."""
    with SessionLocal() as s:
        return detect_unusual_charges(s, parse_date(start), parse_date(end))


@mcp.tool()
def monthly_summary_tool(year: int, month: int) -> dict:
    """Resumen mensual: ingresos, gastos, neto, top comercios y categorías.

    Los movimientos neutrales (traspasos, etc.) no computan y se listan en `neutral`.
    """
    with SessionLocal() as s:
        return monthly_summary(s, year, month)


@mcp.tool()
def sync_status() -> list[dict]:
    """Estado de la última sincronización, por banco."""
    with SessionLocal() as s:
        runs = queries.latest_sync_runs(s)
        if not runs:
            return [{"status": "never", "detail": "Ejecuta `finmcp sync`."}]
        return [
            {
                "bank_id": run.bank_id,
                "status": run.status,
                "started_at": run.started_at.isoformat(),
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "accounts_synced": run.accounts_synced,
                "tx_added": run.tx_added,
            }
            for run in runs
        ]


@mcp.tool()
def sync(
    from_date: str | None = None,
    to_date: str | None = None,
    bank_id: str | None = None,
) -> list[dict]:
    """Sincroniza cuentas, saldos y movimientos desde el banco (fechas YYYY-MM-DD).

    Si se indica `bank_id` solo sincroniza ese banco; si no, todos los configurados.
    """
    from finmcp.sync.service import run_sync

    runs = run_sync(from_date, to_date, bank_id=bank_id)
    return [
        {
            "bank_id": run.bank_id,
            "status": run.status,
            "accounts_synced": run.accounts_synced,
            "tx_added": run.tx_added,
            "detail": run.detail,
        }
        for run in runs
    ]


@mcp.tool()
def list_banks() -> list[dict]:
    """Bancos configurados y si ya tienen vínculo (auth) guardado."""
    from finmcp.config import settings
    from finmcp.providers.enablebanking.auth import has_link

    return [
        {
            "id": b.id,
            "aspsp_name": b.aspsp_name,
            "country": b.country,
            "linked": has_link(b.id),
        }
        for b in settings.banks
    ]


@mcp.tool()
def list_institutions(country: str | None = None) -> list[dict]:
    """Entidades disponibles en Enable Banking para un país ISO-3166 (p.ej. es)."""
    from finmcp.config import settings
    from finmcp.providers.enablebanking.auth import list_aspsps

    rows = list_aspsps(country or settings.enablebanking_country)
    return [{"name": a.get("name", ""), "country": a.get("country", "")} for a in rows]


@mcp.tool()
def import_csv(
    path: str,
    iban: str | None = None,
    account_id: str | None = None,
    delimiter: str | None = None,
) -> dict:
    """Importa movimientos desde un CSV/Excel local (histórico anterior a 90 días PSD2).

    Deduplica contra lo ya existente. `iban`/`account_id` fijan la cuenta destino
    si el fichero no trae columna de cuenta y hay varias cuentas.
    """
    from finmcp.analytics.categorization import apply_rules
    from finmcp.importers.csv_import import import_file

    with SessionLocal() as s:
        added, skipped = import_file(s, path, iban, account_id, delimiter)
        changed = apply_rules(s)
    return {"added": added, "skipped": skipped, "recategorized": changed}


@mcp.tool()
def categorize(only_new: bool = False) -> dict:
    """Reaplica las reglas de categorización (solo a las sin categoría si `only_new`)."""
    from finmcp.analytics.categorization import apply_rules

    with SessionLocal() as s:
        return {"recategorized": apply_rules(s, only_uncategorized=only_new)}


@mcp.tool()
def add_category_rule(
    pattern: str,
    category: str,
    field: str = "any",
    priority: int = 100,
    tags: list[str] | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
    neutral: bool = False,
) -> dict:
    """Añade una regla de categorización y la aplica a los movimientos existentes.

    `pattern` es una subcadena (case-insensitive); `field` es merchant | description | any;
    menor `priority` se evalúa antes. `tags` se añaden a los movimientos que casen
    (se crean en el catálogo si no existen). `amount_min`/`amount_max` limitan la regla
    a movimientos dentro de ese rango de importe (ambos opcionales). Con `neutral` los
    movimientos que casen no computan como ingreso/gasto (traspasos, liquidaciones de tarjeta).
    """
    from finmcp.analytics.categorization import apply_rules

    if field not in ("merchant", "description", "any"):
        raise ValueError("field debe ser merchant, description o any.")
    with SessionLocal() as s:
        rule = models.CategoryRule(
            pattern=pattern,
            category=category,
            field=field,
            priority=priority,
            amount_min=amount_min,
            amount_max=amount_max,
            neutral=neutral,
        )
        rule.tags = [tagging.get_or_create_tag(s, t) for t in tagging.unique_tag_names(tags or [])]
        s.add(rule)
        s.commit()
        rule_id = rule.id
        changed = apply_rules(s)
    return {"id": rule_id, "recategorized": changed}


@mcp.tool()
def set_rule_neutral(rule_id: int, neutral: bool = True) -> dict:
    """Marca (o desmarca) una regla como neutral y reaplica las reglas."""
    from finmcp.analytics.categorization import apply_rules

    with SessionLocal() as s:
        rule = s.get(models.CategoryRule, rule_id)
        if rule is None:
            raise ValueError(f"No existe la regla {rule_id}.")
        rule.neutral = neutral
        s.commit()
        changed = apply_rules(s)
    return {"id": rule_id, "neutral": neutral, "recategorized": changed}


@mcp.tool()
def add_rule_tags(rule_id: int, tags: list[str]) -> dict:
    """Añade etiquetas a una regla de categoría y reaplica las reglas."""
    from finmcp.analytics.categorization import apply_rules

    with SessionLocal() as s:
        names = tagging.add_rule_tags(s, rule_id, tags)
        apply_rules(s)
    return {"id": rule_id, "tags": names}


@mcp.tool()
def remove_rule_tags(rule_id: int, tags: list[str]) -> dict:
    """Quita etiquetas de una regla de categoría y reaplica las reglas."""
    from finmcp.analytics.categorization import apply_rules

    with SessionLocal() as s:
        names = tagging.remove_rule_tags(s, rule_id, tags)
        apply_rules(s)
    return {"id": rule_id, "tags": names}


@mcp.tool()
def list_category_rules() -> list[dict]:
    """Reglas de categorización, en orden de evaluación."""
    with SessionLocal() as s:
        rows = (
            s.query(models.CategoryRule)
            .order_by(models.CategoryRule.priority.asc(), models.CategoryRule.id.asc())
            .all()
        )
        return [
            {
                "id": r.id,
                "pattern": r.pattern,
                "category": r.category,
                "field": r.field,
                "priority": r.priority,
                "amount_min": r.amount_min,
                "amount_max": r.amount_max,
                "neutral": r.neutral,
                "tags": sorted(t.name for t in r.tags),
            }
            for r in rows
        ]


@mcp.tool()
def list_tags() -> list[dict]:
    """Catálogo de etiquetas con cuántos movimientos y reglas las usan."""
    with SessionLocal() as s:
        return tagging.list_tags(s)


@mcp.tool()
def create_tag(name: str) -> dict:
    """Crea una etiqueta en el catálogo (se normaliza a minúsculas)."""
    with SessionLocal() as s:
        return {"name": tagging.create_tag(s, name)}


@mcp.tool()
def delete_tag(name: str) -> dict:
    """Borra una etiqueta del catálogo. Falla si algún movimiento o regla la usa."""
    with SessionLocal() as s:
        tagging.delete_tag(s, name)
    return {"deleted": name}


@mcp.tool()
def tag_transactions(transaction_ids: list[str], tags: list[str]) -> dict:
    """Etiqueta movimientos a mano (crea las etiquetas que no existan)."""
    with SessionLocal() as s:
        return {"changed": tagging.tag_transactions(s, transaction_ids, tags)}


@mcp.tool()
def untag_transactions(transaction_ids: list[str], tags: list[str]) -> dict:
    """Quita etiquetas a movimientos; las reglas no volverán a ponerlas."""
    with SessionLocal() as s:
        return {"changed": tagging.untag_transactions(s, transaction_ids, tags)}


@mcp.tool()
def set_transactions_neutral(transaction_ids: list[str], neutral: bool = True) -> dict:
    """Marca (o desmarca) movimientos como neutrales: no computan como ingreso/gasto.

    Si una regla casa con el movimiento, al reaplicar reglas prevalece la de la regla.
    """
    with SessionLocal() as s:
        txs = (
            s.query(models.Transaction)
            .filter(models.Transaction.id.in_(transaction_ids))
            .all()
        )
        missing = set(transaction_ids) - {t.id for t in txs}
        if missing:
            raise ValueError(f"No existen los movimientos: {', '.join(sorted(missing))}")
        changed = 0
        for t in txs:
            if t.neutral != neutral:
                t.neutral = neutral
                changed += 1
        s.commit()
    return {"changed": changed}


@mcp.tool()
def recategorize_transaction(transaction_id: str, category: str) -> dict:
    """Recategoriza a mano un movimiento: fija `my_category` y activa `skip_category_rules`.

    Mientras `skip_category_rules` esté activo, reaplicar las reglas (sync,
    categorize, cambios en reglas…) no volverá a tocar esta transacción.
    """
    with SessionLocal() as s:
        tx = s.get(models.Transaction, transaction_id)
        if tx is None:
            raise ValueError(f"No existe el movimiento {transaction_id}.")
        tx.my_category = category
        tx.skip_category_rules = True
        s.commit()
    return {"id": transaction_id, "category": category, "skip_category_rules": True}


@mcp.tool()
def set_transactions_skip_rules(transaction_ids: list[str], skip: bool = True) -> dict:
    """Activa (o desactiva con `skip=False`) `skip_category_rules` en movimientos.

    Con el flag activo, `apply_rules` no toca esos movimientos; al desactivarlo,
    las reglas vuelven a aplicárseles en el siguiente ciclo.
    """
    with SessionLocal() as s:
        txs = (
            s.query(models.Transaction)
            .filter(models.Transaction.id.in_(transaction_ids))
            .all()
        )
        missing = set(transaction_ids) - {t.id for t in txs}
        if missing:
            raise ValueError(f"No existen los movimientos: {', '.join(sorted(missing))}")
        changed = 0
        for t in txs:
            if t.skip_category_rules != skip:
                t.skip_category_rules = skip
                changed += 1
        s.commit()
    return {"changed": changed}


@mcp.tool()
def spend_by_tag_tool(start: str | None = None, end: str | None = None) -> list[dict]:
    """Gasto por etiqueta en un periodo (YYYY-MM-DD). Un movimiento con varias etiquetas suma en todas."""
    with SessionLocal() as s:
        return tagging.spend_by_tag(s, parse_date(start), parse_date(end))


def _wrap_bearer_auth(app, token: str):
    """Middleware mínimo: exige `Authorization: Bearer <token>` en cada petición."""
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class BearerAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.headers.get("authorization", "") != f"Bearer {token}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    app.add_middleware(BearerAuth)
    return app


def main(http: bool = False, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Arranca el servidor MCP.

    - stdio (por defecto): para Claude Desktop / Cursor (proceso local).
    - http: transporte Streamable HTTP en /mcp para conectores remotos (ChatGPT).
      Si FINMCP_HTTP_TOKEN está definido, exige ese bearer token.
    """
    import os

    init_db()
    if not http:
        mcp.run()
        return

    import uvicorn

    app = mcp.streamable_http_app()
    token = os.environ.get("FINMCP_HTTP_TOKEN", "")
    if token:
        app = _wrap_bearer_auth(app, token)
    else:
        print(
            "AVISO: sin FINMCP_HTTP_TOKEN; el endpoint queda SIN autenticación. "
            "No lo expongas públicamente con datos bancarios."
        )
    mcp.settings.host, mcp.settings.port = host, port
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
