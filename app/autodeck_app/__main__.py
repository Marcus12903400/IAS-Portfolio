"""`python -m autodeck_app [--port N] [--no-browser]` -- start the local server and open the app."""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser

from . import __version__, settings


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex((host, port)) != 0


def _running_version(host: str, port: int) -> str | None:
    """Version of the AutoDeck app already answering on this port, if any."""

    import json
    from urllib.request import urlopen

    try:
        with urlopen(f"http://{host}:{port}/api/state", timeout=2) as response:
            return str(json.load(response).get("version"))
    except Exception:  # noqa: BLE001 -- anything else on the port is "not ours"
        return None


def _open_browser(url: str) -> None:
    # give the server a moment, then open the page
    for _ in range(40):
        time.sleep(0.25)
        if not _port_free(settings.HOST, int(url.rsplit(":", 1)[1].split("/")[0])):
            break
    webbrowser.open(url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autodeck-app", description=f"AutoDeck v{__version__} review app")
    parser.add_argument("--port", type=int, default=settings.PORT)
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    args = parser.parse_args(argv)

    settings.ensure_engine_on_path()
    settings.ensure_dirs()
    try:
        import autodeck2  # noqa: F401
    except ImportError as exc:
        print(f"AutoDeck2 engine not importable from {settings.AUTODECK2_ROOT}: {exc}")
        print("Set AUTODECK2_ROOT (and AUTODECK_V1_ROOT) to where the engine lives.")
        return 2

    from .server import create_app

    port = args.port
    if not _port_free(settings.HOST, port):
        running = _running_version(settings.HOST, port)
        if running == __version__:
            # this same version is already running (a second double-click): just open the page
            url = f"http://{settings.HOST}:{port}/"
            print(f"AutoDeck v{__version__} is already running at {url}")
            if not args.no_browser:
                webbrowser.open(url)
            return 0
        # a DIFFERENT AutoDeck version (or another program) owns the port --
        # never show the wrong app; start this version on the next free port
        taken = port
        while not _port_free(settings.HOST, port):
            port += 1
        owner = f"AutoDeck v{running}" if running else "another program"
        print(f"Port {taken} is in use by {owner}; starting AutoDeck v{__version__} on port {port} instead.")

    app = create_app()
    url = f"http://{settings.HOST}:{port}/"
    print(f"AutoDeck v{__version__} at {url}   (leave this window open; Ctrl+C stops it)")
    if not args.no_browser:
        threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
    app.run(host=settings.HOST, port=port, threaded=True, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
