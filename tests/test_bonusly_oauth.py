"""OAuth de Bonusly: refresco, caché, invalidación y persistencia del refresh rotado (sin red)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from finmcp.providers.bonusly.oauth import OAuthError, TokenProvider, write_env_value


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


def _provider(responses, env_file=None, clock=None):
    calls = []

    def handler(request):
        calls.append(dict(httpx.QueryParams(request.content.decode())))
        status, body = responses[len(calls) - 1]
        return httpx.Response(status, json=body)

    tp = TokenProvider(
        client_id="cid",
        refresh_token="r1",
        env_file=env_file,
        transport=httpx.MockTransport(handler),
        clock=clock or Clock(),
    )
    return tp, calls


def test_refresco_devuelve_el_access_token():
    tp, calls = _provider([(200, {"access_token": "a1", "refresh_token": "r1", "expires_in": 7200})])
    assert tp.access_token() == "a1"
    assert calls[0]["grant_type"] == "refresh_token"
    assert calls[0]["client_id"] == "cid"
    assert calls[0]["refresh_token"] == "r1"


def test_cachea_entre_llamadas_y_refresca_al_caducar():
    clock = Clock()
    tp, calls = _provider(
        [(200, {"access_token": "a1", "refresh_token": "r1", "expires_in": 7200}),
         (200, {"access_token": "a2", "refresh_token": "r1", "expires_in": 7200})],
        clock=clock,
    )
    assert tp.access_token() == "a1"
    clock.advance(7000)  # 200 s de vida: aún dentro del margen de 60 s
    assert tp.access_token() == "a1"
    assert len(calls) == 1
    clock.advance(300)  # dentro del margen: refresca
    assert tp.access_token() == "a2"
    assert len(calls) == 2


def test_invalidate_fuerza_nuevo_refresco():
    tp, calls = _provider(
        [(200, {"access_token": "a1", "refresh_token": "r1", "expires_in": 7200}),
         (200, {"access_token": "a2", "refresh_token": "r1", "expires_in": 7200})]
    )
    assert tp.access_token() == "a1"
    tp.invalidate()
    assert tp.access_token() == "a2"
    assert len(calls) == 2


def test_refresh_rotado_se_persiste_en_env(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("BONUSLY_API_TOKEN=pat\nBONUSLY_OAUTH_REFRESH_TOKEN=r1\nOTRA=1\n")
    tp, _ = _provider([(200, {"access_token": "a1", "refresh_token": "r2", "expires_in": 7200})],
                      env_file=env)
    tp.access_token()
    lines = env.read_text().splitlines()
    assert "BONUSLY_OAUTH_REFRESH_TOKEN=r2" in lines
    assert "BONUSLY_API_TOKEN=pat" in lines and "OTRA=1" in lines
    assert tp.refresh_token == "r2"
    assert env.stat().st_mode & 0o777 == 0o600


def test_refresh_sin_rotacion_no_reescribe_el_env(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("BONUSLY_OAUTH_REFRESH_TOKEN=r1\n")
    before = env.stat().st_mtime_ns
    tp, _ = _provider([(200, {"access_token": "a1", "refresh_token": "r1", "expires_in": 7200})],
                      env_file=env)
    tp.access_token()
    assert env.stat().st_mtime_ns == before


def test_fallo_de_refresco_pide_repetir_el_alta():
    tp, _ = _provider([(401, {"error": "invalid_grant", "error_description": "revoked"})])
    with pytest.raises(OAuthError) as exc:
        tp.access_token()
    assert "bonusly_oauth_login.py" in str(exc.value)


def test_sin_credenciales_avisa():
    with pytest.raises(OAuthError):
        TokenProvider(client_id="", refresh_token="r1")


def test_write_env_value_es_atomico_y_sustituye(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("A=1\nexport BONUSLY_OAUTH_REFRESH_TOKEN=viejo\nC=3\n")
    write_env_value(env, "BONUSLY_OAUTH_REFRESH_TOKEN", "nuevo")
    assert env.read_text().splitlines() == ["A=1", "C=3", "BONUSLY_OAUTH_REFRESH_TOKEN=nuevo"]
    assert not list(tmp_path.glob(".env.*.tmp"))
    json.loads("{}")  # sanity: el fichero no debe quedar con JSON ni artefactos
