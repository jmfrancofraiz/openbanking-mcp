"""Panel web de finmcp (solo lectura).

Tercera superficie de finmcp, junto al CLI y al servidor MCP: un servicio HTTP
independiente (puerto 8790 por defecto; el MCP --http usa el 8000) que sirve una
interfaz estática y una API JSON sobre `analytics/dashboard.py`.

- La base de datos se abre en modo de solo lectura (`mode=ro`): el panel no escribe.
  Única acción: `POST /api/sync` (tirar hacia abajo en la interfaz) lanza `finmcp sync`
  en un proceso aparte (ver `web/syncjob.py`); el panel solo lo vigila.
- Solo atiende clientes de la red local (loopback y rangos privados) y, si escucha
  en su IP, de Tailscale. Nunca escucha en 0.0.0.0.
- Con PIN (`finmcp web-pin`): la API exige una cookie de sesión obtenida en
  POST /api/login; la interfaz estática es pública (no contiene datos).
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import secrets
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from finmcp.analytics import dashboard, ledger
from finmcp.db import models
from finmcp.web import auth, syncjob

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_PORT = 8790

LAN_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)
TAILSCALE_NETWORK = ipaddress.ip_network("100.64.0.0/10")

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ]
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

KIND_PARAM = {"gastos": dashboard.EXPENSE, "ingresos": dashboard.INCOME,
              "no-computables": dashboard.NEUTRAL}
# Pestaña Diario: tipo de movimiento y marca.
TX_TYPE_PARAM = {"cargos": "debit", "abonos": "credit"}
MARK_PARAM = {"sin-categoria": "uncategorized", "recibos": "bill",
              "no-computables": "neutral", "manuales": "manual"}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")
_CURSOR_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)\|([A-Za-z0-9_.:\-]{1,128})$")
LEDGER_LIMIT = 100
LEDGER_MAX_LIMIT = 200


# --- red local ---------------------------------------------------------------


def client_allowed(host: str, networks) -> bool:
    """¿Pertenece la IP del cliente a alguna de las redes permitidas?"""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in net for net in networks)


def validate_hosts(hosts: list[str]) -> tuple[list[str], bool]:
    """Valida las IP de escucha. Devuelve (ips, escucha_en_tailscale).

    Solo se admiten loopback, IP privadas de la red local o la IP de Tailscale;
    nunca 0.0.0.0 / :: ni direcciones públicas.
    """
    if not hosts:
        raise ValueError("indique al menos una IP con --host.")
    out, tailscale = [], False
    for raw in hosts:
        host = raw.strip()
        if host == "localhost":
            host = "127.0.0.1"
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            raise ValueError(
                f"«{raw}» no es una IP; indique la de la red local (p. ej. 192.168.1.10)."
            ) from None
        if ip.is_unspecified:
            raise ValueError(
                f"no se permite escuchar en {raw} (todas las interfaces); "
                "indique la IP de la red local."
            )
        if ip in TAILSCALE_NETWORK:
            tailscale = True
        elif not client_allowed(host, LAN_NETWORKS):
            raise ValueError(f"{raw} no es una IP de la red local ni de Tailscale.")
        out.append(str(ip))
    return out, tailscale


class LanOnlyMiddleware:
    """Rechaza (403) cualquier cliente cuya IP no esté en las redes permitidas."""

    def __init__(self, app, networks) -> None:
        self.app = app
        self.networks = networks

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            if not client or not client_allowed(client[0], self.networks):
                response = PlainTextResponse("Acceso restringido a la red local.", 403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    """Cabeceras de seguridad en toda respuesta; la API nunca se guarda en caché."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_api = scope["path"].startswith("/api/")

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
                headers["Cache-Control"] = "no-store" if is_api else "no-cache"
            await send(message)

        await self.app(scope, receive, send_with_headers)


