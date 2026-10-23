from __future__ import annotations

import typer

app = typer.Typer(
    help="Finanzas personales sobre Open Banking / Enable Banking (solo lectura)."
)
rules_app = typer.Typer(help="Reglas de categorización personalizadas.")
app.add_typer(rules_app, name="rules")
tags_app = typer.Typer(help="Catálogo de etiquetas y etiquetado de movimientos.")
app.add_typer(tags_app, name="tags")
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
    """Autoriza con Enable Banking y guarda las credenciales cifradas (Bonusly no lo necesita)."""
    b = _resolve_bank(bank)
    if b.provider == "bonusly":
        typer.echo("Bonusly no usa `auth`: basta con BONUSLY_API_TOKEN en .env.", err=True)
        raise typer.Exit(code=2)
    from finmcp.providers.enablebanking.auth import run_link_flow

    run_link_flow(b)


@banks_app.command("list")
def banks_list() -> None:
    """Lista los bancos configurados y si ya pueden sincronizarse (vínculo de auth o token)."""
    from finmcp.config import settings
    from finmcp.providers.factory import is_linked

    banks = settings.banks
    if not banks:
        typer.echo("No hay ningún banco configurado. Ver .env.example.")
        raise typer.Exit()
    for b in banks:
        vinculado = "sí" if is_linked(b) else "no"
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
    strategy: str = typer.Option(
        None,
        "--strategy",
        help=(
            "Estrategia de Enable Banking: 'longest' busca todo lo disponible "
            "desde --from (útil para histórico; evita el 422 de periodo fuera "
            "de rango). Por defecto, la estándar."
        ),
    ),
) -> None:
    """Sincroniza cuentas, saldos y movimientos a la base de datos local (por banco)."""
    from finmcp.sync.lock import EXIT_BUSY, SyncInProgress
    from finmcp.sync.service import run_sync

    try:
        runs = run_sync(from_date, to_date, bank_id=bank, strategy=strategy)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    except SyncInProgress as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=EXIT_BUSY) from exc
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
        from finmcp.db.models import is_manual_account

        for a in rows:
            manual_txt = " · manual" if is_manual_account(a) else ""
            typer.echo(
                f"- [{a.bank_id}] {a.name} ({a.type}) · {a.currency} · "
                f"{a.iban or 's/IBAN'} · id={a.id}{manual_txt}"
            )


@app.command("add-account")
def add_account(
    bank: str = typer.Option(..., "--bank", help="Banco de la cuenta (etiqueta, p. ej. ING)"),
    name: str = typer.Option(..., "--name", help="Nombre de la cuenta"),
    iban: str = typer.Option(None, "--iban", help="IBAN (admite espacios)"),
    currency: str = typer.Option("EUR", "--currency", help="Divisa"),
    account_type: str = typer.Option(
        "CACC", "--type", help="Tipo ISO 20022: CACC (corriente), SVGS (ahorro)…"
    ),
) -> None:
    """Da de alta una cuenta manual (sin sincronización con el banco).

    Sus movimientos se suben con `import-csv --iban …` y su saldo con `set-balance`.
    """
    from finmcp.mcp.server import add_manual_account

    a = _query(add_manual_account, bank, name, iban, currency, account_type)
    typer.echo(
        f"Cuenta manual añadida: [{a['bank_id']}] {a['name']} ({a['type']}) · "
        f"{a['currency']} · {a['iban'] or 's/IBAN'} · id={a['id']}"
    )


@app.command("set-balance")
def set_balance(
    account_id: str = typer.Argument(..., help="Id de la cuenta manual"),
    amount: float = typer.Argument(..., help="Saldo (p. ej. 1234.56)"),
    at: str = typer.Option(
        None, "--at", help="Fecha (YYYY-MM-DD, saldo al cierre de ese día) o ISO; por defecto, ahora"
    ),
) -> None:
    """Fija el saldo de una cuenta manual (las sincronizadas lo reciben del banco)."""
    from finmcp.mcp.server import set_manual_balance

    r = _query(set_manual_balance, account_id, amount, at)
    tail = f" ··{r['iban'][-4:]}" if r["iban"] else ""
    typer.echo(
        f"Saldo de [{r['bank_id']}] {r['name']}{tail}: {r['balance']:.2f} {r['currency']} "
        f"· a {r['as_of']}"
    )


