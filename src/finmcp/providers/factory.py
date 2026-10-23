from __future__ import annotations

from finmcp.config import BankConfig
from finmcp.providers.base import BankDataProvider


def get_provider(bank: BankConfig) -> BankDataProvider:
    """Devuelve el cliente de solo lectura del proveedor del banco."""
    if bank.provider == "bonusly":
        from finmcp.providers.bonusly.client import BonuslyClient

        return BonuslyClient(bank)
    if bank.provider != "enablebanking":
        raise ValueError(f"Proveedor desconocido '{bank.provider}' para el banco '{bank.id}'.")
    from finmcp.providers.enablebanking.client import EnableBankingClient

    return EnableBankingClient(bank)


def is_linked(bank: BankConfig) -> bool:
    """¿Puede sincronizarse ya? Enable Banking: vínculo de `auth`. Bonusly: token en .env."""
    if bank.provider == "bonusly":
        from finmcp import config

        return bool(config.settings.bonusly_api_token)
    from finmcp.providers.enablebanking.auth import has_link

    return has_link(bank.id)
