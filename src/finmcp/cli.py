from __future__ import annotations

import typer

app = typer.Typer(
    help="Finanzas personales sobre Open Banking / Enable Banking (solo lectura)."
)
rules_app = typer.Typer(help="Reglas de categorización personalizadas.")
app.add_typer(rules_app, name="rules")
banks_app = typer.Typer(help="Bancos configurados (multi-banco vía Enable Banking).")
app.add_typer(banks_app, name="banks")


def _resolve_bank(bank_id: str | None):
    """Resuelve un `BankConfig` por id, o el único configurado si no hay ambigüedad."""
    from finmcp.config import settings

    banks = settings.banks
    if not banks:
        raise typer.BadParameter(
            "No hay ningún banco configurado. Define ENABLEBANKING_ASPSP_NAME "
            "o FINMCP_BANK_1_ASPSP_NAME en .env."
        )
    if bank_id:
        for b in banks:
            if b.id == bank_id:
                return b
        ids = ", ".join(b.id for b in banks)
        raise typer.BadParameter(f"Banco '{bank_id}' no encontrado. Disponibles: {ids}")
    if len(banks) == 1:
        return banks[0]
    ids = ", ".join(b.id for b in banks)
    raise typer.BadParameter(f"Hay varios bancos configurados; indica --bank. Disponibles: {ids}")


@app.command()
def auth(
    bank: str = typer.Option(None, "--bank", help="Id del banco (ver `finmcp banks list`)"),
) -> None:
    """Autoriza con Enable Banking y guarda las credenciales cifradas."""
    from finmcp.providers.enablebanking.auth import run_link_flow

    run_link_flow(_resolve_bank(bank))


@banks_app.command("list")
def banks_list() -> None:
    """Lista los bancos configurados y si ya tienen vínculo (auth) guardado."""
    from finmcp.config import settings
    from finmcp.providers.enablebanking.auth import has_link

    banks = settings.banks
    if not banks:
        typer.echo("No hay ningún banco configurado. Ver .env.example.")
        raise typer.Exit()
    for b in banks:
        vinculado = "sí" if has_link(b.id) else "no"
        typer.echo(f"- {b.id} · {b.aspsp_name or 's/nombre'} · {b.country} · vinculado: {vinculado}")


@app.command()
def institutions(
    country: str = typer.Option(
        None, help="Código país ISO-3166 (p.ej. es). Por defecto, el del proveedor."
    ),
) -> None:
    """Lista las entidades de Enable Banking (para fijar `ENABLEBANKING_ASPSP_NAME`)."""
    from finmcp.config import settings
    from finmcp.providers.enablebanking.auth import list_aspsps

    code = country or settings.enablebanking_country
    rows = list_aspsps(code)
    if not rows:
        typer.echo(f"Sin entidades para el país '{code}'.")
        raise typer.Exit()
    for a in rows:
        typer.echo(f"{a.get('name', '')}  ·  {a.get('country', '')}")


@app.command()
def sync(
    from_date: str = typer.Option(None, "--from", help="Fecha inicio YYYY-MM-DD"),
    to_date: str = typer.Option(None, "--to", help="Fecha fin YYYY-MM-DD"),
    bank: str = typer.Option(
        None, "--bank", help="Id del banco (ver `finmcp banks list`). Por defecto, todos."
    ),
) -> None:
    """Sincroniza cuentas, saldos y movimientos a la base de datos local (por banco)."""
    from finmcp.sync.service import run_sync

    try:
        runs = run_sync(from_date, to_date, bank_id=bank)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    any_error = False
    for run in runs:
        if run.status == "ok":
            typer.echo(
                f"[{run.bank_id}] OK · cuentas: {run.accounts_synced} · nuevas tx: {run.tx_added}"
            )
        else:
            any_error = True
            typer.echo(f"[{run.bank_id}] ERROR · {run.detail}")
    if any_error:
        raise typer.Exit(code=1)


@app.command()
def accounts() -> None:
    """Lista las cuentas almacenadas localmente."""
    from finmcp.db.models import Account
    from finmcp.db.session import SessionLocal, init_db

    init_db()
    with SessionLocal() as s:
        rows = s.query(Account).all()
        if not rows:
            typer.echo("No hay cuentas. Ejecuta `finmcp sync` primero.")
            raise typer.Exit()
        for a in rows:
            typer.echo(
                f"- [{a.bank_id}] {a.name} ({a.type}) · {a.currency} · {a.iban or 's/IBAN'}"
            )


def _query(fn, *args, **kwargs):
    """Ejecuta una tool del servidor MCP sobre SQLite; fechas inválidas -> BadParameter."""
    from finmcp.db.session import init_db

    init_db()
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _echo_txs(rows: list[dict]) -> None:
    if not rows:
        typer.echo("Sin movimientos.")
        raise typer.Exit()
    for t in rows:
        sign = "-" if t["type"] == "debit" else "+"
        typer.echo(
            f"{t['date']}  {sign}{t['amount']:>10.2f} {t['currency']}  "
            f"{t['merchant'] or t['description'] or ''}  [{t['category'] or 's/cat'}]  "
            f"(cuenta={t['account_id']})"
        )