@app.command("merge-accounts")
def merge_accounts(
    old_id: str = typer.Argument(..., help="Id obsoleto de la cuenta (se retira)"),
    new_id: str = typer.Argument(..., help="Id vigente de la cuenta (se conserva)"),
    apply: bool = typer.Option(
        False, "--apply", help="Aplica el cambio. Sin esta opción solo simula."
    ),
) -> None:
    """Reasigna el histórico de una cuenta duplicada a su id vigente.

    Mueve movimientos, saldos y reglas restringidas del id antiguo al nuevo y
    retira el id antiguo. Exige que sea la misma cuenta (mismo banco e IBAN) y
    que ningún movimiento tenga el mismo id en ambas. Con --apply hace antes
    una copia de seguridad de la BD.
    """
    import shutil
    from datetime import datetime

    from sqlalchemy import func

    from finmcp.config import settings
    from finmcp.db.models import Account, Balance, CategoryRule, Transaction
    from finmcp.db.session import SessionLocal, init_db

    init_db()
    with SessionLocal() as s:
        old = s.get(Account, old_id)
        new = s.get(Account, new_id)
        if old is None or new is None:
            raise typer.BadParameter("Ambos ids deben existir en `finmcp accounts`.")
        if old.id == new.id:
            raise typer.BadParameter("Los dos ids son el mismo.")
        if old.bank_id != new.bank_id or (old.iban or "") != (new.iban or ""):
            raise typer.BadParameter(
                "No son la misma cuenta: banco o IBAN distintos. Operación cancelada."
            )

        n_tx = s.query(func.count(Transaction.id)).filter(
            Transaction.account_id == old.id
        ).scalar()
        n_bal = s.query(func.count(Balance.id)).filter(
            Balance.account_id == old.id
        ).scalar()
        n_rules = s.query(func.count(CategoryRule.id)).filter(
            CategoryRule.account_id == old.id
        ).scalar()
        old_tx_ids = {
            r[0]
            for r in s.query(Transaction.id).filter(Transaction.account_id == old.id)
        }
        clash = (
            s.query(Transaction.id)
            .filter(Transaction.account_id == new.id, Transaction.id.in_(old_tx_ids))
            .all()
            if old_tx_ids
            else []
        )
        if clash:
            raise typer.BadParameter(
                f"{len(clash)} movimientos tienen el mismo id en ambas cuentas. "
                "Operación cancelada."
            )

        typer.echo(
            f"Cuenta {old.id} ({old.name}, {old.bank_id}) -> {new.id} ({new.name})"
        )
        typer.echo(
            f"  movimientos: {n_tx} · saldos: {n_bal} · reglas restringidas: {n_rules}"
        )
        if not apply:
            typer.echo("Simulación: no se ha modificado nada. Añade --apply para aplicar.")
            raise typer.Exit()

        backup = settings.db_path.with_name(
            f"{settings.db_path.stem}.pre-merge-{datetime.now():%Y%m%d-%H%M%S}.db"
        )
        shutil.copy2(settings.db_path, backup)
        typer.echo(f"Copia de seguridad: {backup}")

        s.query(Transaction).filter(Transaction.account_id == old.id).update(
            {Transaction.account_id: new.id}, synchronize_session=False
        )
        s.query(Balance).filter(Balance.account_id == old.id).update(
            {Balance.account_id: new.id}, synchronize_session=False
        )
        s.query(CategoryRule).filter(CategoryRule.account_id == old.id).update(
            {CategoryRule.account_id: new.id}, synchronize_session=False
        )
        s.flush()
        s.delete(old)
        s.commit()
        typer.echo(
            f"Hecho: {n_tx} movimientos, {n_bal} saldos y {n_rules} reglas "
            f"reasignados a {new.id}; id {old.id} retirado."
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
        tags = "".join(f" #{n}" for n in t["tags"])
        neutral_txt = " (neutral)" if t.get("neutral") else ""
        manual_txt = " (manual)" if t.get("skip_category_rules") else ""
        bill_txt = " (recibo)" if t.get("is_bill") else ""
        typer.echo(
            f"{t['date']}  {sign}{t['amount']:>10.2f} {t['currency']}  "
            f"{t['merchant'] or t['description'] or ''}  "
            f"[{t['category'] or 's/cat'}]{tags}{neutral_txt}{manual_txt}{bill_txt}  "
            f"(id={t['id']}, cuenta={t['account_id']})"
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
    tag: str = typer.Option(None, "--tag", help="Etiqueta"),
    limit: int = typer.Option(100, help="Máximo de movimientos"),
) -> None:
    """Movimientos filtrados por cuenta, banco, fechas, tipo y etiqueta."""
    from finmcp.mcp.server import get_transactions

    _echo_txs(
        _query(
            get_transactions,
            account_id=account_id,
            bank_id=bank,
            start=start,
            end=end,
            type=type,
            tag=tag,
            limit=limit,
        )
    )


@app.command()
def search(
    query: str = typer.Argument(..., help="Texto a buscar en comercio o concepto"),
    bank: str = typer.Option(None, "--bank", help="Id del banco"),
    tag: str = typer.Option(None, "--tag", help="Etiqueta"),
    limit: int = typer.Option(50, help="Máximo de movimientos"),
) -> None:
    """Busca movimientos por texto en el comercio o el concepto."""
    from finmcp.mcp.server import search_transactions

    _echo_txs(_query(search_transactions, query, bank_id=bank, tag=tag, limit=limit))


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
def bills(
    months: int = typer.Option(6, "--months", help="Meses hacia atrás a analizar"),
) -> None:
    """Recibos / pagos recurrentes marcados vía reglas (campo `is_bill`)."""
    from finmcp.mcp.server import list_bills

    rows = _query(list_bills, lookback_months=months)
    if not rows:
        typer.echo("No hay recibos marcados en el periodo.")
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
    neutral = r["neutral"]
    if neutral["transactions"]:
        typer.echo(
            f"No computables (entradas {neutral['in']:.2f} · salidas {neutral['out']:.2f}):"
        )
        for t in neutral["transactions"]:
            sign = "-" if t["type"] == "debit" else "+"
            typer.echo(
                f"  {t['date']}  {sign}{t['amount']:>10.2f} {t['currency']}  "
                f"{t['merchant'] or ''}  [{t['category']}]"
            )


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


@app.command()
def neutral(
    tx_ids: list[str] = typer.Argument(..., help="Ids de los movimientos"),
    off: bool = typer.Option(False, "--off", help="Desmarcar en vez de marcar"),
) -> None:
    """Marca movimientos como neutrales (no computan como ingreso/gasto)."""
    from finmcp.mcp.server import set_transactions_neutral

    r = _query(set_transactions_neutral, tx_ids, not off)
    typer.echo(f"Actualizados {r['changed']} movimientos.")


@app.command()
def recategorize(
    tx_id: str = typer.Argument(..., help="Id del movimiento"),
    category: str = typer.Argument(..., help="Categoría a asignar (texto libre)"),
    is_bill: bool | None = typer.Option(
        None,
        "--is-bill/--no-is-bill",
        help="Marca o desmarca el movimiento como recibo (si se omite, no se toca)",
    ),
) -> None:
    """Recategoriza a mano un movimiento y desactiva las reglas para él."""
    from finmcp.mcp.server import recategorize_transaction

    r = _query(recategorize_transaction, tx_id, category, is_bill)
    bill_txt = " (recibo)" if r.get("is_bill") else ""
    typer.echo(
        f"Movimiento {r['id']}: «{r['category']}»{bill_txt} · reglas desactivadas "
        "(skip_category_rules)"
    )


@app.command("skip-rules")
def skip_rules(
    tx_ids: list[str] = typer.Argument(..., help="Ids de los movimientos"),
    off: bool = typer.Option(
        False, "--off", help="Reactivar las reglas en vez de desactivarlas"
    ),
) -> None:
    """Activa `skip_category_rules`: las reglas no tocarán estos movimientos (--off reactiva).

    Con el flag activo, reaplicar reglas (sync, categorize…) no los toca; con
    --off vuelven a quedar sujetos a las reglas.
    """
    from finmcp.mcp.server import set_transactions_skip_rules

    r = _query(set_transactions_skip_rules, tx_ids, not off)
    typer.echo(f"Actualizados {r['changed']} movimientos.")


def _amount_range(amount_min: float | None, amount_max: float | None) -> str:
    """Texto legible de la condición de importe de una regla ('' si no la tiene)."""
    if amount_min is not None and amount_max is not None:
        return f"importe {amount_min:g}-{amount_max:g}"
    if amount_min is not None:
        return f"importe >={amount_min:g}"
    if amount_max is not None:
        return f"importe <={amount_max:g}"
    return ""


def _resolve_account(account: str | None, bank: str | None) -> str | None:
    """Resuelve --account/--bank (mutuamente excluyentes) al id de cuenta a usar.

    --account toma el id tal cual (ver `finmcp accounts`); --bank busca la cuenta
    de ese banco y solo vale si el banco tiene exactamente una cuenta local.
    """
    if account and bank:
        raise typer.BadParameter("No combines --account con --bank; usa solo uno.")
    if account:
        return account
    if not bank:
        return None
    from finmcp.db.models import Account
    from finmcp.db.session import SessionLocal, init_db

    init_db()
    with SessionLocal() as s:
        matches = s.query(Account).filter(Account.bank_id == bank).all()
    if not matches:
        raise typer.BadParameter(
            f"No hay cuentas del banco '{bank}' (ver `finmcp accounts`)."
        )
    if len(matches) > 1:
        ids = ", ".join(a.id for a in matches)
        raise typer.BadParameter(
            f"El banco '{bank}' tiene varias cuentas ({ids}); usa --account con el id exacto."
        )
    return matches[0].id


@rules_app.command("add")
def rules_add(
    pattern: str = typer.Argument(..., help="Subcadena a buscar (case-insensitive)"),
    category: str = typer.Argument(..., help="Categoría a asignar"),
    field: str = typer.Option("any", help="merchant | description | any"),
    priority: int = typer.Option(100, help="Menor = se evalúa antes"),
    tag: list[str] = typer.Option(None, "--tag", help="Etiqueta a añadir (repetible)"),
    amount_min: float | None = typer.Option(
        None, "--amount-min", help="Solo movimientos con importe >= el valor"
    ),
    amount_max: float | None = typer.Option(
        None, "--amount-max", help="Solo movimientos con importe <= el valor"
    ),
    neutral: bool = typer.Option(
        False, "--neutral", help="Los movimientos que casen no computan como ingreso/gasto"
    ),
    is_bill: bool = typer.Option(
        False, "--is-bill", help="Los movimientos que casen quedan marcados como recibo"
    ),
    account: str | None = typer.Option(
        None, "--account", help="Restringe la regla a esta cuenta (id; ver `finmcp accounts`)"
    ),
    bank: str | None = typer.Option(
        None, "--bank", help="Restringe la regla al banco indicado (alternativa a --account)"
    ),
) -> None:
    """Añade una regla (opcionalmente con rango de importe y/o cuenta) y la aplica a lo existente."""
    from finmcp.mcp.server import add_category_rule

    account_id = _resolve_account(account, bank)
    r = _query(
        add_category_rule,
        pattern,
        category,
        field,
        priority,
        tag or [],
        amount_min,
        amount_max,
        neutral,
        is_bill,
        account_id,
    )
    tags = "".join(f" #{t}" for t in tag or [])
    cond = _amount_range(amount_min, amount_max)
    cond_txt = f" [{cond}]" if cond else ""
    neutral_txt = " (neutral)" if neutral else ""
    bill_txt = " (recibo)" if is_bill else ""
    account_txt = f" [cuenta={r.get('account_label')}]" if r.get("account_label") else ""
    typer.echo(
        f"Regla añadida (id={r['id']}): '{pattern}' -> {category}{tags}{cond_txt}{neutral_txt}"
        f"{bill_txt}{account_txt} · recategorizadas {r['recategorized']} tx"
    )


@rules_app.command("update")
def rules_update(
    rule_id: int = typer.Argument(..., help="Id de la regla (ver `finmcp rules list`)"),
    pattern: str | None = typer.Option(None, help="Nuevo patrón (subcadena, case-insensitive)"),
    category: str | None = typer.Option(None, help="Nueva categoría"),
    field: str | None = typer.Option(None, help="merchant | description | any"),
    priority: int | None = typer.Option(None, help="Nueva prioridad (menor = se evalúa antes)"),
    amount_min: float | None = typer.Option(
        None, "--amount-min", help="Nuevo importe mínimo (>=)"
    ),
    amount_max: float | None = typer.Option(
        None, "--amount-max", help="Nuevo importe máximo (<=)"
    ),
    clear_amount_min: bool = typer.Option(
        False, "--clear-amount-min", help="Quita la condición de importe mínimo"
    ),
    clear_amount_max: bool = typer.Option(
        False, "--clear-amount-max", help="Quita la condición de importe máximo"
    ),
    neutral: bool | None = typer.Option(
        None,
        "--neutral/--no-neutral",
        help="Marca o desmarca la regla como neutral (si se omite, no se toca)",
    ),
    is_bill: bool | None = typer.Option(
        None,
        "--is-bill/--no-is-bill",
        help="Marca o desmarca la regla como recibo (si se omite, no se toca)",
    ),
    account: str | None = typer.Option(
        None, "--account", help="Restringe la regla a esta cuenta (id; ver `finmcp accounts`)"
    ),
    bank: str | None = typer.Option(
        None, "--bank", help="Restringe la regla al banco indicado (alternativa a --account)"
    ),
    clear_account: bool = typer.Option(
        False, "--clear-account", help="Quita la restricción de cuenta (vuelve a aplicar a todas)"
    ),
) -> None:
    """Edita una regla existente y reaplica las reglas.

    Solo cambia los campos indicados; los omitidos se conservan. Con
    `--clear-amount-min`/`--clear-amount-max` se retira la condición de importe;
    con `--clear-account` se retira la restricción de cuenta.
    """
    from finmcp.mcp.server import update_category_rule

    if (account or bank) and clear_account:
        raise typer.BadParameter("No combines --account/--bank con --clear-account.")
    account_id = _resolve_account(account, bank)
    r = _query(
        update_category_rule,
        rule_id,
        pattern,
        category,
        field,
        priority,
        amount_min,
        amount_max,
        clear_amount_min,
        clear_amount_max,
        neutral,
        is_bill,
        account_id,
        clear_account,
    )
    cond = _amount_range(r.get("amount_min"), r.get("amount_max"))
    cond_txt = f" [{cond}]" if cond else ""
    neutral_txt = " (neutral)" if r.get("neutral") else ""
    bill_txt = " (recibo)" if r.get("is_bill") else ""
    account_txt = f" [cuenta={r.get('account_label')}]" if r.get("account_label") else ""
    typer.echo(
        f"Regla {r['id']} actualizada: '{r['pattern']}' ({r['field']}) -> "
        f"{r['category']}{cond_txt}{neutral_txt}{bill_txt}{account_txt} · "
        f"recategorizadas {r['recategorized']} tx"
    )


@rules_app.command("remove")
def rules_remove(
    rule_ids: list[int] = typer.Argument(..., help="Ids de las reglas a eliminar"),
) -> None:
    """Elimina reglas y reaplica las reglas.

    Los movimientos que solo casaban con la regla eliminada conservan su
    categoría; los que casen con otra regla se recategorizan.
    """
    from finmcp.mcp.server import remove_category_rules

    r = _query(remove_category_rules, rule_ids)
    for d in r["removed"]:
        typer.echo(
            f"Regla {d['id']} eliminada: '{d['pattern']}' ({d['field']}) -> {d['category']}"
        )
    typer.echo(f"recategorizadas {r['recategorized']} tx")


@rules_app.command("neutral")
def rules_neutral(
    rule_id: int = typer.Argument(..., help="Id de la regla"),
    off: bool = typer.Option(False, "--off", help="Desmarcar en vez de marcar"),
) -> None:
    """Marca una regla como neutral (no computa como ingreso/gasto) y reaplica las reglas."""
    from finmcp.mcp.server import set_rule_neutral

    r = _query(set_rule_neutral, rule_id, not off)
    estado = "neutral" if r["neutral"] else "computable"
    typer.echo(f"Regla {rule_id}: {estado} · actualizadas {r['recategorized']} tx")


@rules_app.command("bill")
def rules_bill(
    rule_id: int = typer.Argument(..., help="Id de la regla"),
    off: bool = typer.Option(False, "--off", help="Desmarcar en vez de marcar"),
) -> None:
    """Marca una regla como recibo / pago recurrente y reaplica las reglas."""
    from finmcp.mcp.server import set_rule_is_bill

    r = _query(set_rule_is_bill, rule_id, not off)
    estado = "recibo" if r["is_bill"] else "no recibo"
    typer.echo(f"Regla {rule_id}: {estado} · actualizadas {r['recategorized']} tx")


@rules_app.command("tag")
def rules_tag(
    rule_id: int = typer.Argument(..., help="Id de la regla"),
    tags: list[str] = typer.Argument(..., help="Etiquetas a añadir"),
) -> None:
    """Añade etiquetas a una regla y reaplica las reglas."""
    from finmcp.mcp.server import add_rule_tags

    r = _query(add_rule_tags, rule_id, tags)
    typer.echo(f"Regla {rule_id}: {' '.join('#' + t for t in r['tags']) or 'sin etiquetas'}")


@rules_app.command("untag")
def rules_untag(
    rule_id: int = typer.Argument(..., help="Id de la regla"),
    tags: list[str] = typer.Argument(..., help="Etiquetas a quitar"),
) -> None:
    """Quita etiquetas de una regla y reaplica las reglas."""
    from finmcp.mcp.server import remove_rule_tags

    r = _query(remove_rule_tags, rule_id, tags)
    typer.echo(f"Regla {rule_id}: {' '.join('#' + t for t in r['tags']) or 'sin etiquetas'}")


@rules_app.command("list")
def rules_list() -> None:
    """Lista las reglas de categorización."""
    from finmcp.mcp.server import list_category_rules

    rows = _query(list_category_rules)
    if not rows:
        typer.echo("Sin reglas. Añade una con `finmcp rules add`.")
        raise typer.Exit()
    for r in rows:
        tags = "".join(f" #{t}" for t in r["tags"])
        cond = _amount_range(r.get("amount_min"), r.get("amount_max"))
        cond_txt = f" [{cond}]" if cond else ""
        neutral_txt = " (neutral)" if r.get("neutral") else ""
        bill_txt = " (recibo)" if r.get("is_bill") else ""
        account_txt = f" [cuenta={r.get('account_label')}]" if r.get("account_label") else ""
        typer.echo(
            f"[{r['priority']}] '{r['pattern']}' ({r['field']}) -> {r['category']}{tags}{cond_txt}"
            f"{neutral_txt}{bill_txt}{account_txt}  (id={r['id']})"
        )


@tags_app.command("list")
def tags_list() -> None:
    """Lista el catálogo de etiquetas y su uso."""
    from finmcp.mcp.server import list_tags

    rows = _query(list_tags)
    if not rows:
        typer.echo("Sin etiquetas. Crea una con `finmcp tags add`.")
        raise typer.Exit()
    for t in rows:
        typer.echo(f"#{t['name']}  ({t['transactions']} tx, {t['rules']} reglas)")


@tags_app.command("add")
def tags_add(name: str = typer.Argument(..., help="Nombre de la etiqueta")) -> None:
    """Crea una etiqueta en el catálogo."""
    from finmcp.mcp.server import create_tag

    typer.echo(f"Etiqueta #{_query(create_tag, name)['name']} lista.")


@tags_app.command("delete")
def tags_delete(name: str = typer.Argument(..., help="Nombre de la etiqueta")) -> None:
    """Borra una etiqueta del catálogo (falla si está en uso)."""
    from finmcp.mcp.server import delete_tag

    _query(delete_tag, name)
    typer.echo(f"Etiqueta '{name}' borrada.")


@tags_app.command("assign")
def tags_assign(
    tag: str = typer.Argument(..., help="Etiqueta"),
    tx_ids: list[str] = typer.Argument(..., help="Ids de los movimientos"),
) -> None:
    """Etiqueta movimientos a mano."""
    from finmcp.mcp.server import tag_transactions

    r = _query(tag_transactions, tx_ids, [tag])
    typer.echo(f"Etiquetados {r['changed']} movimientos.")


@tags_app.command("remove")
def tags_remove(
    tag: str = typer.Argument(..., help="Etiqueta"),
    tx_ids: list[str] = typer.Argument(..., help="Ids de los movimientos"),
) -> None:
    """Quita una etiqueta a movimientos (las reglas no la volverán a poner)."""
    from finmcp.mcp.server import untag_transactions

    r = _query(untag_transactions, tx_ids, [tag])
    typer.echo(f"Desetiquetados {r['changed']} movimientos.")


@tags_app.command("spend")
def tags_spend(
    start: str = typer.Option(None, "--from", help="Fecha inicio YYYY-MM-DD"),
    end: str = typer.Option(None, "--to", help="Fecha fin YYYY-MM-DD"),
) -> None:
    """Gasto por etiqueta en un periodo (un movimiento suma en todas sus etiquetas)."""
    from finmcp.mcp.server import spend_by_tag_tool

    rows = _query(spend_by_tag_tool, start, end)
    if not rows:
        typer.echo("Sin gasto en el periodo.")
        raise typer.Exit()
    for r in rows:
        typer.echo(f"{r['total']:>10.2f}  {r['tag']}  ({r['count']} tx)")


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


@app.command()
def web(
    host: list[str] = typer.Option(
        ["127.0.0.1"],
        "--host",
        help="IP en la que escuchar (repetible): loopback, IP de la red local o de Tailscale. "
        "Nunca 0.0.0.0.",
    ),
    port: int = typer.Option(8790, help="Puerto del panel (el MCP --http usa el 8000)"),
) -> None:
    """Arranca el panel web de análisis (solo lectura), pensado para el móvil.

    Servicio independiente del servidor MCP. Solo atiende clientes de la red local
    (y de Tailscale si se le indica su IP con --host). Abre la BD en solo lectura.
    """
    from finmcp.config import settings
    from finmcp.web import app as webapp
    from finmcp.web import auth

    try:
        hosts, _ = webapp.validate_hosts(host)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2)
    pin_hash = settings.finmcp_web_pin_hash
    if pin_hash and not auth.valid_hash(pin_hash):
        typer.echo(f"Error: {auth.PIN_ENV} no es válido; vuelva a fijar el PIN con `finmcp web-pin`.",
                   err=True)
        raise typer.Exit(2)
    if not pin_hash:
        typer.echo("Aviso: el panel no tiene PIN; cualquiera en la red local podrá verlo "
                   "(fíjelo con `finmcp web-pin`).", err=True)
    webapp.run(hosts, port)


@app.command("web-pin")
def web_pin(
    remove: bool = typer.Option(False, "--remove", help="Quita el PIN (el panel queda abierto en la red local)"),
) -> None:
    """Fija o quita el PIN del panel web. Guarda solo su huella en .env, nunca el PIN.

    Reinicie el panel después para aplicarlo (p. ej. `systemctl --user restart finmcp-web`).
    """
    from pathlib import Path

    from finmcp.config import settings
    from finmcp.web import auth

    env = settings._env_file_path or Path(".env")
    if remove:
        auth.write_env_value(env, auth.PIN_ENV, None)
        typer.echo("PIN eliminado. Reinicie el panel para aplicarlo.")
        return
    pin = typer.prompt("PIN (de 4 a 12 cifras)", hide_input=True, confirmation_prompt="Repita el PIN")
    if not auth.valid_pin(pin):
        typer.echo("Error: el PIN debe tener entre 4 y 12 cifras.", err=True)
        raise typer.Exit(2)
    auth.write_env_value(env, auth.PIN_ENV, auth.hash_pin(pin))
    typer.echo(f"PIN guardado (solo su huella) en {env}. Reinicie el panel para aplicarlo.")


if __name__ == "__main__":
    app()
