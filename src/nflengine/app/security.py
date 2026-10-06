"""The control room's safety rules at the HTTP layer (documentation/control-room/README.md §5).

- Every request must carry `Host: 127.0.0.1:<port>` or `localhost:<port>`. The server binds
  127.0.0.1 only; the Host check also stops DNS-rebinding pages on other sites from talking to
  it through a hostname they control.
- Every state-changing request (anything but GET / HEAD / OPTIONS) needs a same-origin
  `Origin` header and the per-launch token in `X-CR-Token`. The page reads the token from
  `GET /api/session`, which other sites can't read (no CORS headers are ever sent).
- Responses carry headers that stop framing (clickjacking a Run button) and sniffing.

Written as plain ASGI middleware (no BaseHTTPMiddleware) so it also covers streaming
responses (CR02's server-sent events).
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Iterable
from typing import Any

from nflengine.app.jsonsafe import dumps
from nflengine.ops.summary import scrub

log = logging.getLogger("nflengine.app")

TOKEN_HEADER = "x-cr-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
VITE_DEV_PORT = 5173
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; object-src 'none'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"content-security-policy", CSP.encode()),
]


def allowed_hosts(port: int) -> frozenset[str]:
    return frozenset({f"127.0.0.1:{port}", f"localhost:{port}"})


def allowed_origins(port: int, dev: bool = False) -> frozenset[str]:
    ports = [port, VITE_DEV_PORT] if dev else [port]
    return frozenset(f"http://{h}:{p}" for p in ports for h in ("127.0.0.1", "localhost"))


class LocalOnlyMiddleware:
    def __init__(
        self,
        app: Any,
        *,
        port: int,
        token: str,
        dev: bool = False,
        extra_hosts: Iterable[str] = (),
    ) -> None:
        self.app = app
        self.token = token
        self.hosts = allowed_hosts(port) | frozenset(extra_hosts)
        self.origins = allowed_origins(port, dev)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        kind = scope["type"]
        if kind == "lifespan":
            await self.app(scope, receive, send)
            return
        if kind != "http":  # no websockets: CR02 streams with server-sent events
            await send({"type": "websocket.close", "code": 1008})
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        if headers.get("host", "") not in self.hosts:
            await _reject(send, 400, "bad_host", "This app only answers on 127.0.0.1.")
            return
        if scope["method"] not in SAFE_METHODS:
            if headers.get("origin") not in self.origins:
                await _reject(send, 403, "bad_origin", "Requests must come from the app itself.")
                return
            if not hmac.compare_digest(headers.get(TOKEN_HEADER, ""), self.token):
                await _reject(send, 403, "bad_token", "Missing or wrong launch token.")
                return
        api = scope.get("path", "").startswith("/api/")
        started = False

        async def send_with_headers(message: dict) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                extra = list(SECURITY_HEADERS)
                if api:
                    extra.append((b"cache-control", b"no-store"))
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        except Exception as e:
            # Handled here, inside the boundary: the 500 keeps the security headers, the log
            # line is scrubbed (an exception message could carry a credential), and nothing
            # reaches Starlette's / uvicorn's raw traceback printers (Sol review, CR00).
            log.error("control room request failed: %s", scrub(f"{type(e).__name__}: {e}", 300))
            if not started:
                await _reject(
                    send, 500, "server_error", f"{type(e).__name__} (see the app's console)"
                )


async def _reject(send: Any, status: int, code: str, message: str) -> None:
    body = dumps({"error": {"code": code, "message": message}}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
                *SECURITY_HEADERS,
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
