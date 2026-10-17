#!/usr/bin/env python
"""Stop the detached dev servers started by `dev_start.py`."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PID_FILE = BACKEND / "data" / ".devpids"


def _kill(pid: int) -> bool:
    """Terminate one process tree."""
    if sys.platform == "win32":
        result = subprocess.run(  # noqa: S603
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        return True
    except ProcessLookupError:
        return False


def main() -> int:
    """Kill whatever `dev_start.py` recorded."""
    if not PID_FILE.is_file():
        print("no dev pids recorded; nothing to stop")
        return 0
    pids = [int(line) for line in PID_FILE.read_text(encoding="utf-8").split() if line.strip()]
    for pid in pids:
        ok = _kill(pid)
        print(f"  pid {pid}: {'stopped' if ok else 'not running'}")
    PID_FILE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
