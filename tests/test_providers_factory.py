from __future__ import annotations

from types import SimpleNamespace

import pytest

import finmcp.config as config
from finmcp.config import BankConfig
from finmcp.providers import factory

BONUSLY = BankConfig(id="Bonusly", aspsp_name="Bonusly", provider="bonusly")


def test_bonusly_devuelve_su_cliente(monkeypatch):
    from finmcp.providers.bonusly.client import BonuslyClient

    monkeypatch.setattr(
        config, "settings",
        SimpleNamespace(bonusly_api_token="", bonusly_eur_per_point=1.08,
                        bonusly_oauth_refresh_token="r1", bonusly_oauth_client_id="cid",
                        bonusly_oauth_token_url="https://bonus.ly/oauth/token"),
    )
    assert isinstance(factory.get_provider(BONUSLY), BonuslyClient)


def test_enablebanking_por_defecto(monkeypatch):
    from finmcp.providers.enablebanking import client as eb

    monkeypatch.setattr(eb.auth, "load_link", lambda bank_id: {"accounts": []})
    assert isinstance(factory.get_provider(BankConfig(id="bbva")), eb.EnableBankingClient)


def test_proveedor_desconocido():
    with pytest.raises(ValueError, match="Proveedor desconocido"):
        factory.get_provider(BankConfig(id="x", provider="otro"))


def test_bonusly_vinculado_solo_con_oauth(monkeypatch):
    monkeypatch.setattr(config, "settings",
                        SimpleNamespace(bonusly_oauth_refresh_token="r1",
                                        bonusly_oauth_client_id="cid", bonusly_api_token=""))
    assert factory.is_linked(BONUSLY) is True
    # Un PAT suelto ya no vincula: el MCP dejó de aceptarlo (nov-2026).
    monkeypatch.setattr(config, "settings",
                        SimpleNamespace(bonusly_oauth_refresh_token="",
                                        bonusly_oauth_client_id="", bonusly_api_token="tok"))
    assert factory.is_linked(BONUSLY) is False
