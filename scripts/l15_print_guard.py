#!/usr/bin/env python3
"""Buffer command output and fail closed before a known secret reaches a transcript."""

from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote_from_bytes

BLOCKED_EXIT = 86
CONFIG_ERROR_EXIT = 87
MIN_SECRET_LENGTH = 8
SENSITIVE_NAME = re.compile(r"password|passwd|token|secret|credential|api[_-]?key|auth", re.I)
PATTERNS = (
    re.compile(rb"[A-Za-z][A-Za-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(rb"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(rb"sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"sk-(?:proj-)?[A-Za-z0-9_-]{24,}"),
    re.compile(rb"tskey-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"Basic\s+[A-Za-z0-9+/]{16,}={0,2}", re.I),
    re.compile(rb"(?<![A-Za-z0-9_-])kai:[A-Za-z0-9!#$%&*+,._=?@^\-]{8,}"),
)


def _default_secret_dirs() -> list[Path]:
    home = Path.home()
    return [Path("/run/secrets"), home / ".kai/secrets", home / "kai-system/secrets"]


def _values_from_file(path: Path) -> set[bytes]:
    try:
        raw = path.read_bytes().strip()
    except OSError:
        return set()
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    values: set[bytes] = set()
    if lines:
        values.add(lines[0])
    for line in lines:
        if b"=" in line:
            key, value = line.split(b"=", 1)
            if SENSITIVE_NAME.search(key.decode(errors="ignore")):
                values.add(value.strip().strip(b"\"'"))
        if b":" in line and b"://" not in line:
            values.add(line)
            values.add(line.split(b":", 1)[1].strip())
    return {value for value in values if len(value) >= MIN_SECRET_LENGTH and b"\x00" not in value}


def load_secret_variants(secret_dirs: list[Path]) -> set[bytes]:
    values: set[bytes] = set()
    for directory in secret_dirs:
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if path.is_file():
                values.update(_values_from_file(path))
    for name, value in os.environ.items():
        normalized = name.upper()
        is_reference = normalized.endswith(("_FILE", "_PATH", "_SOCK", "_URL"))
        if SENSITIVE_NAME.search(name) and not is_reference and len(value) >= MIN_SECRET_LENGTH:
            values.add(value.encode())

    variants: set[bytes] = set()
    for value in values:
        variants.add(value)
        variants.add(base64.b64encode(value))
        variants.add(quote_from_bytes(value).encode())
    return {value for value in variants if len(value) >= MIN_SECRET_LENGTH}


def contains_secret(data: bytes, variants: set[bytes]) -> bool:
    return any(value in data for value in variants) or any(pattern.search(data) for pattern in PATTERNS)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secret-dir", action="append", default=[], type=Path)
    parser.add_argument("--no-default-secret-dirs", action="store_true")
    parser.add_argument("--stdin", action="store_true", help="guard stdin instead of launching a command")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    secret_dirs = list(args.secret_dir)
    if not args.no_default_secret_dirs:
        secret_dirs.extend(_default_secret_dirs())
    variants = load_secret_variants(secret_dirs)
    if not variants:
        print("L15 PRINT GUARD: no readable secret source; command not run", file=sys.stderr)
        return CONFIG_ERROR_EXIT

    if args.stdin:
        stdout = sys.stdin.buffer.read()
        stderr = b""
        returncode = 0
    else:
        if not args.command:
            print("L15 PRINT GUARD: missing command", file=sys.stderr)
            return CONFIG_ERROR_EXIT
        try:
            completed = subprocess.run(args.command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        except OSError:
            print("L15 PRINT GUARD: command launch failed", file=sys.stderr)
            return CONFIG_ERROR_EXIT
        stdout, stderr, returncode = completed.stdout, completed.stderr, completed.returncode

    if contains_secret(stdout, variants) or contains_secret(stderr, variants):
        print("L15 PRINT GUARD: blocked secret-bearing output", file=sys.stderr)
        return BLOCKED_EXIT

    sys.stdout.buffer.write(stdout)
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(stderr)
    sys.stderr.buffer.flush()
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
