"""Proveedor Bonusly: cliente JSON-RPC (paginación, fuentes, ventanas y errores) sin red."""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx
import pytest

from finmcp.config import BankConfig
from finmcp.providers.bonusly.client import BonuslyClient, BonuslyError, _windows

BANK = BankConfig(id="Bonusly", aspsp_name="Bonusly", provider="bonusly")
BAL = {"id": "u1", "currency": "CHF", "exchange_rate": 1.0, "earned_balance": 30, "points_name": "NetCoins"}
EMPTY_FEED = {"recognitions": [], "next_cursor": None}
NO_REDS = {"redemptions": [], "next_cursor": None, "total_count": 0}


def _rec(i, day="2026-04-15", amount=10, **extra):
    return {"id": i, "giver": {"name": "Luis"}, "amount": amount, "reason": "gracias",
            "created_at": f"{day}T10:00:00Z", **extra}


def _red(i, day="2026-04-20", price=5):
    return {"id": i, "created_at": f"{day}T10:00:00Z", "state": "fulfilled",
            "reward_details": {"name": "€5 Amazon.es Gift Card", "price": price, "categories": []}}


def _rpc(obj, is_error=False):
    return {"jsonrpc": "2.0", "id": "1",
            "result": {"content": [{"type": "text", "text": json.dumps(obj)}], "isError": is_error}}


def _client(tools, calls=None, status=200):
    calls = [] if calls is None else calls

    def handler(request):
        assert request.headers["Authorization"] == "Bearer tok"
        body = json.loads(request.content)
        assert body["method"] == "tools/call"
        name, args = body["params"]["name"], body["params"]["arguments"]
        calls.append((name, args))
        if status != 200:
            return httpx.Response(status, json={"error": "unauthorized"})
        return httpx.Response(200, json=_rpc(tools[name](args)))

    return BonuslyClient(BANK, token="tok", eur_per_point=1.08, transport=httpx.MockTransport(handler))


def _tools(received=None, feed=None, reds=None):
    return {
        "getPointsBalance": lambda a: BAL,
        "getRecognitionReceived": received or (lambda a: EMPTY_FEED),
        "getRecognitionFeed": feed or (lambda a: EMPTY_FEED),
        "getMyRedemptions": reds or (lambda a: NO_REDS),
    }


def test_cuenta_y_saldo_con_una_sola_consulta_de_saldo():
    calls = []
    c = _client(_tools(), calls)
    assert [a.provider_account_id for a in c.get_accounts()] == ["bonusly-u1"]
    assert c.get_balance("bonusly-u1").current == 32.4
    assert [n for n, _ in calls] == ["getPointsBalance"]


def test_pagina_reconocimientos_y_canjes():
    pages = {None: {"recognitions": [_rec("b1")], "next_cursor": "c2"},
             "c2": {"recognitions": [_rec("b2")], "next_cursor": None}}
    calls = []
    c = _client(_tools(received=lambda a: pages[a.get("cursor")],
                       reds=lambda a: {"redemptions": [_red("r1")], "next_cursor": None}), calls)

    txs = c.get_transactions("bonusly-u1", "2026-04-01", "2026-04-30")

    assert sorted(t.provider_id for t in txs) == ["bonusly-rec-b1", "bonusly-rec-b2", "bonusly-red-r1"]
    rec_calls = [a for n, a in calls if n == "getRecognitionReceived"]
    assert rec_calls[0] == {"start_date": "2026-04-01", "end_date": "2026-04-30", "limit": 50}
    assert rec_calls[1] == {**rec_calls[0], "cursor": "c2"}  # mismos argumentos + cursor
    feed_calls = [a for n, a in calls if n == "getRecognitionFeed"]
    assert feed_calls[0] == {"current_user_only": ["received"], "recognition_types": ["peer"], "limit": 100}


def test_une_feed_y_recibidos_sin_duplicar():
    c = _client(_tools(
        received=lambda a: {"recognitions": [_rec("b1"), _rec("addon", parent_bonus_id="b0")],
                            "next_cursor": None},
        feed=lambda a: {"recognitions": [_rec("grupo", amount=1), _rec("b1")], "next_cursor": None},
    ))
    txs = c.get_transactions("bonusly-u1", "2026-04-01", "2026-04-30")
    assert sorted(t.provider_id for t in txs) == ["bonusly-rec-addon", "bonusly-rec-b1", "bonusly-rec-grupo"]


def test_feed_y_canjes_paran_al_pasar_del_inicio_y_respetan_la_ventana():
    feed_pages = {None: {"recognitions": [_rec("f1", "2026-04-20"), _rec("f0", "2026-03-31")], "next_cursor": "x"},
                  "x": {"recognitions": [_rec("viejo", "2025-01-01")], "next_cursor": None}}
    calls = []
    c = _client(_tools(
        feed=lambda a: feed_pages[a.get("cursor")],
        reds=lambda a: {"redemptions": [_red("r2", "2026-05-02"), _red("r1", "2026-04-02"),
                                        _red("r0", "2026-03-01")], "next_cursor": "más"},
    ), calls)

    txs = c.get_transactions("bonusly-u1", "2026-04-01", "2026-04-30")

    assert sorted(t.provider_id for t in txs) == ["bonusly-rec-f1", "bonusly-red-r1"]
    assert sum(1 for n, _ in calls if n == "getRecognitionFeed") == 1  # no pide la página «x»
    assert sum(1 for n, _ in calls if n == "getMyRedemptions") == 1  # ni la siguiente de canjes


def test_trocea_rangos_largos():
    calls = []
    c = _client(_tools(), calls)
    c.get_transactions("bonusly-u1", "2023-01-01", "2025-06-30")
    windows = [(a["start_date"], a["end_date"]) for n, a in calls if n == "getRecognitionReceived"]
    assert windows[0][0] == "2023-01-01" and windows[-1][1] == "2025-06-30" and len(windows) == 3


def test_ventanas_contiguas_de_maximo_un_anio():
    ws = list(_windows(date(2023, 1, 1), date(2025, 6, 30), 365))
    assert ws[0][0] == date(2023, 1, 1) and ws[-1][1] == date(2025, 6, 30)
    assert all((b - a).days < 365 for a, b in ws)
    assert all(ws[i + 1][0] == ws[i][1] + timedelta(days=1) for i in range(len(ws) - 1))


def test_sin_fechas_mira_90_dias():
    calls = []
    c = _client(_tools(), calls)
    c.get_transactions("bonusly-u1")
    args = next(a for n, a in calls if n == "getRecognitionReceived")
    assert date.fromisoformat(args["end_date"]) - date.fromisoformat(args["start_date"]) == timedelta(days=90)


def test_401_da_error_claro():
    with pytest.raises(BonuslyError, match="token caducado"):
        _client({}, status=401).get_accounts()


def test_is_error_da_error():
    def handler(request):
        return httpx.Response(200, json=_rpc({"message": "forbidden scope"}, is_error=True))

    c = BonuslyClient(BANK, token="tok", eur_per_point=1.08, transport=httpx.MockTransport(handler))
    with pytest.raises(BonuslyError, match="forbidden scope"):
        c.get_accounts()


def test_sin_token():
    with pytest.raises(BonuslyError, match="BONUSLY_API_TOKEN"):
        BonuslyClient(BANK, token="", eur_per_point=1.08)
