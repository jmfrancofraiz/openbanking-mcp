from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import pytest
from sqlalchemy.exc import OperationalError
from starlette.testclient import TestClient
from typer.testing import CliRunner

from finmcp.web import app as webapp

LAN = ("192.168.1.20", 50000)
TODAY = date(2026, 4, 10)


def at(y: int, m: int, day: int) -> datetime:
    return datetime(y, m, day, tzinfo=timezone.utc)


@pytest.fixture
def datos(make_tx):
    """Subconjunto del ejemplo de la especificación (§10.1)."""
    make_tx(120.00, merchant="Supermercado", booked_at=at(2026, 3, 5),
            my_category="Alimentación - Supermercado")
    make_tx(80.00, merchant="Tienda de ropa", booked_at=at(2026, 3, 12),
            my_category="Moda y complementos")
    make_tx(30.00, type="credit", merchant="Devolución", booked_at=at(2026, 3, 15),
            my_category="Moda y complementos")
    make_tx(500.00, description="Traspaso a cuenta de ahorro", booked_at=at(2026, 3, 20),
            my_category="Traspasos internos", neutral=True)
    make_tx(3000.00, type="credit", description="Nómina", booked_at=at(2026, 3, 28),
            my_category="Ingresos - Nómina")
    make_tx(60.00, merchant="Supermercado", booked_at=at(2026, 4, 2),
            my_category="Alimentación - Supermercado")


@pytest.fixture
def app(Session, session, datos):
    return webapp.create_app(Session, today=lambda: TODAY)


@pytest.fixture
def client(app):
    return TestClient(app, client=LAN)


# --- API ---------------------------------------------------------------------


def test_meta(client):
    r = client.get("/api/meta")

    assert r.status_code == 200
    body = r.json()
    assert body["current_month"] == "2026-04" and body["cut_day"] == 10
    assert body["months"] == ["2026-03", "2026-04"]


def test_breakdown(client):
    r = client.get("/api/breakdown", params={"from": "2026-03-01", "to": "2026-03-31"})

    assert r.status_code == 200
    body = r.json()
    assert body["income"]["total"] == 3000.0
    assert body["expense"]["total"] == 170.0
    assert body["refunds"] == 30.0
    assert body["net"] == 2830.0
    assert body["neutral"]["total"] == -500.0
    assert [g["group"] for g in body["neutral"]["groups"]] == ["Traspasos internos"]


def test_monthly_y_monthly_a_dia_d(client):
    full = client.get("/api/monthly").json()["months"]
    cut = client.get("/api/monthly", params={"day": 10}).json()["months"]

    assert [m["month"] for m in full] == ["2026-03", "2026-04"]
    assert full[0]["expense"] == 170.0
    assert cut[0]["expense"] == 120.0 and cut[0]["cut_day"] == 10


def test_monthly_con_since_y_until(client):
    """Carga por años de la pestaña Ahorro: `since`/`until` acotan sin tocar el resto."""
    solo_marzo = client.get("/api/monthly", params={"since": "2026-03", "until": "2026-03"}).json()["months"]
    desde_abril = client.get("/api/monthly", params={"since": "2026-04"}).json()["months"]

    assert [m["month"] for m in solo_marzo] == ["2026-03"]
    assert solo_marzo[0]["expense"] == 170.0
    assert [m["month"] for m in desde_abril] == ["2026-04"]


def test_monthly_since_posterior_a_until_es_error(client):
    r = client.get("/api/monthly", params={"since": "2026-04", "until": "2026-03"})

    assert r.status_code == 400


def test_series(client):
    r = client.get("/api/series", params={"from": "2026-03", "to": "2026-04"})

    assert r.status_code == 200
    assert r.json()["expense"]["Alimentación"] == [120.0, 60.0]


def test_references(client):
    r = client.get("/api/references", params={"to": "2026-04"})

    assert r.status_code == 200
    body = r.json()
    assert body["months"] == 1  # único mes cerrado disponible: marzo
    assert body["expense"]["Alimentación"] == 120.0
    assert body["income"]["Ingresos - Nómina"] == 3000.0


def test_groups(client):
    r = client.get("/api/groups")

    assert r.status_code == 200
    body = r.json()
    assert body["expense"] == ["Alimentación", "Moda y complementos"]
    assert body["income"] == ["Ingresos - Nómina"]
    assert body["neutral"] == ["Traspasos internos"]


