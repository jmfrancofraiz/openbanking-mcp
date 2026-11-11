"""Cliente Bonusly con OAuth: refresco ante un 401 y un solo reintento (sin red)."""

from __future__ import annotations

import json

import httpx

from finmcp.config import BankConfig
from finmcp.providers.bonusly.client import BonuslyClient, BonuslyError
from finmcp.providers.bonusly.oauth import TokenProvider

BANK = BankConfig(id="Bonusly", aspsp_name="Bonusly", provider="bonusly")
BAL = {"id": "u1", "currency": "CHF", "exchange_rate": 1.0, "earned_balance": 30}
RPC = {"jsonrpc": "2.0", "id": "1",
       "result": {"content": [{"type": "text", "text": json.dumps(BAL)}], "isError": False}}


def _provider(statuses):
    """TokenProvider con respuestas de refresco encadenadas y su contador de llamadas."""
    calls = []

    def token_handler(request):
        calls.append(1)
        token = f"a{len(calls)}"
        refresh = f"r{len(calls)}"
        return httpx.Response(200, json={"access_token": token, "refresh_token": refresh,
                                        "expires_in": 7200})

    return TokenProvider(client_id="cid", refresh_token="r0",
                         transport=httpx.MockTransport(token_handler)), calls


def test_un_401_refresca_y_reintenta_una_vez():
    tp, token_calls = _provider(None)
    seen = []

    def mcp_handler(request):
        seen.append(request.headers["Authorization"])
        if len(seen) == 1:
            return httpx.Response(401, json={"error": "expired"})
        return httpx.Response(200, json=RPC)

    c = BonuslyClient(BANK, eur_per_point=1.08, token_provider=tp,
                      transport=httpx.MockTransport(mcp_handler))
    assert c.get_accounts()[0].provider_account_id == "bonusly-u1"
    assert seen == ["Bearer a1", "Bearer a2"]          # refrescó y reintentó con el nuevo
    assert len(token_calls) == 2


def test_dos_401_seguidos_no_reintentan_mas_y_piden_repetir_el_alta():
    tp, token_calls = _provider(None)
    seen = []

    def mcp_handler(request):
        seen.append(request.headers["Authorization"])
        return httpx.Response(401, json={"error": "unauthorized"})

    c = BonuslyClient(BANK, eur_per_point=1.08, token_provider=tp,
                      transport=httpx.MockTransport(mcp_handler))
    try:
        c.get_accounts()
    except BonuslyError as exc:
        assert "bonusly_oauth_login.py" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("debía lanzar BonuslyError")
    assert len(seen) == 2                              # original + un solo reintento
    assert len(token_calls) == 2


def test_token_estatico_sigue_funcionando_sin_refresco():
    def mcp_handler(request):
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(200, json=RPC)

    c = BonuslyClient(BANK, token="tok", eur_per_point=1.08,
                      transport=httpx.MockTransport(mcp_handler))
    assert c.get_balance("bonusly-u1").current == 32.4
