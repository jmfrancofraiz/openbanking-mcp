"""Token OAuth de Bonusly: refresco perezoso y persistencia del refresh token rotado.

Bonusly cerró `https://bonus.ly/mcp` a los PAT (nov-2026): el MCP exige ahora OAuth 2.0 con
authorization code + PKCE S256 (y DCR para el cliente). El alta es un consentimiento único de
navegador, con `scripts/bonusly_oauth_login.py`; aquí sólo se mantiene vivo el acceso.

- `BONUSLY_OAUTH_CLIENT_ID` y `BONUSLY_OAUTH_REFRESH_TOKEN` viven en `.env`.
- El refresh token ROTA en cada uso: si no se persiste el nuevo, el `.env` queda con uno muerto
  y la sincronización falla para siempre. Por eso se escribe de inmediato, de forma atómica
  (temporal en el mismo directorio + `os.replace`) y con permisos 600.
- El refresco es perezoso: una sola petición al principio de cada proceso (y otra si el servidor
  rechaza el token con 401, vía `invalidate()`), nunca una por llamada.
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import httpx

CLIENT_ENV = "BONUSLY_OAUTH_CLIENT_ID"
REFRESH_ENV = "BONUSLY_OAUTH_REFRESH_TOKEN"
DEFAULT_TOKEN_URL = "https://bonus.ly/oauth/token"
DEFAULT_REDIRECT = "http://127.0.0.1:8765/callback"


class OAuthError(RuntimeError):
    """No se pudo obtener un access token: hay que repetir el alta (`bonusly_oauth_login.py`)."""


def write_env_value(path: Path, key: str, value: str, *, mode: int = 0o600) -> None:
    """Sustituye (o añade) `KEY=valor` en un `.env` conservando el resto, de forma atómica."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    prefix = f"{key}="
    out = [ln for ln in lines if not ln.lstrip().removeprefix("export ").startswith(prefix)]
    out.append(f"{key}={value}")
    directory = path.parent if str(path.parent) else Path(".")
    fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class TokenProvider:
    """Devuelve un access token válido a partir del refresh token guardado en `.env`."""

    def __init__(
        self,
        client_id: str,
        refresh_token: str,
        token_url: str = DEFAULT_TOKEN_URL,
        env_file: Path | None = None,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
        skew: int = 60,
    ) -> None:
        if not client_id or not refresh_token:
            raise OAuthError(
                "Falta BONUSLY_OAUTH_CLIENT_ID o BONUSLY_OAUTH_REFRESH_TOKEN en .env: "
                "repita el alta con scripts/bonusly_oauth_login.py."
            )
        self._client_id = client_id
        self._refresh_token = refresh_token
        self._token_url = token_url
        self._env_file = Path(env_file) if env_file else None
        self._transport = transport
        self._clock = clock
        self._skew = skew
        self._access: str | None = None
        self._expires_at = 0.0

    @property
    def refresh_token(self) -> str:
        return self._refresh_token

    def invalidate(self) -> None:
        """Olvida el access token en caché: la próxima llamada refresca."""
        self._access = None
        self._expires_at = 0.0

    def access_token(self) -> str:
        if self._access and self._clock() < self._expires_at - self._skew:
            return self._access
        return self._refresh()

    def _refresh(self) -> str:
        data = {
            "grant_type": "refresh_token",
            "client_id": self._client_id,
            "refresh_token": self._refresh_token,
        }
        try:
            with httpx.Client(timeout=60, transport=self._transport) as c:
                r = c.post(self._token_url, data=data)
        except httpx.HTTPError as exc:  # red caída: no es un problema de credenciales
            raise OAuthError(f"Bonusly: no se pudo refrescar el token ({exc}).") from exc
        if r.status_code != 200:
            raise OAuthError(
                f"Bonusly OAuth {r.status_code} al refrescar: el refresh token ya no vale "
                f"({r.text[:120]}). Repita scripts/bonusly_oauth_login.py."
            )
        body = r.json()
        token = body.get("access_token")
        if not token:
            raise OAuthError("Bonusly OAuth: la respuesta de refresco no trae access_token.")
        rotated = body.get("refresh_token")
        if rotated and rotated != self._refresh_token:
            self._refresh_token = rotated
            if self._env_file is not None:
                write_env_value(self._env_file, REFRESH_ENV, rotated)
        expires_in = int(body.get("expires_in") or 3600)
        self._access = token
        self._expires_at = self._clock() + expires_in
        return token
