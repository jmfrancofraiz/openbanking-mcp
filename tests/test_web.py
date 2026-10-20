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


def test_monthly_y_monthly_a_dia_d(client):
    full = client.get("/api/monthly").json()["months"]
    cut = client.get("/api/monthly", params={"day": 10}).json()["months"]

    assert [m["month"] for m in full] == ["2026-03", "2026-04"]
    assert full[0]["expense"] == 170.0
    assert cut[0]["expense"] == 120.0 and cut[0]["cut_day"] == 10


def test_series(client):
    r = client.get("/api/series", params={"from": "2026-03", "to": "2026-04"})

    assert r.status_code == 200
    assert r.json()["expense"]["Alimentación"] == [120.0, 60.0]


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
