from __future__ import annotations

from datetime import datetime, timezone

from finmcp.analytics.categorization import apply_rules
from finmcp.config import BankConfig, settings
from finmcp.db import repo
from finmcp.db.models import SyncRun
from finmcp.db.session import SessionLocal, init_db
from finmcp.providers.factory import get_provider


def _sync_one_bank(
    session, bank: BankConfig, from_date: str | None, to_date: str | None
) -> SyncRun:
    """Sincroniza un banco; nunca relanza, deja el resultado (ok/error) en el SyncRun."""
    # Persistimos el inicio antes de tocar el banco: así, si el pull falla,
    # la fila sobrevive al rollback y podemos marcarla como "error".
    run = SyncRun(bank_id=bank.id, started_at=datetime.now(timezone.utc), status="running")
    session.add(run)
    session.commit()

    try:
        client = get_provider(bank)
        tx_added = 0
        accounts = client.get_accounts()
        for acc in accounts:
            repo.upsert_account(session, acc)
            try:
                bal = client.get_balance(acc.provider_account_id)
                repo.add_balance(session, acc.provider_account_id, bal)
            except Exception:  # noqa: BLE001 -- el saldo no debe romper el sync
                pass
            for tx in client.get_transactions(
                acc.provider_account_id, from_date, to_date
            ):
                if repo.upsert_transaction(session, acc.provider_account_id, tx):
                    tx_added += 1

        run.accounts_synced = len(accounts)
        run.tx_added = tx_added
        run.finished_at = datetime.now(timezone.utc)
        run.status = "ok"
        session.commit()
        session.refresh(run)
        return run
    except Exception as exc:  # noqa: BLE001 -- registramos el fallo de este banco y seguimos
        session.rollback()
        run = session.get(SyncRun, run.id)
        run.status = "error"
        run.finished_at = datetime.now(timezone.utc)
        run.detail = str(exc)[:500]
        session.commit()
        return run


def run_sync(
    from_date: str | None = None, to_date: str | None = None
) -> list[SyncRun]:
    """Pull idempotente de cuentas, saldos y movimientos a SQLite, banco a banco.

    Un fallo en un banco no aborta los demás: cada uno reporta su propio SyncRun.
    """
    init_db()
    banks = settings.banks
    if not banks:
        raise RuntimeError(
            "No hay ningún banco configurado. Define ENABLEBANKING_ASPSP_NAME "
            "o FINMCP_BANK_1_ASPSP_NAME en .env."
        )
    with SessionLocal() as session:
        runs = [_sync_one_bank(session, bank, from_date, to_date) for bank in banks]
        # Recategoriza según las reglas del usuario tras incorporar lo nuevo de todos los bancos.
        apply_rules(session)
        return runs
