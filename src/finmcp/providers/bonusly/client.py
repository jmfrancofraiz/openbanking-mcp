"""Cliente de SOLO LECTURA de Bonusly (herramientas JSON-RPC de https://bonus.ly/mcp)."""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterator
from datetime import date, timedelta

import httpx

from finmcp.config import BankConfig
from finmcp.providers.bonusly import mapper
from finmcp.providers.bonusly.oauth import TokenProvider
from finmcp.providers.types import Account, Balance, Transaction

MCP_URL = "https://bonus.ly/mcp"
WINDOW_DAYS = 365  # tramos de `getRecognitionReceived` (el histórico entero, por partes)
DEFAULT_LOOKBACK_DAYS = 90  # sin --from, como el valor por defecto de la API
MAX_PAGES = 500  # cortafuegos ante un cursor que no termine


class BonuslyError(RuntimeError):
    pass


def _windows(start: date, end: date, days: int) -> Iterator[tuple[date, date]]:
    """Tramos contiguos, sin solaparse, de `days` días como máximo, de `start` a `end`."""
    cur = start
    while cur <= end:
        w_end = min(cur + timedelta(days=days - 1), end)
        yield cur, w_end
        cur = w_end + timedelta(days=1)


class BonuslyClient:
    """Implementa `BankDataProvider` sobre Bonusly. Nunca da reconocimientos ni canjea.

    - Cuenta: el saldo canjeable (`earned_balance`), en euros a `eur_per_point` por punto.
    - Ingresos: reconocimientos recibidos. Se unen dos fuentes, porque
      `getRecognitionReceived` omite las felicitaciones a grupos grandes y
      `getRecognitionFeed` no trae los añadidos (add-ons); juntas cuadran con
      `lifetime_earnings` (verificado en oct-2026).
    - Gastos: canjes de recompensas (`getMyRedemptions`).
    """

    def __init__(
        self,
        bank: BankConfig,
        token: str | None = None,
        eur_per_point: float | None = None,
        transport: httpx.BaseTransport | None = None,
        token_provider: TokenProvider | None = None,
    ) -> None:
        if eur_per_point is None or (token is None and token_provider is None):
            from finmcp import config

            if eur_per_point is None:
                eur_per_point = config.settings.bonusly_eur_per_point
            if token is None and token_provider is None:
                token = config.settings.bonusly_api_token
                if config.settings.bonusly_oauth_refresh_token:
                    token_provider = TokenProvider(
                        client_id=config.settings.bonusly_oauth_client_id,
                        refresh_token=config.settings.bonusly_oauth_refresh_token,
                        token_url=config.settings.bonusly_oauth_token_url,
                        env_file=getattr(config.settings, "_env_file_path", None),
                    )
        if token_provider is None and not token:
            raise BonuslyError(
                "Falta BONUSLY_OAUTH_REFRESH_TOKEN en .env: repita el alta con "
                "scripts/bonusly_oauth_login.py."
            )
        self._bank = bank
        self._token = token
        self._token_provider = token_provider
        self._eur = eur_per_point
        self._transport = transport
        self._ids = itertools.count(1)
        self._balance: dict | None = None

    # --- transporte -------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        token = self._token_provider.access_token() if self._token_provider else self._token
        return {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}

    def _post(self, payload: dict) -> httpx.Response:
        """Una llamada al MCP; ante un 401 con OAuth, refresca el token y reintenta una vez."""
        with httpx.Client(timeout=60, transport=self._transport, headers=self._headers()) as c:
            r = c.post(MCP_URL, json=payload)
            if r.status_code == 401 and self._token_provider is not None:
                self._token_provider.invalidate()
                with httpx.Client(timeout=60, transport=self._transport,
                                  headers=self._headers()) as c2:
                    r = c2.post(MCP_URL, json=payload)
        return r

    def _call(self, tool: str, arguments: dict | None = None) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": str(next(self._ids)),
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments or {}},
        }
        r = self._post(payload)
        if r.status_code in (401, 403):
            hint = (
                "OAuth: repita el alta con scripts/bonusly_oauth_login.py."
                if self._token_provider is not None
                else "token caducado, revocado o sin el scope necesario."
            )
            raise BonuslyError(f"Bonusly {r.status_code} en {tool}: {hint}")
        r.raise_for_status()
        body = r.json()
        if body.get("error"):
            err = body["error"]
            raise BonuslyError(f"Bonusly {tool}: {err.get('message', err) if isinstance(err, dict) else err}")
        result = body.get("result") or {}
        text = "".join(
            b.get("text", "") for b in result.get("content") or [] if b.get("type") == "text"
        )
        if result.get("isError"):
            raise BonuslyError(f"Bonusly {tool}: {text[:300]}")
        return json.loads(text)

    def _pages(self, tool: str, arguments: dict, key: str) -> Iterator[list[dict]]:
        """Páginas de una herramienta con cursor (`next_cursor`), con los mismos argumentos."""
        cursor = None
        for _ in range(MAX_PAGES):
            args = dict(arguments)
            if cursor:
                args["cursor"] = cursor
            page = self._call(tool, args)
            yield page.get(key) or []
            cursor = page.get("next_cursor")
            if not cursor:
                return
        raise BonuslyError(f"Bonusly {tool}: más de {MAX_PAGES} páginas; abortado.")

    def _newest_first(self, tool: str, arguments: dict, key: str, start: date) -> list[dict]:
        """Filas de una lista ordenada de más nueva a más antigua, hasta pasar de `start`."""
        rows: list[dict] = []
        for page in self._pages(tool, arguments, key):
            rows.extend(page)
            if page and mapper.booked_date(page[-1]["created_at"]).date() < start:
                break
        return rows

    # --- BankDataProvider --------------------------------------------------------------

    def _points(self) -> dict:
        if self._balance is None:
            self._balance = self._call("getPointsBalance")
        return self._balance

    def get_accounts(self) -> list[Account]:
        return [mapper.to_account(self._points(), bank_id=self._bank.id, eur_per_point=self._eur)]

    def get_balance(self, account_id: str) -> Balance:
        return mapper.to_balance(self._points(), eur_per_point=self._eur)

    def get_transactions(
        self,
        account_id: str,
        from_date: str | None = None,
        to_date: str | None = None,
        strategy: str | None = None,
    ) -> list[Transaction]:
        """Movimientos con fecha (día local) entre `from_date` y `to_date`, ambos incluidos.

        `strategy` es de Enable Banking y aquí no aplica.
        """
        end = date.fromisoformat(to_date) if to_date else date.today()
        start = (
            date.fromisoformat(from_date)
            if from_date
            else end - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        )
        kw = {"eur_per_point": self._eur, "tz": mapper.DEFAULT_TZ}
        txs: dict[str, Transaction] = {}

        def keep(tx: Transaction | None) -> None:
            if tx is not None and start <= tx.booked_at.date() <= end:
                txs.setdefault(tx.provider_id, tx)

        for w_start, w_end in _windows(start, end, WINDOW_DAYS):
            args = {"start_date": w_start.isoformat(), "end_date": w_end.isoformat(), "limit": 50}
            for page in self._pages("getRecognitionReceived", args, "recognitions"):
                for r in page:
                    keep(mapper.recognition_to_tx(r, **kw))
        feed_args = {"current_user_only": ["received"], "recognition_types": ["peer"], "limit": 100}
        for r in self._newest_first("getRecognitionFeed", feed_args, "recognitions", start):
            keep(mapper.recognition_to_tx(r, **kw))
        for d in self._newest_first("getMyRedemptions", {"limit": 100}, "redemptions", start):
            for tx in mapper.redemption_to_txs(d, **kw):
                keep(tx)
        return list(txs.values())
