from __future__ import annotations

from typing import Protocol

from finmcp.providers.types import Account, Balance, Transaction


class BankDataProvider(Protocol):
    """Interfaz de solo lectura para el proveedor Open Banking (Enable Banking).

    Ninguna operación inicia pagos ni transferencias.
    """

    def get_accounts(self) -> list[Account]: ...

    def get_balance(self, account_id: str) -> Balance: ...

    def get_transactions(
        self,
        account_id: str,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[Transaction]: ...
