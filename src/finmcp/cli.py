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
) -> None:
    """Sincroniza cuentas, saldos y movimientos a la base de datos local (por banco)."""
    from finmcp.sync.service import run_sync

    runs = run_sync(from_date, to_date)
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
    from pathlib import Path

    from finmcp.analytics.categorization import apply_rules
    from finmcp.db import models
    from finmcp.db.session import SessionLocal, init_db
    from finmcp.importers.csv_import import (
        import_transactions,
        parse_rows,
        parse_xls,
        parse_xlsx,
    )

    ext = Path(path).suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        rows = parse_xlsx(path)
    elif ext == ".xls":
        rows = parse_xls(path)
    else:
        raw = Path(path).read_bytes()
        text = ""
        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        rows = parse_rows(text, delimiter)
    if not rows:
        typer.echo("No se encontraron movimientos en el fichero.")
        raise typer.Exit()

    # El fichero puede traer varias cuentas (columna "Número de cuenta"); en ese
    # caso cada fila se mapea sola. --iban/--account-id solo se usan como destino
    # por defecto para ficheros de una sola cuenta sin esa columna.
    has_account_col = any(r.get("account") for r in rows)

    init_db()
    with SessionLocal() as s:
        default_acc = None
        if account_id:
            default_acc = s.get(models.Account, account_id)
        elif iban:
            default_acc = (
                s.query(models.Account)
                .filter(models.Account.iban == iban)
                .first()
            )
        elif not has_account_col:
            accs = s.query(models.Account).all()
            default_acc = accs[0] if len(accs) == 1 else None

        if default_acc is None and not has_account_col:
            raise typer.BadParameter(
                "Indica la cuenta destino con --iban o --account-id "
                "(hay varias cuentas). Lístalas con `finmcp accounts`."
            )

        added, skipped = import_transactions(s, rows, account=default_acc)
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
