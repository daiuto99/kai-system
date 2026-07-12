#!/usr/bin/env python3
"""Run a value-aware recursive grep without ever printing matching lines."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import tempfile

from l15_print_guard import _default_secret_dirs, load_secret_variants

INCLUDES = (
    "*.yml", "*.yaml", "*.conf", "*.json", "*.toml", "*.ini",
    "*.env", ".env*", "*.service", "*.properties", "*.config",
    "Dockerfile*", "*entrypoint*.sh", "config",
)
EXCLUDES = (".git", "secrets", "node_modules", "__pycache__", ".venv", "dist", "kai_mode")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--secret-dir", action="append", default=[], type=Path)
    parser.add_argument("--no-default-secret-dirs", action="store_true")
    args = parser.parse_args()

    secret_dirs = list(args.secret_dir)
    if not args.no_default_secret_dirs:
        secret_dirs.extend(_default_secret_dirs())
    variants = sorted(load_secret_variants(secret_dirs))
    if not variants:
        print("CONFIG_CREDENTIAL_GREP=ERROR no readable secret source")
        return 2
    if any(b"\n" in value or b"\x00" in value for value in variants):
        print("CONFIG_CREDENTIAL_GREP=ERROR unsafe pattern encoding")
        return 2

    descriptor, pattern_name = tempfile.mkstemp(prefix="kai-l15-patterns-")
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as pattern_file:
            pattern_file.write(b"\n".join(variants) + b"\n")
        command = ["grep", "-r", "-l", "-F", "-f", pattern_name]
        for include in INCLUDES:
            command.append(f"--include={include}")
        for exclude in EXCLUDES:
            command.append(f"--exclude-dir={exclude}")
        command.extend(str(root) for root in args.roots)
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    finally:
        try:
            os.unlink(pattern_name)
        except FileNotFoundError:
            pass

    if result.returncode not in (0, 1):
        print(f"CONFIG_CREDENTIAL_GREP=ERROR grep_exit={result.returncode}")
        return 2
    hits = [line.decode(errors="replace") for line in result.stdout.splitlines() if line]
    print(f"CONFIG_FILES_WITH_PLAINTEXT_CREDENTIALS={len(hits)}")
    for hit in hits:
        print(f"CONFIG_HIT={hit}")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
