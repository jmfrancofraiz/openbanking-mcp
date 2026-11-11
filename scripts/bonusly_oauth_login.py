"""Alta OAuth de Bonusly (consentimiento único de navegador) para el proveedor de finmcp.

Desde nov-2026 `https://bonus.ly/mcp` ya no acepta el PAT: exige OAuth 2.0 con
authorization code + PKCE S256, y el cliente se registra por DCR. El refresh token ROTA en
cada uso; `finmcp.providers.bonusly.oauth.TokenProvider` lo refresca y lo reescribe solo.

Uso, en dos actos y sin plazos:

  1) python scripts/bonusly_oauth_login.py url
     Registra el cliente si no hay uno guardado y escribe la URL de consentimiento.
     El `state` y el `code_verifier` se guardan en disco ANTES de imprimirla
     (~/.hermes/cache/scratch/bonusly_oauth_pkce.json, 0600, acumulando intentos).

  2) python scripts/bonusly_oauth_login.py redeem --callback-url '<URL del callback>'
     Canjea el `code`, comprueba el acceso con las herramientas del MCP y escribe
     BONUSLY_OAUTH_CLIENT_ID y BONUSLY_OAUTH_REFRESH_TOKEN en el `.env` (permisos 600).
     Si se lanzó `scripts/bonusly_callback_listener.py` en paralelo, basta con `redeem`.

Solo lectura sobre Bonusly: no otorga puntos, no canjea recompensas y no modifica la cuenta.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.parse
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from finmcp.providers.bonusly.oauth import (  # noqa: E402
    CLIENT_ENV,
    REFRESH_ENV,
    write_env_value,
)

AUTH = "https://bonus.ly/oauth/authorize"
TOKEN = "https://bonus.ly/oauth/token"
MCP = "https://bonus.ly/mcp"
REGISTER = "https://bonus.ly/oauth/register"
SCOPE = "user:read recognition:read rewards:read"
PORT = 8765
REDIRECT = f"http://127.0.0.1:{PORT}/callback"
SCRATCH = Path(os.path.expanduser("~/.hermes/cache/scratch"))
PKCE = SCRATCH / "bonusly_oauth_pkce.json"
CODE = SCRATCH / "bonusly_oauth_code.txt"


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def register_client() -> str:
    """Registro dinámico de un cliente público con PKCE; devuelve su `client_id`."""
    body = {
        "client_name": "finmcp",
        "redirect_uris": [REDIRECT, f"http://localhost:{PORT}/callback"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": SCOPE,
    }
    r = httpx.post(REGISTER, json=body, timeout=60, headers={"Accept": "application/json"})
    if r.status_code not in (200, 201):
        sys.exit(f"DCR falló: HTTP {r.status_code} {r.text[:200]}")
    return r.json()["client_id"]


def cmd_url(a) -> int:
    env = Path(a.env)
    client_id = a.client_id or os.environ.get(CLIENT_ENV, "")
    if not client_id:
        client_id = register_client()
        write_env_value(env, CLIENT_ENV, client_id)
        print(f"cliente registrado por DCR y guardado como {CLIENT_ENV} en {env}")
    verifier = b64u(secrets.token_bytes(48))
    challenge = b64u(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_urlsafe(16)
    url = AUTH + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
        "scope": SCOPE, "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256"})
    entries = []
    if PKCE.exists():
        try:
            old = json.loads(PKCE.read_text())
            entries = old if isinstance(old, list) else [old]
        except json.JSONDecodeError:
            entries = []
    entries.append({"client_id": client_id, "verifier": verifier, "state": state,
                    "redirect_uri": REDIRECT, "created": time.time()})
    SCRATCH.mkdir(parents=True, exist_ok=True)
    PKCE.write_text(json.dumps(entries[-10:], indent=1))
    os.chmod(PKCE, 0o600)
    print(url)
    print(f"state={state}")
    return 0


def mcp(access: str, tool: str, args: dict | None = None) -> dict:
    payload = {"jsonrpc": "2.0", "id": "1", "method": "tools/call",
               "params": {"name": tool, "arguments": args or {}}}
    h = {"Authorization": f"Bearer {access}", "Accept": "application/json, text/event-stream"}
    r = httpx.post(MCP, headers=h, json=payload, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"{tool}: HTTP {r.status_code} {r.text[:120]}")
    body = r.json()
    if body.get("error"):
        raise RuntimeError(f"{tool}: {body['error']}")
    res = body.get("result") or {}
    text = "".join(c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text")
    if res.get("isError"):
        raise RuntimeError(f"{tool}: {text[:200]}")
    return json.loads(text)


def parse_code(blob: str) -> dict[str, str]:
    blob = (blob or "").strip().strip('"').strip("'")
    if not blob:
        return {}
    if blob.startswith("http"):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(blob).query)
        return {k: v[0] for k, v in q.items()}
    return {"code": blob}


def cmd_redeem(a) -> int:
    env = Path(a.env)
    raw = json.loads(PKCE.read_text())
    entries = raw if isinstance(raw, list) else [raw]
    blob = a.callback_url
    if blob == "-":
        blob = sys.stdin.read()
    if not blob and CODE.exists():
        blob = CODE.read_text()
    got = parse_code(blob)
    if not got.get("code"):
        sys.exit("No hay `code`: pase --callback-url con la URL completa del callback.")
    pk = (next((e for e in entries if e.get("state") == got.get("state")), None)
          if got.get("state") else entries[-1])
    if pk is None:
        sys.exit(f"State {got['state']} desconocido; guardados: {[e['state'] for e in entries]}")

    r = httpx.post(TOKEN, data={"grant_type": "authorization_code", "client_id": pk["client_id"],
                                "code": got["code"], "code_verifier": pk["verifier"],
                                "redirect_uri": pk["redirect_uri"]}, timeout=60)
    if r.status_code != 200:
        sys.exit(f"Canje falló: HTTP {r.status_code} {r.text[:200]}")
    tok = r.json()
    access, refresh = tok.get("access_token"), tok.get("refresh_token")
    if not (access and refresh):
        sys.exit("La respuesta no trae access_token y refresh_token.")
    print(f"canje OK · expires_in={tok.get('expires_in')} · scope={tok.get('scope')}")

    bal = mcp(access, "getPointsBalance")
    print(f"MCP OK · earned_balance={bal.get('earned_balance')} NC · "
          f"lifetime_earnings={bal.get('lifetime_earnings')} NC")
    mcp(access, "getRecognitionFeed",
        {"current_user_only": ["received"], "recognition_types": ["peer"], "limit": 1})
    mcp(access, "getMyRedemptions", {"limit": 1})
    print("MCP OK · reconocimientos y canjes accesibles con los scopes dados")

    write_env_value(env, CLIENT_ENV, pk["client_id"])
    write_env_value(env, REFRESH_ENV, refresh)
    print(f"guardado en {env}: {CLIENT_ENV} y {REFRESH_ENV} (permisos 600)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("cmd", choices=["url", "redeem"])
    ap.add_argument("--client-id", default="", help="si se omite: el guardado, o uno nuevo por DCR")
    ap.add_argument("--callback-url", default="", help="URL del callback, o '-' para leerla de stdin")
    ap.add_argument("--env", default=str(REPO / ".env"), help="ruta del .env a escribir")
    a = ap.parse_args()
    return cmd_url(a) if a.cmd == "url" else cmd_redeem(a)


if __name__ == "__main__":
    sys.exit(main())
