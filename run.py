#!/usr/bin/env python3
"""Launch one MindSpace agent from a profile YAML.

Usage:
    python run.py <profile>

Where <profile> is any of:
    - a profile name         -> profiles/<name>.yaml
    - a bare filename        -> profiles/<filename>
    - a relative/abs path    -> used as-is

Each profile is a standalone bot configuration (Discord token, model
settings, MCP servers, etc.). To run multiple agents, launch `run.py`
multiple times with different profiles — one process per agent.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _resolve_profile(arg: str, profiles_dir: Path) -> Path:
    p = Path(arg).expanduser()
    if p.is_absolute() or ("/" in arg or "\\" in arg):
        if p.is_file():
            return p.resolve()
        raise FileNotFoundError(f"Profile path does not exist: {p}")

    # Bare name or filename — look inside profiles/.
    for candidate in (profiles_dir / arg, profiles_dir / f"{arg}.yaml", profiles_dir / f"{arg}.yml"):
        if candidate.is_file():
            return candidate.resolve()

    available = sorted(x.name for x in profiles_dir.glob("*.yaml"))
    raise FileNotFoundError(
        f"No profile found for {arg!r} in {profiles_dir}. "
        f"Available: {', '.join(available) if available else '(none)'}"
    )


def _usage(profiles_dir: Path) -> str:
    available = sorted(x.stem for x in profiles_dir.glob("*.yaml"))
    listing = ", ".join(available) if available else "(none yet — add one to profiles/)"
    return (
        "Usage: python run.py <profile>\n"
        f"  <profile> resolves against {profiles_dir}/\n"
        f"  Available profiles: {listing}"
    )


def main() -> int:
    repo_root = Path(__file__).resolve().parent
    profiles_dir = repo_root / "profiles"

    if len(sys.argv) != 2:
        print(_usage(profiles_dir), file=sys.stderr)
        return 2

    try:
        profile_path = _resolve_profile(sys.argv[1], profiles_dir)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2

    # config.py reads MINDSPACE_CONFIG at import time; set it before the
    # mindspace package is touched.
    os.environ["MINDSPACE_CONFIG"] = str(profile_path)

    # Make src/ importable without a separate PYTHONPATH dance.
    sys.path.insert(0, str(repo_root / "src"))

    from mindspace.main import main as _run
    _run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
