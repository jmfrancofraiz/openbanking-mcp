"""Receptor local del callback OAuth de Bonusly (opcional, para el alta).

Escucha en 127.0.0.1:<puerto>/callback, guarda la query recibida en --code-file y muestra una
página de cortesía. No canjea nada: eso lo hace `scripts/bonusly_oauth_login.py redeem`, que
lee ese fichero si no se le pasa la URL.

Sólo sirve si el navegador corre en la misma máquina que este proceso (el `redirect_uri` es de
loopback). Si el navegador está en otro equipo, no hace falta: muestre la URL del callback a
`redeem --callback-url`.

  python scripts/bonusly_callback_listener.py [--port 8765] [--code-file RUTA]
"""

from __future__ import annotations

import argparse
import http.server
import os
import socketserver
import sys
import urllib.parse
from pathlib import Path

DEFAULT_CODE = Path(os.path.expanduser("~/.hermes/cache/scratch/bonusly_oauth_code.txt"))

p = argparse.ArgumentParser(description=__doc__,
                            formatter_class=argparse.RawTextHelpFormatter)
p.add_argument("--port", type=int, default=8765)
p.add_argument("--code-file", default=str(DEFAULT_CODE))
a = p.parse_args()
out = Path(a.code_file)
out.parent.mkdir(parents=True, exist_ok=True)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path.rstrip("/") in ("/callback", "") and ("code" in q or "error" in q):
            out.write_text(urllib.parse.urlunparse(u))
            body = ("<h2>Listo. Puede cerrar esta ventana.</h2>"
                    "<p>finmcp ya tiene el código.</p>").encode()
        else:
            body = b"<h2>Receptor activo.</h2>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


with socketserver.TCPServer(("127.0.0.1", a.port), Handler) as httpd:
    print(f"escuchando en 127.0.0.1:{a.port} · código en {out}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