def test_transactions_por_categoria_y_subcategoria(client):
    base = {"from": "2026-03-01", "to": "2026-04-30", "type": "gastos"}
    by_group = client.get("/api/transactions", params={**base, "group": "Moda y complementos"})
    by_cat = client.get("/api/transactions",
                        params={**base, "category": "Alimentación - Supermercado"})
    income = client.get("/api/transactions",
                        params={"from": "2026-03-01", "to": "2026-03-31", "type": "ingresos"})

    assert [t["amount"] for t in by_group.json()["transactions"]] == [-80.0, 30.0]
    assert [t["date"] for t in by_cat.json()["transactions"]] == ["2026-03-05", "2026-04-02"]
    assert [t["category"] for t in income.json()["transactions"]] == ["Ingresos - Nómina"]


def test_transactions_no_computables(client):
    r = client.get("/api/transactions", params={
        "from": "2026-03-01", "to": "2026-03-31", "type": "no-computables",
    })

    assert r.status_code == 200
    body = r.json()
    assert len(body["transactions"]) == 1
    assert body["transactions"][0]["category"] == "Traspasos internos"
    assert body["transactions"][0]["amount"] == -500.0


@pytest.mark.parametrize(
    "path, params",
    [
        ("/api/breakdown", {}),
        ("/api/breakdown", {"from": "2026-03-01"}),
        ("/api/breakdown", {"from": "2026-3-1", "to": "2026-03-31"}),
        ("/api/breakdown", {"from": "2026-02-30", "to": "2026-03-31"}),
        ("/api/breakdown", {"from": "2026-04-01", "to": "2026-03-31"}),
        ("/api/monthly", {"day": "0"}),
        ("/api/monthly", {"day": "32"}),
        ("/api/monthly", {"day": "x"}),
        ("/api/series", {"from": "2026-13", "to": "2026-04"}),
        ("/api/series", {"from": "2026-04", "to": "2026-03"}),
        ("/api/transactions", {"from": "2026-03-01", "to": "2026-03-31"}),
        ("/api/transactions", {"from": "2026-03-01", "to": "2026-03-31", "type": "otros"}),
        ("/api/transactions", {"from": "2026-03-01", "to": "2026-03-31", "type": "gastos",
                               "group": "Ocio", "category": "Ocio"}),
    ],
)
def test_parametros_invalidos_devuelven_400(client, path, params):
    r = client.get(path, params=params)

    assert r.status_code == 400
    assert r.json()["error"]


# --- interfaz y cabeceras ----------------------------------------------------


def test_index_y_estaticos(client):
    index = client.get("/")
    js = client.get("/static/app.js")
    css = client.get("/static/app.css")

    assert index.status_code == 200 and "text/html" in index.headers["content-type"]
    assert '<script src="/static/lib.js"' in index.text
    assert '<script src="/static/app.js"' in index.text
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert css.status_code == 200


def test_instalable_como_aplicacion_sin_service_worker(client):
    """PWA: manifiesto enlazado, servido con su tipo y con lo que Chrome exige para instalar; sin service worker."""
    import json
    import struct

    index = client.get("/")
    assert '<link rel="manifest" href="/static/manifest.webmanifest">' in index.text
    # Ningún service worker: nada del panel queda guardado en el dispositivo.
    assert "serviceWorker" not in client.get("/static/app.js").text

    r = client.get("/static/manifest.webmanifest")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/manifest+json")
    m = json.loads(r.text)
    assert m["name"] and m["short_name"] and m["display"] == "standalone"
    assert m["start_url"] == "/" and m["scope"] == "/"
    sizes = {(i["sizes"], i.get("purpose", "any")) for i in m["icons"]}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= sizes
    for icon in m["icons"]:
        png = client.get(icon["src"])
        assert png.status_code == 200 and png.headers["content-type"] == "image/png"
        assert png.content[:8] == b"\x89PNG\r\n\x1a\n"
        w, h = struct.unpack(">II", png.content[16:24])
        assert f"{w}x{h}" == icon["sizes"]


def test_cabeceras_de_seguridad_y_cache(client):
    api = client.get("/api/meta")
    index = client.get("/")

    csp = index.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp
    assert index.headers["x-content-type-options"] == "nosniff"
    assert index.headers["x-frame-options"] == "DENY"
    assert index.headers["referrer-policy"] == "no-referrer"
    assert api.headers["cache-control"] == "no-store"
    assert index.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_no_hay_metodos_de_escritura(client, method):
    assert getattr(client, method)("/api/meta").status_code == 405


# --- acceso solo desde la red local ------------------------------------------


@pytest.mark.parametrize("host", ["8.8.8.8", "100.101.3.5", "testclient"])
def test_rechaza_clientes_fuera_de_la_red_local(app, host):
    r = TestClient(app, client=(host, 1234)).get("/api/meta")

    assert r.status_code == 403
    assert r.headers["x-frame-options"] == "DENY"


