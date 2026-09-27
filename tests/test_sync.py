from __future__ import annotations

from types import SimpleNamespace

from finmcp.config import BankConfig
from finmcp.sync import service

_ONE_BANK = [BankConfig(id="default", aspsp_name="CaixaBank", country="ES")]
_TWO_BANKS = [
    BankConfig(id="bank1", aspsp_name="CaixaBank", country="ES"),
    BankConfig(id="bank2", aspsp_name="BBVA", country="ES"),
]


def _fake_settings(banks):
    return SimpleNamespace(banks=banks)


def test_run_sync_marks_error_on_failure(monkeypatch, Session):
    """Si el pull a un banco falla, su SyncRun queda 'error' (no colgado en 'running')."""
    monkeypatch.setattr(service, "init_db", lambda: None)
    monkeypatch.setattr(service, "SessionLocal", Session)
    monkeypatch.setattr(service, "settings", _fake_settings(_ONE_BANK))

    class BoomClient:
        def get_accounts(self):
            raise RuntimeError("HTTP 401 token caducado")

    monkeypatch.setattr(service, "get_provider", lambda bank: BoomClient())

    runs = service.run_sync()

    assert len(runs) == 1
    assert runs[0].status == "error"
    assert runs[0].finished_at is not None
    assert "401" in runs[0].detail


def test_run_sync_ok_path(monkeypatch, Session):
    from finmcp.providers.types import Account, Transaction
    from datetime import datetime, timezone

    monkeypatch.setattr(service, "init_db", lambda: None)
    monkeypatch.setattr(service, "SessionLocal", Session)
    monkeypatch.setattr(service, "settings", _fake_settings(_ONE_BANK))

    class FakeClient:
        def get_accounts(self):
            return [Account(provider_account_id="a1", name="C", type="T", currency="EUR")]

        def get_balance(self, account_id):
            raise RuntimeError("sin saldo")  # no debe romper el sync

        def get_transactions(self, account_id, from_date=None, to_date=None):
            return [
                Transaction(
                    provider_id="t1",
                    amount=10.0,
                    currency="EUR",
                    booked_at=datetime.now(timezone.utc),
                    description="x",
                    type="debit",
                )
            ]

    monkeypatch.setattr(service, "get_provider", lambda bank: FakeClient())

    runs = service.run_sync()
    assert len(runs) == 1
    assert runs[0].status == "ok"
    assert runs[0].accounts_synced == 1
    assert runs[0].tx_added == 1


def test_run_sync_partial_failure_does_not_abort_other_banks(monkeypatch, Session):
    """Un banco roto no debe impedir que el otro se sincronice."""
    from finmcp.providers.types import Account

    monkeypatch.setattr(service, "init_db", lambda: None)
    monkeypatch.setattr(service, "SessionLocal", Session)
    monkeypatch.setattr(service, "settings", _fake_settings(_TWO_BANKS))

    class BoomClient:
        def get_accounts(self):
            raise RuntimeError("token caducado")

    class FakeClient:
        def get_accounts(self):
            return [Account(provider_account_id="a2", name="C2", type="T", currency="EUR")]

        def get_balance(self, account_id):
            raise RuntimeError("sin saldo")

        def get_transactions(self, account_id, from_date=None, to_date=None):
            return []

    def fake_get_provider(bank):
        return BoomClient() if bank.id == "bank1" else FakeClient()

    monkeypatch.setattr(service, "get_provider", fake_get_provider)

    runs = {r.bank_id: r for r in service.run_sync()}
    assert runs["bank1"].status == "error"
    assert runs["bank2"].status == "ok"
    assert runs["bank2"].accounts_synced == 1