class PinMiddleware:
    """Con PIN, la API (salvo login/logout) exige una cookie de sesión válida; si no, 401."""

    OPEN = frozenset({"/api/login", "/api/logout"})

    def __init__(self, app, sessions: auth.Sessions) -> None:
        self.app = app
        self.sessions = sessions

    async def __call__(self, scope, receive, send) -> None:
        path = scope.get("path", "")
        if scope["type"] == "http" and path.startswith("/api/") and path not in self.OPEN:
            token = Request(scope).cookies.get(auth.SESSION_COOKIE)
            if not self.sessions.valid(token):
                response = JSONResponse({"error": "Introduzca el PIN.", "auth": "pin"}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


# --- base de datos -----------------------------------------------------------


def readonly_session_factory(db_path: Path) -> sessionmaker:
    """Sesiones sobre la BD de finmcp abierta en modo de solo lectura (SQLite `mode=ro`)."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"No existe la base de datos {db_path}; ejecute antes `finmcp sync`.")
    engine = create_engine(
        f"sqlite:///file:{db_path.resolve().as_posix()}?mode=ro&uri=true",
        connect_args={"check_same_thread": False},
        future=True,
    )
    return sessionmaker(bind=engine, future=True)


# --- parámetros --------------------------------------------------------------


class ParamError(ValueError):
    """Parámetro de consulta inválido (respuesta 400)."""


def _date_param(request: Request, name: str) -> date:
    value = request.query_params.get(name, "")
    if not _DATE_RE.match(value):
        raise ParamError(f"«{name}» debe ser una fecha AAAA-MM-DD.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ParamError(f"«{name}» no es una fecha válida.") from None


def _range_params(request: Request) -> tuple[date, date]:
    start, end = _date_param(request, "from"), _date_param(request, "to")
    if start > end:
        raise ParamError("«from» no puede ser posterior a «to».")
    return start, end


def _month_param(request: Request, name: str) -> str:
    value = request.query_params.get(name, "")
    if not _MONTH_RE.match(value):
        raise ParamError(f"«{name}» debe ser un mes AAAA-MM.")
    try:
        datetime.strptime(value, "%Y-%m")
    except ValueError:
        raise ParamError(f"«{name}» no es un mes válido.") from None
    return value


def _kind_param(request: Request) -> str:
    value = request.query_params.get("type", "")
    if value not in KIND_PARAM:
        raise ParamError("«type» debe ser «gastos», «ingresos» o «no-computables».")
    return KIND_PARAM[value]


def _day_param(request: Request) -> int | None:
    value = request.query_params.get("day")
    if value is None:
        return None
    if not value.isdigit() or not 1 <= int(value) <= 31:
        raise ParamError("«day» debe ser un día entre 1 y 31.")
    return int(value)


def _optional_date(request: Request, name: str) -> date | None:
    return _date_param(request, name) if request.query_params.get(name) else None


def _choice_param(request: Request, name: str, choices: dict[str, str]) -> str | None:
    value = request.query_params.get(name)
    if not value:
        return None
    if value not in choices:
        raise ParamError(f"«{name}» debe ser {' o '.join(f'«{c}»' for c in choices)}.")
    return choices[value]


def _text_param(request: Request, name: str, max_len: int = 100) -> str | None:
    value = (request.query_params.get(name) or "").strip()
    if len(value) > max_len:
        raise ParamError(f"«{name}» admite como mucho {max_len} caracteres.")
    return value or None


def _cursor_param(request: Request) -> tuple[datetime, str] | None:
    value = request.query_params.get("cursor")
    if not value:
        return None
    m = _CURSOR_RE.match(value)
    if not m:
        raise ParamError("«cursor» no es válido.")
    try:
        return datetime.fromisoformat(m.group(1)), m.group(2)
    except ValueError:
        raise ParamError("«cursor» no es válido.") from None


def _limit_param(request: Request) -> int:
    value = request.query_params.get("limit")
    if value is None:
        return LEDGER_LIMIT
    if not value.isdigit() or not 1 <= int(value) <= LEDGER_MAX_LIMIT:
        raise ParamError(f"«limit» debe estar entre 1 y {LEDGER_MAX_LIMIT}.")
    return int(value)


# --- aplicación --------------------------------------------------------------


def create_app(
    session_factory: Callable,
    *,
    today: Callable[[], date] = date.today,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    allow_tailscale: bool = False,
    pin_hash: str = "",
    session_secret: bytes | None = None,
    throttle: auth.Throttle | None = None,
    sync_job: syncjob.SyncJob | None = None,
) -> Starlette:
    """Crea la aplicación del panel sobre una factoría de sesiones (de solo lectura).

    Con `pin_hash`, la API exige sesión (POST /api/login con el PIN). Sin
    `session_secret`, las sesiones duran lo que dure el proceso. Con `sync_job`, la
    interfaz puede lanzar una sincronización (POST /api/sync); sin él, no.
    """
    networks = LAN_NETWORKS + ((TAILSCALE_NETWORK,) if allow_tailscale else ())
    sessions = auth.Sessions(session_secret or secrets.token_bytes(32), pin_hash) if pin_hash else None
    throttle = throttle or auth.Throttle()
    login_lock = asyncio.Lock()

    def query(fn):
        with session_factory() as session:
            return fn(session)

    def api_meta(request: Request) -> JSONResponse:
        return JSONResponse({**query(lambda s: dashboard.meta(s, today())), "pin": bool(pin_hash),
                             "sync": sync_job is not None})

    async def api_login(request: Request) -> Response:
        if not sessions:
            return JSONResponse({"error": "El panel no tiene PIN."}, status_code=404)
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            return JSONResponse({"error": "Se esperaba JSON."}, status_code=415)
        try:
            body = await request.json()
        except ValueError:
            body = None
        pin = body.get("pin") if isinstance(body, dict) else None
        async with login_lock:  # intentos de uno en uno: el freno no se puede esquivar en paralelo
            wait = throttle.retry_after()
            if wait:
                return JSONResponse({"error": "Demasiados intentos fallidos.", "retry_after": wait},
                                    status_code=429, headers={"Retry-After": str(wait)})
            if not await run_in_threadpool(auth.verify_pin, pin, pin_hash):
                throttle.failure()
                return JSONResponse({"error": "PIN incorrecto.", "retry_after": throttle.retry_after()},
                                    status_code=401)
            throttle.success()
        response = Response(status_code=204)
        response.set_cookie(auth.SESSION_COOKIE, sessions.issue(), max_age=auth.SESSION_TTL,
                            path="/", httponly=True, samesite="strict")
        return response

    async def api_logout(request: Request) -> Response:
        response = Response(status_code=204)
        response.delete_cookie(auth.SESSION_COOKIE, path="/", httponly=True, samesite="strict")
        return response

    async def api_sync_start(request: Request) -> Response:
        """Lanza una sincronización (si no hay otra en marcha ni se está en el freno)."""
        if sync_job is None:
            return JSONResponse({"error": "La sincronización desde el panel no está disponible."},
                                status_code=404)
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            return JSONResponse({"error": "Se esperaba JSON."}, status_code=415)
        snapshot, started = await sync_job.start()
        return JSONResponse({**snapshot, "started": started}, status_code=202 if started else 200)

    async def api_sync_status(request: Request) -> Response:
        if sync_job is None:
            return JSONResponse({"error": "La sincronización desde el panel no está disponible."},
                                status_code=404)
        return JSONResponse(await sync_job.snapshot())

    def api_monthly(request: Request) -> JSONResponse:
        day = _day_param(request)
        return JSONResponse({"months": query(lambda s: dashboard.monthly(s, today(), day=day))})

    def api_breakdown(request: Request) -> JSONResponse:
        start, end = _range_params(request)
        return JSONResponse(query(lambda s: dashboard.breakdown(s, start, end)))

    def api_series(request: Request) -> JSONResponse:
        start, end = _month_param(request, "from"), _month_param(request, "to")
        if start > end:
            raise ParamError("«from» no puede ser posterior a «to».")
        return JSONResponse(query(lambda s: dashboard.series(s, start, end)))

    def api_references(request: Request) -> JSONResponse:
        month = _month_param(request, "to")
        return JSONResponse(query(lambda s: dashboard.references(s, month)))

    def api_transactions(request: Request) -> JSONResponse:
        start, end = _range_params(request)
        kind = _kind_param(request)
        group = request.query_params.get("group")
        category = request.query_params.get("category")
        if group is not None and category is not None:
            raise ParamError("indique «group» o «category», no ambos.")
        rows = query(lambda s: dashboard.transactions(s, start, end, kind, group, category))
        return JSONResponse({"from": start.isoformat(), "to": end.isoformat(),
                             "transactions": rows})

    # --- pestaña Diario: el día a día, sin agregaciones -------------------------

    def api_accounts(request: Request) -> JSONResponse:
        moment = now()
        rows = query(lambda s: ledger.accounts(s, moment))
        return JSONResponse({"now": ledger.iso_utc(moment), "accounts": rows,
                             "total": ledger.accounts_total(rows)})

    def api_tags(request: Request) -> JSONResponse:
        return JSONResponse({"tags": query(ledger.tags_in_use)})

    def api_ledger(request: Request) -> JSONResponse:
        account_raw = request.query_params.get("account") or None
        accounts: list[str] | None = None
        if account_raw is not None:
            accounts = list(dict.fromkeys(p.strip() for p in account_raw.split(",") if p.strip()))
            if not accounts or any(not _ID_RE.match(a) for a in accounts):
                raise ParamError("«account» no es válido.")
        start, end = _optional_date(request, "from"), _optional_date(request, "to")
        if start and end and start > end:
            raise ParamError("«from» no puede ser posterior a «to».")
        params = {
            "account": accounts,
            "kind": _choice_param(request, "type", TX_TYPE_PARAM),
            "mark": _choice_param(request, "mark", MARK_PARAM),
            "tag": _text_param(request, "tag", 64),
            "start": start,
            "end": end,
            "text": _text_param(request, "q"),
            "cursor": _cursor_param(request),
            "limit": _limit_param(request),
        }

        def run(session):
            if accounts is not None and any(session.get(models.Account, a) is None for a in accounts):
                raise ParamError("«account»: cuenta desconocida.")
            return ledger.movements(session, **params)

        return JSONResponse(query(run))

    def api_movement(request: Request) -> JSONResponse:
        tx_id = request.query_params.get("id", "")
        if not _ID_RE.match(tx_id):
            raise ParamError("«id» no es válido.")
        row = query(lambda s: ledger.movement(s, tx_id))
        if row is None:
            return JSONResponse({"error": "Movimiento no encontrado."}, status_code=404)
        return JSONResponse(row)

    def index(request: Request) -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html; charset=utf-8")

    async def bad_request(request: Request, exc: ParamError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=400)

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/meta", api_meta),
            Route("/api/login", api_login, methods=["POST"]),
            Route("/api/logout", api_logout, methods=["POST"]),
            Route("/api/sync", api_sync_start, methods=["POST"]),
            Route("/api/sync", api_sync_status, methods=["GET"]),
            Route("/api/monthly", api_monthly),
            Route("/api/breakdown", api_breakdown),
            Route("/api/series", api_series),
            Route("/api/references", api_references),
            Route("/api/transactions", api_transactions),
            Route("/api/accounts", api_accounts),
            Route("/api/tags", api_tags),
            Route("/api/ledger", api_ledger),
            Route("/api/movement", api_movement),
            Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
        ],
        middleware=[
            Middleware(SecurityHeadersMiddleware),
            Middleware(LanOnlyMiddleware, networks=networks),
            *([Middleware(PinMiddleware, sessions=sessions)] if sessions else []),
            Middleware(GZipMiddleware, minimum_size=1024),
        ],
        exception_handlers={ParamError: bad_request},
    )


def run(hosts: list[str], port: int = DEFAULT_PORT) -> None:
    """Arranca el panel con uvicorn en una o varias IP (p. ej. LAN y Tailscale)."""
    import socket

    import uvicorn

    from finmcp.config import settings

    hosts, allow_tailscale = validate_hosts(hosts)
    pin_hash = settings.finmcp_web_pin_hash
    session_factory = readonly_session_factory(settings.db_path)
    app = create_app(
        session_factory,
        allow_tailscale=allow_tailscale,
        pin_hash=pin_hash,
        session_secret=auth.load_secret(settings.data_dir / "web_session.key") if pin_hash else None,
        # El hijo arranca en la raíz del repo: allí está el `.env` con las credenciales del banco.
        sync_job=syncjob.SyncJob(session_factory, cwd=settings.data_dir.parent,
                                 cooldown=settings.finmcp_web_sync_cooldown),
    )
    sockets = []
    for host in hosts:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sockets.append(sock)
        shown = f"[{host}]" if ":" in host else host
        print(f"Panel finmcp (solo lectura{', con PIN' if pin_hash else ''}; "
              f"tirar hacia abajo sincroniza) en http://{shown}:{port}/", flush=True)
    # proxy_headers=False: la IP del cliente es siempre la del socket (filtro de red local).
    config = uvicorn.Config(app, proxy_headers=False, server_header=False, log_level="info")
    uvicorn.Server(config).run(sockets=sockets)
