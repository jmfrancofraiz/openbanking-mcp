"""Panel web de finmcp (solo lectura).

Tercera superficie de finmcp, junto al CLI y al servidor MCP: un servicio HTTP
independiente (puerto 8790 por defecto; el MCP --http usa el 8000) que sirve una
interfaz estática y una API JSON sobre `analytics/dashboard.py`.

- La base de datos se abre en modo de solo lectura (`mode=ro`): no hay escrituras.
- Solo atiende clientes de la red local (loopback y rangos privados) y, si escucha
  en su IP, de Tailscale. Nunca escucha en 0.0.0.0.
- Sin autenticación: el acceso queda limitado a la red de casa.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.applications import Starlette
from starlette.datastructures import MutableHeaders
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from finmcp.analytics import dashboard

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

KIND_PARAM = {"gastos": dashboard.EXPENSE, "ingresos": dashboard.INCOME}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


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
        raise ParamError("«type» debe ser «gastos» o «ingresos».")
    return KIND_PARAM[value]


def _day_param(request: Request) -> int | None:
    value = request.query_params.get("day")
    if value is None:
        return None
    if not value.isdigit() or not 1 <= int(value) <= 31:
        raise ParamError("«day» debe ser un día entre 1 y 31.")
    return int(value)


# --- aplicación --------------------------------------------------------------


def create_app(
    session_factory: Callable,
    *,
    today: Callable[[], date] = date.today,
    allow_tailscale: bool = False,
) -> Starlette:
    """Crea la aplicación del panel sobre una factoría de sesiones (de solo lectura)."""
    networks = LAN_NETWORKS + ((TAILSCALE_NETWORK,) if allow_tailscale else ())

    def query(fn):
        with session_factory() as session:
            return fn(session)

    def api_meta(request: Request) -> JSONResponse:
        return JSONResponse(query(lambda s: dashboard.meta(s, today())))

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

    def index(request: Request) -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html; charset=utf-8")

    async def bad_request(request: Request, exc: ParamError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=400)

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/meta", api_meta),
            Route("/api/monthly", api_monthly),
            Route("/api/breakdown", api_breakdown),
            Route("/api/series", api_series),
            Route("/api/transactions", api_transactions),
            Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
        ],
        middleware=[
            Middleware(SecurityHeadersMiddleware),
            Middleware(LanOnlyMiddleware, networks=networks),
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
    app = create_app(readonly_session_factory(settings.db_path), allow_tailscale=allow_tailscale)
    sockets = []
    for host in hosts:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sockets.append(sock)
        shown = f"[{host}]" if ":" in host else host
        print(f"Panel finmcp (solo lectura) en http://{shown}:{port}/", flush=True)
    # proxy_headers=False: la IP del cliente es siempre la del socket (filtro de red local).
    config = uvicorn.Config(app, proxy_headers=False, server_header=False, log_level="info")
    uvicorn.Server(config).run(sockets=sockets)
