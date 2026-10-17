#!/usr/bin/env python
"""Start the SKYGYM backend + Vite dev server as *detached* background processes.

Used by `make dev` on Windows, where a Ctrl-C in the foreground would take the
child processes with it. Writes PIDs to backend/data/.devpids so `dev_stop.py`
can shut them down.

    .venv/Scripts/python backend/scripts/dev_start.py
"""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
PYTHON = REPO / ".venv" / "Scripts" / "python.exe"
FRONTEND = REPO / "frontend"
PID_FILE = BACKEND / "data" / ".devpids"
LOG_DIR = REPO / "backend" / "data" / "logs"


def _spawn(cmd: list[str], cwd: Path, log_name: str) -> int:
    """Start a detached child process with its output redirected to a log file."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out = (LOG_DIR / log_name).open("w", encoding="utf-8")
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(  # noqa: S603
        cmd,
        cwd=str(cwd),
        stdout=out,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    return proc.pid


def wait_for(url: str, timeout_s: float = 45.0) -> bool:
    """Poll `url` until it answers or the timeout expires."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:  # noqa: S310
                if r.status < 500:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.7)
    return False


def main() -> int:
    """Start both servers and verify them."""
    if not PYTHON.exists():
        print(f"venv missing at {PYTHON}; run: python -m venv .venv", file=sys.stderr)
        return 1

    backend_pid = _spawn(
        [
            str(PYTHON),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--app-dir",
            ".",
        ],
        cwd=BACKEND,
        log_name="backend.log",
    )
    frontend_pid = _spawn(
        ["npm.cmd", "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173"],
        cwd=FRONTEND,
        log_name="frontend.log",
    )
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(f"{backend_pid}\n{frontend_pid}\n", encoding="utf-8")

    backend_ok = wait_for("http://127.0.0.1:8000/api/health")
    frontend_ok = wait_for("http://127.0.0.1:5173/")
    print(
        f"backend  http://127.0.0.1:8000   {'ready' if backend_ok else 'NOT READY'}  (pid {backend_pid})"
    )
    print(
        f"frontend http://127.0.0.1:5173   {'ready' if frontend_ok else 'NOT READY'}  (pid {frontend_pid})"
    )
    print(f"logs     {LOG_DIR}")
    print("stop with: .venv/Scripts/python backend/scripts/dev_stop.py")
    return 0 if backend_ok and frontend_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