def test_tailscale_solo_si_se_permite(Session, session, datos):
    app = webapp.create_app(Session, today=lambda: TODAY, allow_tailscale=True)

    assert TestClient(app, client=("100.101.3.5", 1)).get("/api/meta").status_code == 200
    assert TestClient(app, client=("8.8.8.8", 1)).get("/api/meta").status_code == 403


@pytest.mark.parametrize(
    "host, allowed",
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("10.1.2.3", True),
        ("172.20.0.1", True),
        ("192.168.0.37", True),
        ("::ffff:192.168.0.5", True),
        ("fd00::1", True),
        ("8.8.8.8", False),
        ("172.32.0.1", False),
        ("100.101.3.99", False),
        ("testclient", False),
    ],
)
def test_client_allowed(host, allowed):
    assert webapp.client_allowed(host, webapp.LAN_NETWORKS) is allowed


# --- arranque ----------------------------------------------------------------


def test_validate_hosts():
    assert webapp.validate_hosts(["192.168.0.10"]) == (["192.168.0.10"], False)
    assert webapp.validate_hosts(["localhost", "100.101.3.99"]) == (
        ["127.0.0.1", "100.101.3.99"], True)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "8.8.8.8", "mini.local", ""])
def test_validate_hosts_rechaza_direcciones_no_privadas(host):
    with pytest.raises(ValueError):
        webapp.validate_hosts([host])


def test_readonly_session_factory(tmp_path):
    db = tmp_path / "finmcp.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (x INTEGER)")
    con.commit()
    con.close()

    Session = webapp.readonly_session_factory(db)
    with Session() as s:
        assert s.connection().exec_driver_sql("SELECT count(*) FROM t").scalar() == 0
        with pytest.raises(OperationalError, match="readonly"):
            s.connection().exec_driver_sql("INSERT INTO t VALUES (1)")


def test_readonly_session_factory_sin_bd(tmp_path):
    with pytest.raises(FileNotFoundError):
        webapp.readonly_session_factory(tmp_path / "no-existe.db")


def test_cli_web_arranca_con_hosts_y_puerto(monkeypatch):
    from finmcp.cli import app as cli

    calls = []
    monkeypatch.setattr(webapp, "run", lambda hosts, port: calls.append((hosts, port)))

    result = CliRunner().invoke(cli, ["web", "--host", "192.168.0.10", "--port", "8799"])

    assert result.exit_code == 0, result.output
    assert calls == [(["192.168.0.10"], 8799)]


def test_cli_web_por_defecto_loopback_y_8790(monkeypatch):
    from finmcp.cli import app as cli

    calls = []
    monkeypatch.setattr(webapp, "run", lambda hosts, port: calls.append((hosts, port)))

    assert CliRunner().invoke(cli, ["web"]).exit_code == 0
    assert calls == [(["127.0.0.1"], 8790)]


def test_cli_web_rechaza_0000():
    from finmcp.cli import app as cli

    result = CliRunner().invoke(cli, ["web", "--host", "0.0.0.0"])

    assert result.exit_code == 2
    assert "0.0.0.0" in result.output


# --- pestaña Diario ------------------------------------------------------------


@pytest.fixture
def diario(Session, session, make_tx):
    from finmcp.db.models import Balance, SyncRun

    make_tx(12.5, merchant="Café", booked_at=at(2026, 4, 9).replace(tzinfo=None), my_category="Restauración")
    make_tx(500, type="credit", description="Traspaso", booked_at=at(2026, 4, 9).replace(tzinfo=None),
            my_category="Traspasos internos", neutral=True)
    make_tx(60, description="Garaje", booked_at=at(2026, 4, 1).replace(tzinfo=None), is_bill=True,
            my_category="Vivienda - Garaje")
    session.add(Balance(account_id="acc1", current=1000.0, currency="EUR",
                        snapshot_at=datetime(2026, 4, 10, 5, 30)))
    session.add(SyncRun(bank_id="default", started_at=datetime(2026, 4, 10, 5, 30),
                        finished_at=datetime(2026, 4, 10, 5, 31), status="ok"))
    session.commit()
    app = webapp.create_app(Session, today=lambda: TODAY,
                            now=lambda: datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc))
    return TestClient(app, client=LAN)


def test_api_accounts_y_tags(diario):
    body = diario.get("/api/accounts").json()

    assert body["now"] == "2026-04-10T12:00:00Z"
    [acc] = body["accounts"]
    assert acc["id"] == "acc1" and acc["state"] == "ok"
    assert acc["balance"] == {"value": 1000.0, "kind": "actual", "at": "2026-04-10T05:30:00Z"}
    assert body["total"] == {"value": 1000.0, "currency": "EUR", "count": 1, "missing": [], "disponible": [],
                             "outdated": [], "oldest": "2026-04-10T05:30:00Z", "state": "ok"}
    assert diario.get("/api/tags").json() == {"tags": []}


