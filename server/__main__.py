from __future__ import annotations

import argparse
import os
import signal
import threading
from pathlib import Path

from .app import create_server, create_web_server
from .pipeline import Toolchain, Worker
from .store import JobStore


def _configure_runtime_cache(state_dir: Path) -> Path:
    cache_dir = Path(
        os.environ.get("PHOTOGRAMMETRY_CACHE_DIR", state_dir / "cache")
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("XDG_CACHE_HOME", str(cache_dir))
    return cache_dir


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Headless photogrammetry service")
    commands = result.add_subparsers(dest="command", required=True)

    web = commands.add_parser("web", help="run the HTTP API and static UI")
    web.add_argument("--host", default="0.0.0.0")
    web.add_argument("--port", type=int, default=8080)
    web.add_argument("--state-dir", type=Path, required=True)
    web.add_argument("--web-dir", type=Path)

    worker = commands.add_parser("worker", help="run the persistent FIFO worker")
    worker.add_argument("--state-dir", type=Path, required=True)
    worker.add_argument("--tools-dir", type=Path, required=True)

    serve = commands.add_parser("serve", help="run web and worker in one process")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--state-dir", type=Path, required=True)
    serve.add_argument("--tools-dir", type=Path, required=True)
    serve.add_argument("--web-dir", type=Path)
    return result


def _run_web(args) -> None:
    server = create_web_server(
        args.host,
        args.port,
        data_dir=args.state_dir,
        web_dir=args.web_dir,
    )

    def stop(_signum, _frame) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"Photogrammetry web service listening on {server.server_address}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _run_worker(args) -> None:
    cache_dir = _configure_runtime_cache(args.state_dir)
    worker = Worker(JobStore(args.state_dir), Toolchain.from_bin_dir(args.tools_dir))

    def stop(_signum, _frame) -> None:
        worker.stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    worker.start()
    print(f"Photogrammetry worker started; runtime cache: {cache_dir}", flush=True)
    worker.thread.join()


def _run_combined(args) -> None:
    _configure_runtime_cache(args.state_dir)
    server = create_server(
        args.host,
        args.port,
        data_dir=args.state_dir,
        bin_dir=args.tools_dir,
        web_dir=args.web_dir,
    )

    def stop(_signum, _frame) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"Photogrammetry service listening on {server.server_address}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.worker.stop()
        server.server_close()


def main() -> None:
    args = parser().parse_args()
    if args.command == "web":
        _run_web(args)
    elif args.command == "worker":
        _run_worker(args)
    else:
        _run_combined(args)


if __name__ == "__main__":
    main()