@app.command()
def balances() -> None:
    """Saldo más reciente de cada cuenta."""
    from finmcp.mcp.server import get_balances

    rows = _query(get_balances)
    if not rows:
        typer.echo("Sin saldos. Ejecuta `finmcp sync` primero.")
        raise typer.Exit()
    for b in rows:
        typer.echo(
            f"- {b['account_id']} · disponible: {b['available']} · actual: {b['current']} "
            f"{b['currency']} · a {b['as_of']}"
        )


@app.command()
def transactions(
    account_id: str = typer.Option(None, "--account", help="Id de la cuenta"),
    bank: str = typer.Option(None, "--bank", help="Id del banco"),
    start: str = typer.Option(None, "--from", help="Fecha inicio YYYY-MM-DD"),
    end: str = typer.Option(None, "--to", help="Fecha fin YYYY-MM-DD"),
    type: str = typer.Option(None, "--type", help="debit | credit"),
    limit: int = typer.Option(100, help="Máximo de movimientos"),
) -> None:
    """Movimientos filtrados por cuenta, banco, fechas y tipo."""
    from finmcp.mcp.server import get_transactions

    _echo_txs(
        _query(
            get_transactions,
            account_id=account_id,
            bank_id=bank,
            start=start,
            end=end,
            type=type,
            limit=limit,
        )
    )


@app.command()
def search(
    query: str = typer.Argument(..., help="Texto a buscar en comercio o concepto"),
    bank: str = typer.Option(None, "--bank", help="Id del banco"),
    limit: int = typer.Option(50, help="Máximo de movimientos"),
) -> None:
    """Busca movimientos por texto en el comercio o el concepto."""
    from finmcp.mcp.server import search_transactions

    _echo_txs(_query(search_transactions, query, bank_id=bank, limit=limit))


@app.command()
def spend(
    start: str = typer.Option(None, "--from", help="Fecha inicio YYYY-MM-DD"),
    end: str = typer.Option(None, "--to", help="Fecha fin YYYY-MM-DD"),
) -> None:
    """Gasto agregado por categoría en un periodo."""
    from finmcp.mcp.server import spend_by_category_tool

    rows = _query(spend_by_category_tool, start, end)
    if not rows:
        typer.echo("Sin gasto en el periodo.")
        raise typer.Exit()
    for r in rows:
        typer.echo(f"{r['total']:>10.2f}  {r['category']}  ({r['count']} tx)")


@app.command()
def subscriptions(
    months: int = typer.Option(6, "--months", help="Meses hacia atrás a analizar"),
) -> None:
    """Cargos recurrentes detectados (suscripciones / domiciliaciones)."""
    from finmcp.mcp.server import list_subscriptions

    rows = _query(list_subscriptions, lookback_months=months)
    if not rows:
        typer.echo("No se detectan cargos recurrentes.")
        raise typer.Exit()
    for r in rows:
        typer.echo(
            f"- {r['merchant']} · {r['amount']:.2f} {r['currency']} · {r['cadence']} · "
            f"{r['occurrences']} cargos · último {r['last_charge']}"
        )


@app.command()
def unusual(
    start: str = typer.Option(None, "--from", help="Fecha inicio YYYY-MM-DD"),
    end: str = typer.Option(None, "--to", help="Fecha fin YYYY-MM-DD"),
) -> None:
    """Cargos atípicos respecto al histórico de cada comercio."""
    from finmcp.mcp.server import unusual_charges

    rows = _query(unusual_charges, start, end)
    if not rows:
        typer.echo("Sin cargos atípicos.")
        raise typer.Exit()
    for r in rows:
        z = f"z={r['z_score']}" if r["z_score"] is not None else "sin dispersión"
        typer.echo(
            f"{r['date']}  {r['merchant']} · {r['amount']:.2f} {r['currency']} "
            f"(habitual {r['usual_amount']:.2f}) · {z}"
        )


@app.command()
def summary(
    year: int = typer.Argument(..., help="Año (p.ej. 2026)"),
    month: int = typer.Argument(..., min=1, max=12, help="Mes 1-12"),
) -> None:
    """Resumen mensual: ingresos, gastos, neto, top comercios y categorías."""
    from finmcp.mcp.server import monthly_summary_tool

    r = _query(monthly_summary_tool, year, month)
    typer.echo(
        f"{r['period']} · ingresos {r['income']:.2f} · gastos {r['expense']:.2f} · "
        f"neto {r['net']:.2f} · {r['transactions']} tx"
    )
    if r["by_category"]:
        typer.echo("Por categoría:")
        for c in r["by_category"]:
            typer.echo(f"  {c['total']:>10.2f}  {c['category']}  ({c['count']} tx)")
    if r["top_merchants"]:
        typer.echo("Top comercios:")
        for m in r["top_merchants"]:
            typer.echo(f"  {m['total']:>10.2f}  {m['merchant']}")


