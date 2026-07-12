from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts/l15_print_guard.py"
AUDIT = ROOT / "scripts/l15_config_grep.py"


def _write_secret(directory: Path) -> tuple[Path, str]:
    directory.mkdir()
    value = "unit-test-worker-password-42"
    path = directory / "worker_auth.txt"
    path.write_text(f"kai:{value}\n")
    path.chmod(0o600)
    return path, value


def test_guard_blocks_known_secret_on_stdout(tmp_path: Path) -> None:
    secret_dir = tmp_path / "secrets"
    secret_path, value = _write_secret(secret_dir)
    result = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "--no-default-secret-dirs",
            "--secret-dir",
            str(secret_dir),
            "--",
            sys.executable,
            "-c",
            "import pathlib,sys;sys.stdout.write(pathlib.Path(sys.argv[1]).read_text())",
            str(secret_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 86
    assert value not in result.stdout + result.stderr
    assert result.stdout == ""
    assert "blocked secret-bearing output" in result.stderr


def test_guard_blocks_pattern_on_stderr(tmp_path: Path) -> None:
    secret_dir = tmp_path / "secrets"
    _write_secret(secret_dir)
    result = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "--no-default-secret-dirs",
            "--secret-dir",
            str(secret_dir),
            "--",
            sys.executable,
            "-c",
            "import sys;sys.stderr.write('https://actor:credential-value@example.invalid')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 86
    assert "credential-value" not in result.stdout + result.stderr


def test_guard_blocks_base64_nginx_representation(tmp_path: Path) -> None:
    secret_dir = tmp_path / "secrets"
    secret_path, value = _write_secret(secret_dir)
    result = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "--no-default-secret-dirs",
            "--secret-dir",
            str(secret_dir),
            "--",
            sys.executable,
            "-c",
            "import base64,pathlib,sys;sys.stdout.buffer.write(base64.b64encode(pathlib.Path(sys.argv[1]).read_bytes().strip()))",
            str(secret_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 86
    assert value not in result.stdout + result.stderr


def test_guard_blocks_pat_in_git_url(tmp_path: Path) -> None:
    secret_dir = tmp_path / "secrets"
    _write_secret(secret_dir)
    result = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "--no-default-secret-dirs",
            "--secret-dir",
            str(secret_dir),
            "--",
            sys.executable,
            "-c",
            "print('https://actor:github_pat_' + 'A' * 32 + '@example.invalid/repo.git')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 86
    assert "github_pat_" not in result.stdout + result.stderr


def test_guard_blocks_unknown_basic_literal(tmp_path: Path) -> None:
    secret_dir = tmp_path / "secrets"
    _write_secret(secret_dir)
    result = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "--no-default-secret-dirs",
            "--secret-dir",
            str(secret_dir),
            "--",
            sys.executable,
            "-c",
            "print('kai:' + 'legacy-fixture-value')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 86
    assert "legacy-fixture-value" not in result.stdout + result.stderr


def test_guard_preserves_clean_output_and_exit(tmp_path: Path) -> None:
    secret_dir = tmp_path / "secrets"
    _write_secret(secret_dir)
    result = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "--no-default-secret-dirs",
            "--secret-dir",
            str(secret_dir),
            "--",
            sys.executable,
            "-c",
            "print('safe diagnostic')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == "safe diagnostic\n"
    assert result.stderr == ""


def test_config_grep_lists_only_filename_then_clears_on_secret_reference(tmp_path: Path) -> None:
    secret_dir = tmp_path / "secrets"
    secret_path, value = _write_secret(secret_dir)
    configs = tmp_path / "configs"
    configs.mkdir()
    config = configs / "service.yml"
    config.write_text(json.dumps({"password": value}))
    hit = subprocess.run(
        [sys.executable, str(AUDIT), "--no-default-secret-dirs", "--secret-dir", str(secret_dir), str(configs)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert hit.returncode == 1
    assert value not in hit.stdout + hit.stderr
    assert "CONFIG_FILES_WITH_PLAINTEXT_CREDENTIALS=1" in hit.stdout
    assert str(config) in hit.stdout

    config.write_text(json.dumps({"password_file": f"/run/secrets/{secret_path.name}"}))
    clean = subprocess.run(
        [sys.executable, str(AUDIT), "--no-default-secret-dirs", "--secret-dir", str(secret_dir), str(configs)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert clean.returncode == 0
    assert clean.stdout.strip() == "CONFIG_FILES_WITH_PLAINTEXT_CREDENTIALS=0"


def test_nginx_config_never_renders_worker_credential() -> None:
    entrypoint = (ROOT / "kai-web/entrypoint.sh").read_text()
    nginx = (ROOT / "kai-web/nginx.conf").read_text()
    assert "/run/secrets/kai_worker_auth" in entrypoint
    assert "envsubst" not in entrypoint
    assert "WORKER_AUTH_B64}" not in nginx
    assert "proxy_set_header Authorization $worker_authorization;" in nginx


def test_n8n_scrubber_removes_key_and_preserves_nonsecret_settings(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("Node is not installed on this host")
    config = tmp_path / "config"
    config.write_text(json.dumps({"encryptionKey": "unit-test-encryption-value", "fsStorageMigrated": True}))
    environment = os.environ.copy()
    environment["N8N_USER_FOLDER"] = str(tmp_path)
    result = subprocess.run(
        [node, str(ROOT / "n8n/scrub-settings.cjs")],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert json.loads(config.read_text()) == {"fsStorageMigrated": True}
