"""`nfl app`: run the control room on 127.0.0.1 (documentation/control-room/README.md §4)."""

from __future__ import annotations

import socket
import threading
import webbrowser
from collections.abc import Callable

HOST = "127.0.0.1"  # never 0.0.0.0: the app is localhost only (README §5.1)
EXIT_OK, EXIT_PORT_IN_USE, EXIT_NO_BUILD = 0, 1, 2


def port_free(port: int, host: str = HOST) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def serve(
    port: int = 8765,
    open_browser: bool = True,
    dev: bool = False,
    rehearsal: bool = False,
    log: Callable[[str], None] = print,
    fail_at: str | None = None,
) -> int:
    """Start the server and block until Ctrl+C. Returns an exit code."""
    import uvicorn

    from nflengine.app import server as srv

    dist = srv.WEB_DIST
    if not dev and not (dist / "index.html").exists():
        log(
            f"The web app isn't built yet ({dist} is missing). Build it once with "
            "`npm --prefix web ci` then `npm --prefix web run build`, or use `--dev`."
        )
        return EXIT_NO_BUILD
    if not port_free(port):
        log(f"Port {port} on {HOST} is in use (is the control room already running?).")
        return EXIT_PORT_IN_USE
    settings = srv.AppSettings(
        port=port, dev=dev, rehearsal=rehearsal, web_dist=None if dev else dist, fail_at=fail_at
    )
    app = srv.create_app(settings)
    url = f"http://{HOST}:{port}/"
    if dev:
        log(
            f"API on {url}api/ (dev mode). Run `npm --prefix web run dev`, then open "
            "http://localhost:5173/."
        )
    else:
        log(f"Control room on {url}  (Ctrl+C to stop)")
        if open_browser:
            threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    if rehearsal:
        log(
            "Rehearsal mode: the Run button runs `nfl weekly rehearse` into "
            "rehearsals/control-room, never the live week."
            + (
                f" The first rehearsal fails at {fail_at} on purpose (--fail-at)."
                if fail_at
                else ""
            )
        )
    try:
        uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, log_level="warning")).run()
    except KeyboardInterrupt:  # uvicorn replays SIGINT after a clean shutdown
        log("Control room stopped.")
    return EXIT_OK