def test_api_ledger_lista_plana_con_no_computables(diario):
    body = diario.get("/api/ledger").json()

    assert [(t["date"], t["amount"], t["neutral"]) for t in body["transactions"]] == [
        ("2026-04-09", 500.0, True), ("2026-04-09", -12.5, False), ("2026-04-01", -60.0, False)]
    assert body["next"] is None and body["closing"] is None


def test_api_ledger_filtros_cursor_y_saldo_por_cuenta(diario):
    assert [t["amount"] for t in diario.get("/api/ledger", params={"type": "cargos"}).json()["transactions"]] == [-12.5, -60.0]
    assert [t["is_bill"] for t in diario.get("/api/ledger", params={"mark": "recibos"}).json()["transactions"]] == [True]
    assert len(diario.get("/api/ledger", params={"mark": "no-computables"}).json()["transactions"]) == 1
    assert len(diario.get("/api/ledger", params={"from": "2026-04-02", "to": "2026-04-30"}).json()["transactions"]) == 2
    assert [t["merchant"] for t in diario.get("/api/ledger", params={"q": "CAFE"}).json()["transactions"]] == ["Café"]

    first = diario.get("/api/ledger", params={"limit": 2}).json()
    rest = diario.get("/api/ledger", params={"limit": 2, "cursor": first["next"]}).json()
    assert [t["id"] for t in first["transactions"] + rest["transactions"]] == ["tx2", "tx1", "tx3"]
    assert rest["next"] is None

    acc = diario.get("/api/ledger", params={"account": "acc1"}).json()
    assert acc["closing"] == {"2026-04-09": 1000.0, "2026-04-01": 512.5}
    assert acc["balance"]["check"]["ok"] == 1


def test_api_ledger_account_admite_varias_cuentas_separadas_por_comas(diario, session, make_tx):
    # El bloque de saldos del Diario permite togglear varias cuentas a la vez: el parámetro
    # «account» acepta una lista separada por comas, combinadas con OR (sin excluirse entre sí).
    from finmcp.db.models import Account

    session.add(Account(id="acc2", bank_id="BBVA", name="Cuenta BBVA", type="CACC", currency="EUR"))
    make_tx(99, description="BBVA aparte", booked_at=at(2026, 4, 9).replace(tzinfo=None), account_id="acc2")
    session.commit()

    only_acc1 = diario.get("/api/ledger", params={"account": "acc1"}).json()
    both = diario.get("/api/ledger", params={"account": "acc1,acc2"}).json()

    assert len(only_acc1["transactions"]) == 3  # las tres de la fixture `diario`, todas en acc1
    assert len(both["transactions"]) == 4       # + la nueva de acc2
    # Con dos cuentas el saldo al cierre deja de tener un significado único: no se calcula.
    assert both["closing"] is None and both["balance"] is None


def test_api_movement(diario):
    ok = diario.get("/api/movement", params={"id": "tx1"})
    missing = diario.get("/api/movement", params={"id": "nada"})

    assert ok.status_code == 200 and ok.json()["merchant"] == "Café"
    assert ok.json()["account_info"]["id"] == "acc1"
    assert missing.status_code == 404 and missing.json()["error"]


@pytest.mark.parametrize(
    "path, params",
    [
        ("/api/ledger", {"type": "gastos"}),
        ("/api/ledger", {"mark": "otra"}),
        ("/api/ledger", {"from": "2026-4-1"}),
        ("/api/ledger", {"from": "2026-04-10", "to": "2026-04-01"}),
        ("/api/ledger", {"cursor": "ayer"}),
        ("/api/ledger", {"cursor": "2026-04-09T00:00:00|tx 1"}),
        ("/api/ledger", {"limit": "0"}),
        ("/api/ledger", {"limit": "201"}),
        ("/api/ledger", {"q": "x" * 101}),
        ("/api/ledger", {"account": "no-existe"}),
        ("/api/ledger", {"account": "../etc"}),
        ("/api/ledger", {"account": "acc1,no-existe"}),  # una cuenta válida no salva a una desconocida
        ("/api/ledger", {"account": ",,"}),  # solo separadores: ninguna cuenta real
        ("/api/movement", {}),
        ("/api/movement", {"id": "a b"}),
    ],
)
def test_api_diario_parametros_invalidos(diario, path, params):
    r = diario.get(path, params=params)

    assert r.status_code == 400
    assert r.json()["error"]


@pytest.mark.parametrize("path", ["/api/accounts", "/api/tags", "/api/ledger", "/api/movement"])
def test_api_diario_solo_lectura(diario, path):
    assert diario.post(path).status_code == 405
