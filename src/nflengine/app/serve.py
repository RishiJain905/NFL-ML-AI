"""`nfl app`: run the control room on 127.0.0.1 (documentation/control-room/README.md §4)."""

from __future__ import annotations

import socket
import threading
import webbrowser
from collections.abc import Callable

HOST = "127.0.0.1"  # never 0.0.0.0: the app is localhost only (README §5.1)
EXIT_OK, EXIT_PORT_IN_USE, EXIT_NO_BUILD, EXIT_NO_REPLAY = 0, 1, 2, 3


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
    live_replay: dict | None = None,
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
    feed = None
    if live_replay is not None:
        from nflengine.live.replayfeed import ReplayError, ReplayFeed, parse_at
        from nflengine.paths import DataRootError, ensure_data_root

        try:
            folder = ensure_data_root(create_dirs=False).live_data / "summaries"
            feed = ReplayFeed.from_folder(
                folder,
                live_replay["event"],
                at=parse_at(live_replay.get("at")),
                speed=float(live_replay.get("speed") or 1.0),
                lag_s=float(live_replay.get("lag_s") or 0.0),
            )
        except (ReplayError, ValueError, DataRootError) as e:
            log(f"Can't start the replay: {e}")
            return EXIT_NO_REPLAY
    settings = srv.AppSettings(
        port=port,
        dev=dev,
        rehearsal=rehearsal,
        web_dist=None if dev else dist,
        fail_at=fail_at,
        live_replay=feed,
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
    if feed is not None:
        log(
            f"Live replay: Game day serves {feed.season} week {feed.week()} as if live, from "
            f"{feed.now():%Y-%m-%d %H:%M:%S} UTC at {feed.speed:g}x, ESPN {feed.lag_s:g} s behind. "
            "Nothing is fetched from ESPN."
        )
    try:
        uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, log_level="warning")).run()
    except KeyboardInterrupt:  # uvicorn replays SIGINT after a clean shutdown
        log("Control room stopped.")
    return EXIT_OK
