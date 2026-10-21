from __future__ import annotations

import os
from datetime import date

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from finmcp.config import settings
from finmcp.web import app as webapp
from finmcp.web import auth

PIN = "2580"  # PIN de pruebas
LAN = ("192.168.1.20", 50000)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture(scope="module")
def pin_hash() -> str:
    return auth.hash_pin(PIN, iterations=1000)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def app(Session, session, pin_hash, clock):
    return webapp.create_app(
        Session, today=lambda: date(2026, 4, 10), pin_hash=pin_hash,
        session_secret=b"k" * 32, throttle=auth.Throttle(clock=clock),
    )


@pytest.fixture
def client(app):
    return TestClient(app, client=LAN)


def login(client, pin=PIN, **kw):
    return client.post("/api/login", json={"pin": pin}, **kw)


# --- huella del PIN ------------------------------------------------------------


def test_hash_y_verificacion(pin_hash):
    assert pin_hash.startswith("pbkdf2_sha256:1000:")
    assert "$" not in pin_hash  # sin «$»: python-dotenv lo interpretaría
    assert auth.valid_hash(pin_hash)
    assert auth.verify_pin(PIN, pin_hash)
    assert not auth.verify_pin("2581", pin_hash)
    assert auth.hash_pin(PIN, iterations=1000) != pin_hash  # sal aleatoria


@pytest.mark.parametrize("stored", ["", "x", "md5:1:aa:bb", "pbkdf2_sha256:mil:aa:bb", "pbkdf2_sha256:1000:zz:bb"])
def test_huellas_invalidas(stored):
    assert not auth.valid_hash(stored)
    assert not auth.verify_pin(PIN, stored)


@pytest.mark.parametrize("pin, ok", [("2580", True), ("123456789012", True), ("123", False),
                                     ("1234567890123", False), ("12a4", False), ("", False), (" 2580", False)])
def test_formato_del_pin(pin, ok):
    assert auth.valid_pin(pin) is ok


def test_hash_rechaza_pin_mal_formado():
    with pytest.raises(ValueError):
        auth.hash_pin("12")


# --- freno a los intentos fallidos ---------------------------------------------


def test_throttle_bloqueo_progresivo(clock):
    t = auth.Throttle(clock=clock)
    for _ in range(4):
        t.failure()
    assert t.retry_after() == 0

    t.failure()  # 5.º fallo seguido: 30 s
    assert t.retry_after() == 30
    clock.t += 30
    assert t.retry_after() == 0

    t.failure()  # 6.º: 60 s
    assert t.retry_after() == 60
    for _ in range(10):
        t.failure()
    assert t.retry_after() == 3600  # tope de una hora

    t.success()
    assert t.retry_after() == 0
    t.failure()
    assert t.retry_after() == 0


# --- sesiones firmadas ---------------------------------------------------------


def test_sesiones(clock, pin_hash):
    s = auth.Sessions(b"k" * 32, pin_hash, clock=clock)
    token = s.issue()

    assert s.valid(token)
    assert not s.valid(token[:-1] + ("0" if token[-1] != "0" else "1"))
    assert not s.valid("") and not s.valid(None) and not s.valid("abc")
    assert not auth.Sessions(b"j" * 32, pin_hash, clock=clock).valid(token)
    assert not auth.Sessions(b"k" * 32, auth.hash_pin("1111", iterations=1000), clock=clock).valid(token)
    clock.t += auth.SESSION_TTL + 1
    assert not s.valid(token)


def test_secreto_persistente(tmp_path):
    path = tmp_path / "web_session.key"
    first = auth.load_secret(path)

    assert len(first) == 32
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert auth.load_secret(path) == first


# --- .env ------------------------------------------------------------------------


