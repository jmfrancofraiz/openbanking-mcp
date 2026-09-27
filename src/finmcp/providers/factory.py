from __future__ import annotations

from finmcp.config import BankConfig
from finmcp.providers.base import BankDataProvider


def get_provider(bank: BankConfig) -> BankDataProvider:
    """Devuelve el cliente de Enable Banking (único proveedor soportado) para un banco."""
    from finmcp.providers.enablebanking.client import EnableBankingClient

    return EnableBankingClient(bank)
