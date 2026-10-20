"""Fail the build when the two copies of the runtime pins drift apart.

The runtime dependencies are pinned twice on purpose: `backend/requirements-runtime.txt`
is what pip installs locally and on the container image, and the root
`pyproject.toml` is what Vercel installs the project from. A silent drift
between them would mean the deployed build runs different libraries from the
tested one, so this check is part of `make check`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS = REPO_ROOT / "backend" / "requirements-runtime.txt"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _normalise(name: str) -> str:
    """Comparable package name: no extras, no underscores, lower case."""
    return re.sub(r"\[.*\]", "", name).strip().lower().replace("_", "-")


def requirement_pins() -> dict[str, str]:
    """`name -> pinned version` for every requirement line."""
    pins: dict[str, str] = {}
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name, _, version = line.partition("==")
        pins[_normalise(name)] = version.strip()
    return pins


def project_pins() -> dict[str, str]:
    """`name -> pinned version` for every entry of `[project.dependencies]`."""
    text = PYPROJECT.read_text(encoding="utf-8")
    block = re.search(r"^dependencies\s*=\s*\[(.*?)^\]", text, re.MULTILINE | re.DOTALL)
    if block is None:
        raise SystemExit("pyproject.toml has no [project].dependencies table")
    pins: dict[str, str] = {}
    for raw in block.group(1).splitlines():
        line = raw.split("#", 1)[0].strip().strip(",").strip('"').strip("'")
        if not line:
            continue
        name, _, version = line.partition("==")
        pins[_normalise(name)] = version.strip()
    return pins


def main() -> int:
    """Compare the two tables and report the differences."""
    want = requirement_pins()
    have = project_pins()
    problems: list[str] = []
    for name, version in sorted(want.items()):
        if name not in have:
            problems.append(f"{name} is missing from pyproject.toml")
        elif have[name] != version:
            problems.append(f"{name}: requirements say {version}, pyproject says {have[name]}")
    for name in sorted(set(have) - set(want)):
        problems.append(f"{name} is in pyproject.toml but not in requirements-runtime.txt")
    if problems:
        print("runtime pin drift between requirements-runtime.txt and pyproject.toml:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"runtime pins agree ({len(want)} packages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