def test_write_env_value(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1\nFINMCP_WEB_PIN_HASH=viejo\n# comentario\n")

    auth.write_env_value(env, "FINMCP_WEB_PIN_HASH", "nuevo")
    assert env.read_text() == "A=1\n# comentario\nFINMCP_WEB_PIN_HASH=nuevo\n"

    auth.write_env_value(env, "FINMCP_WEB_PIN_HASH", None)
    assert env.read_text() == "A=1\n# comentario\n"


def test_write_env_value_crea_fichero_privado(tmp_path):
    env = tmp_path / ".env"
    auth.write_env_value(env, "K", "v")

    assert env.read_text() == "K=v\n"
    assert oct(os.stat(env).st_mode & 0o777) == "0o600"


# --- API con PIN -----------------------------------------------------------------


def test_sin_sesion_la_api_pide_pin_pero_la_interfaz_es_publica(client):
    r = client.get("/api/meta")

    assert r.status_code == 401
    assert r.json()["auth"] == "pin"
    assert r.headers["cache-control"] == "no-store"
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200


@pytest.mark.parametrize("path", ["/api/accounts", "/api/tags", "/api/ledger", "/api/movement?id=tx1"])
def test_la_pestana_diario_tambien_exige_pin(client, path):
    r = client.get(path)

    assert r.status_code == 401 and r.json()["auth"] == "pin"
    login(client)
    assert client.get(path).status_code in (200, 404)  # con sesión responde (404: movimiento inexistente)


def test_login_correcto_abre_sesion_y_logout_la_cierra(client):
    r = login(client)

    assert r.status_code == 204
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{auth.SESSION_COOKIE}=")
    for attr in ("HttpOnly", "SameSite=strict", "Path=/", f"Max-Age={auth.SESSION_TTL}"):
        assert attr in cookie
    meta = client.get("/api/meta")
    assert meta.status_code == 200 and meta.json()["pin"] is True

    assert client.post("/api/logout").status_code == 204
    assert client.get("/api/meta").status_code == 401


def test_login_incorrecto(client):
    r = login(client, "0000")

    assert r.status_code == 401
    assert r.json()["error"] == "PIN incorrecto."
    assert "auth" not in r.json()
    assert "set-cookie" not in r.headers
    assert client.get("/api/meta").status_code == 401


def test_login_exige_json(client):
    r = client.post("/api/login", data={"pin": PIN})

    assert r.status_code == 415
    assert login(client).status_code == 204  # el intento no cuenta como fallo


@pytest.mark.parametrize("body", [{}, {"pin": 2580}, {"pin": "12"}, ["2580"]])
def test_login_con_cuerpo_invalido_es_fallo(client, body):
    assert client.post("/api/login", json=body).status_code == 401


def test_bloqueo_tras_cinco_fallos_incluso_con_el_pin_correcto(client, clock):
    for _ in range(4):
        assert login(client, "0000").status_code == 401
    fifth = login(client, "0000")
    assert fifth.status_code == 401 and fifth.json()["retry_after"] == 30

    locked = login(client)
    assert locked.status_code == 429
    assert locked.headers["retry-after"] == "30"
    assert locked.json()["retry_after"] == 30

    clock.t += 30
    assert login(client).status_code == 204


def test_cookie_falsificada(client):
    client.cookies.set(auth.SESSION_COOKIE, "9999999999.deadbeef")

    assert client.get("/api/meta").status_code == 401


def test_sin_pin_no_hay_login(Session, session):
    client = TestClient(webapp.create_app(Session, today=lambda: date(2026, 4, 10)), client=LAN)

    assert client.get("/api/meta").json()["pin"] is False
    assert login(client).status_code == 404


def test_login_desde_fuera_de_la_red_local(app):
    assert TestClient(app, client=("8.8.8.8", 1)).post("/api/login", json={"pin": PIN}).status_code == 403


# --- CLI -------------------------------------------------------------------------


def test_cli_web_pin_guarda_solo_la_huella(tmp_path, monkeypatch):
    from finmcp.cli import app as cli

    env = tmp_path / ".env"
    env.write_text("A=1\n")
    monkeypatch.setattr(settings, "_env_file_path", env)

    result = CliRunner().invoke(cli, ["web-pin"], input=f"{PIN}\n{PIN}\n")

    assert result.exit_code == 0, result.output
    text = env.read_text()
    assert PIN not in text
    value = text.split("FINMCP_WEB_PIN_HASH=", 1)[1].strip()
    assert auth.verify_pin(PIN, value)

    removed = CliRunner().invoke(cli, ["web-pin", "--remove"])
    assert removed.exit_code == 0
    assert env.read_text() == "A=1\n"


def test_cli_web_pin_rechaza_pin_mal_formado(tmp_path, monkeypatch):
    from finmcp.cli import app as cli

    env = tmp_path / ".env"
    monkeypatch.setattr(settings, "_env_file_path", env)

    result = CliRunner().invoke(cli, ["web-pin"], input="12\n12\n")

    assert result.exit_code == 2
    assert not env.exists()


def test_cli_web_avisa_sin_pin_y_rechaza_huella_invalida(monkeypatch):
    from finmcp.cli import app as cli

    monkeypatch.setattr(webapp, "run", lambda hosts, port: None)
    monkeypatch.setattr(settings, "finmcp_web_pin_hash", "")
    warned = CliRunner().invoke(cli, ["web"])
    assert warned.exit_code == 0 and "no tiene PIN" in warned.output

    monkeypatch.setattr(settings, "finmcp_web_pin_hash", "basura")
    bad = CliRunner().invoke(cli, ["web"])
    assert bad.exit_code == 2 and "FINMCP_WEB_PIN_HASH" in bad.output