@app.command()
def status() -> None:
    """Estado de la última sincronización, por banco."""
    from finmcp.mcp.server import sync_status

    for r in _query(sync_status):
        if r["status"] == "never":
            typer.echo(f"Nunca sincronizado. {r['detail']}")
            continue
        typer.echo(
            f"[{r['bank_id']}] {r['status']} · inicio {r['started_at']} · "
            f"fin {r['finished_at'] or '-'} · cuentas {r['accounts_synced']} · "
            f"nuevas tx {r['tx_added']}"
        )


@rules_app.command("add")
def rules_add(
    pattern: str = typer.Argument(..., help="Subcadena a buscar (case-insensitive)"),
    category: str = typer.Argument(..., help="Categoría a asignar"),
    field: str = typer.Option("any", help="merchant | description | any"),
    priority: int = typer.Option(100, help="Menor = se evalúa antes"),
) -> None:
    """Añade una regla y la aplica a los movimientos existentes."""
    from finmcp.analytics.categorization import apply_rules
    from finmcp.db.models import CategoryRule
    from finmcp.db.session import SessionLocal, init_db

    init_db()
    with SessionLocal() as s:
        s.add(
            CategoryRule(
                pattern=pattern, category=category, field=field, priority=priority
            )
        )
        s.commit()
        changed = apply_rules(s)
    typer.echo(
        f"Regla añadida: '{pattern}' -> {category} · recategorizadas {changed} tx"
    )


@rules_app.command("list")
def rules_list() -> None:
    """Lista las reglas de categorización."""
    from finmcp.db.models import CategoryRule
    from finmcp.db.session import SessionLocal, init_db

    init_db()
    with SessionLocal() as s:
        rows = (
            s.query(CategoryRule)
            .order_by(CategoryRule.priority.asc(), CategoryRule.id.asc())
            .all()
        )
        if not rows:
            typer.echo("Sin reglas. Añade una con `finmcp rules add`.")
            raise typer.Exit()
        for r in rows:
            typer.echo(
                f"[{r.priority}] '{r.pattern}' ({r.field}) -> {r.category}  (id={r.id})"
            )


@app.command("import-csv")
def import_csv(
    path: str = typer.Argument(..., help="Ruta al CSV/Excel-exportado de movimientos"),
    iban: str = typer.Option(None, help="IBAN de la cuenta destino"),
    account_id: str = typer.Option(None, help="ID de la cuenta destino"),
    delimiter: str = typer.Option(None, help="Delimitador (autodetecta si se omite)"),
) -> None:
    """Importa movimientos desde un CSV (p.ej. export de CaixaBankNow).

    Deduplica contra lo ya existente, así que es seguro reimportar o solapar
    con lo que ya bajó la API. Útil para histórico anterior a los 90 días PSD2.
    """
    from finmcp.analytics.categorization import apply_rules
    from finmcp.db.session import SessionLocal, init_db
    from finmcp.importers.csv_import import import_file

    init_db()
    with SessionLocal() as s:
        try:
            added, skipped = import_file(s, path, iban, account_id, delimiter)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        changed = apply_rules(s)

    typer.echo(
        f"Importadas {added} · saltadas (duplicadas/sin cuenta) {skipped} · "
        f"recategorizadas {changed}"
    )


@app.command()
def categorize(
    only_new: bool = typer.Option(
        False, "--only-new", help="Solo las que aún no tienen categoría manual"
    ),
) -> None:
    """Reaplica las reglas de categorización a los movimientos."""
    from finmcp.analytics.categorization import apply_rules
    from finmcp.db.session import SessionLocal, init_db

    init_db()
    with SessionLocal() as s:
        changed = apply_rules(s, only_uncategorized=only_new)
    typer.echo(f"Recategorizadas {changed} transacciones.")


@app.command()
def serve(
    http: bool = typer.Option(
        False, "--http", help="Transporte HTTP (para ChatGPT); por defecto stdio"
    ),
    host: str = typer.Option("127.0.0.1", help="Host en modo --http"),
    port: int = typer.Option(8000, help="Puerto en modo --http"),
) -> None:
    """Arranca el servidor MCP (solo lectura).

    stdio para Claude Desktop/Cursor; --http para conectores remotos (ChatGPT).
    En --http, define FINMCP_HTTP_TOKEN para exigir bearer auth.
    """
    from finmcp.mcp.server import main

    main(http=http, host=host, port=port)


if __name__ == "__main__":
    app()
