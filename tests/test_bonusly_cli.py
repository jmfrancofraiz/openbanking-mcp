"""Bonusly en la CLI y el MCP: `banks list`, `auth` y `list_banks` conscientes del proveedor."""

from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

import finmcp.config as config
from finmcp.cli import app
from finmcp.config import BankConfig
from finmcp.mcp import server

BONUSLY = BankConfig(id="Bonusly", aspsp_name="Bonusly", provider="bonusly")


def _settings(monkeypatch, token="tok"):
    monkeypatch.setattr(config, "settings", SimpleNamespace(banks=[BONUSLY], bonusly_api_token=token))


def test_banks_list_muestra_bonusly_vinculado(monkeypatch):
    _settings(monkeypatch)
    out = CliRunner().invoke(app, ["banks", "list"]).output
    assert "- Bonusly · Bonusly · ES · vinculado: sí" in out


def test_banks_list_sin_token_no_esta_vinculado(monkeypatch):
    _settings(monkeypatch, token="")
    assert "vinculado: no" in CliRunner().invoke(app, ["banks", "list"]).output


def test_auth_rechaza_bonusly(monkeypatch):
    _settings(monkeypatch)
    res = CliRunner().invoke(app, ["auth", "--bank", "Bonusly"])
    assert res.exit_code == 2
    assert "BONUSLY_API_TOKEN" in res.output


def test_mcp_list_banks_da_proveedor_y_vinculo(monkeypatch):
    _settings(monkeypatch)
    assert server.list_banks() == [
        {"id": "Bonusly", "aspsp_name": "Bonusly", "country": "ES", "provider": "bonusly", "linked": True}
    ]
