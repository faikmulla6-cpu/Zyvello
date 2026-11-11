"""Off-platform encrypted Zyvello PostgreSQL backups and local restore tests.

Uses PostgreSQL's maintained pg_dump/pg_restore archive format and GnuPG
recipient encryption. Database passwords never appear in child argv, dump
bytes are piped directly into encryption, and archives cannot be written
inside a Git working tree. A restore test *only* accepts a loopback Postgres
database whose name begins with zyvello_restore_test_.

This protects application data outside Neon's short Free-plan history; it is
not a substitute for separately testing Neon-managed Auth recovery.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from getpass import getpass
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4


class BackupError(Exception):
    """A fail-closed backup or restore precondition was not met."""


def _database_env(url: str, *, expected_host: str | None = None,
                  restore_test: bool = False, environment: dict | None = None) -> tuple[dict, str]:
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname or ""
        port = parsed.port or 5432
        username = unquote(parsed.username or "")
        password = unquote(parsed.password or "")
        database = unquote(parsed.path.lstrip("/"))
        options = parse_qs(parsed.query, strict_parsing=True)
    except (ValueError, TypeError) as cause:
        raise BackupError("Invalid PostgreSQL connection configuration") from cause

    if (parsed.scheme not in ("postgres", "postgresql") or not hostname
            or not username or not password or not database or "/" in database
            or parsed.fragment or not 1 <= port <= 65535):
        raise BackupError("Invalid PostgreSQL connection configuration")

    if restore_test:
        if hostname not in ("localhost", "127.0.0.1", "::1"):
            raise BackupError("Restore rehearsal requires a local loopback PostgreSQL server")
        if not re.fullmatch(r"zyvello_restore_test_[a-z0-9_]{1,45}", database):
            raise BackupError("Restore rehearsal requires a dedicated test database name")
        sslmode = options.get("sslmode", ["disable"])[0]
        if sslmode not in ("disable", "require", "verify-full"):
            raise BackupError("Unsupported restore test SSL configuration")
    else:
        if (not expected_host or hostname.lower() != expected_host.lower()
                or not hostname.endswith(".neon.tech") or "-pooler" in hostname
                or database != "neondb"):
            raise BackupError("Source is not the pinned direct Zyvello Neon database")
        sslmode = options.get("sslmode", [""])[0]
        if sslmode not in ("require", "verify-full"):
            raise BackupError("Neon backups require an authenticated TLS connection")

    if set(options) - {"sslmode", "channel_binding"}:
        raise BackupError("Unsupported connection options; review the direct URL")
    channel_binding = options.get("channel_binding", ["prefer"])[0]
    if channel_binding not in ("prefer", "require"):
        raise BackupError("Unsupported channel-binding configuration")

    child = dict(environment if environment is not None else os.environ)
    for key in tuple(child):
        if key.startswith("PG") or key in ("ZYVELLO_BACKUP_DATABASE_URL", "ZYVELLO_REHEARSAL_DATABASE_URL"):
            child.pop(key, None)
    child.update(PGHOST=hostname, PGPORT=str(port), PGUSER=username,
                 PGPASSWORD=password, PGDATABASE=database, PGSSLMODE=sslmode,
                 PGCHANNELBINDING=channel_binding)
    return child, database


def _unprivileged_env(environment: dict | None = None) -> dict:
    child = dict(environment if environment is not None else os.environ)
    for key in tuple(child):
        if key.startswith("PG") or key in ("ZYVELLO_BACKUP_DATABASE_URL", "ZYVELLO_REHEARSAL_DATABASE_URL"):
            child.pop(key, None)
    return child


def _outside_git(path: Path) -> None:
    for directory in (path, *path.parents):
        if (directory / ".git").exists():
            raise BackupError("Database backups must stay outside Git repositories")


def _require_program(name: str) -> None:
    if shutil.which(name) is None:
        raise BackupError(f"Required utility is unavailable: {name}")


def _terminate(proc: subprocess.Popen | None) -> None:
    if proc is not None and proc.poll() is None:
        proc.kill()
        proc.wait()


def backup_archive(source_url: str, expected_host: str, output_directory: Path,
                   recipient_fingerprint: str, *, environment: dict | None = None,
                   dump_command: list[str] | None = None,
                   gpg_command: list[str] | None = None) -> Path:
    """Write a complete, authenticated .gpg archive; never persist plaintext."""
    if not re.fullmatch(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?", recipient_fingerprint):
        raise BackupError("A pinned 40/64-character GPG key fingerprint is required")
    pg_env, database = _database_env(source_url, expected_host=expected_host,
                                     environment=environment)
    destination = Path(output_directory).resolve()
    if not destination.is_dir():
        raise BackupError("Backup destination directory does not exist")
    _outside_git(destination)
    gpg_command = gpg_command or ["gpg"]
    _require_program(gpg_command[0])
    if dump_command is None:
        _require_program("pg_dump")
        dump_command = ["pg_dump", "--format=custom", "--no-owner", "--no-privileges",
                        "--no-password", "--dbname", database]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = destination / f"zyvello-{stamp}-{uuid4().hex[:8]}.pgcustom.gpg"
    fd, temp_name = tempfile.mkstemp(prefix="zyvello-", suffix=".partial", dir=destination)
    os.close(fd)
    os.chmod(temp_name, 0o600)
    encrypt = None
    dump = None
    try:
        encrypt = subprocess.Popen(
            [*gpg_command, "--batch", "--no-tty", "--yes", "--trust-model", "always",
             "--encrypt", "--recipient", recipient_fingerprint, "--output", temp_name],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=_unprivileged_env(environment),
        )
        dump = subprocess.Popen(dump_command, stdout=encrypt.stdin,
                                stderr=subprocess.DEVNULL, env=pg_env)
        encrypt.stdin.close()
        if dump.wait(timeout=900) != 0 or encrypt.wait(timeout=90) != 0:
            raise BackupError("Database dump or recipient encryption failed")
        if Path(temp_name).stat().st_size < 64:
            raise BackupError("Encrypted backup was unexpectedly small")
        # Hard-link within the same directory is exclusive: never overwrite a
        # previously completed backup, even if another process races us.
        os.link(temp_name, final)
        return final
    except (OSError, subprocess.TimeoutExpired) as cause:
        raise BackupError("Encrypted backup could not complete safely") from cause
    finally:
        _terminate(dump)
        _terminate(encrypt)
        Path(temp_name).unlink(missing_ok=True)


def restore_rehearsal(archive: Path, target_url: str, *, environment: dict | None = None,
                      restore_command: list[str] | None = None,
                      gpg_command: list[str] | None = None) -> None:
    """Verify decryptability, then restore transactionally to loopback *only*."""
    pg_env, database = _database_env(target_url, restore_test=True, environment=environment)
    source = Path(archive).resolve()
    if source.suffix != ".gpg" or not source.is_file():
        raise BackupError("An existing encrypted .gpg archive is required")
    gpg_command = gpg_command or ["gpg"]
    _require_program(gpg_command[0])
    if restore_command is None:
        _require_program("pg_restore")
        restore_command = ["pg_restore", "--single-transaction", "--exit-on-error",
                           "--no-owner", "--no-privileges", "--no-password",
                           "--dbname", database, "-"]

    decrypt_env = _unprivileged_env(environment)
    preflight = subprocess.run(
        [*gpg_command, "--batch", "--no-tty", "--decrypt", "--output", os.devnull,
         str(source)], env=decrypt_env, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, check=False,
    )
    if preflight.returncode != 0:
        raise BackupError("Archive cannot be fully authenticated with the available private key")

    decrypt = None
    restore = None
    try:
        decrypt = subprocess.Popen(
            [*gpg_command, "--batch", "--no-tty", "--decrypt", str(source)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=decrypt_env,
        )
        restore = subprocess.Popen(restore_command, stdin=decrypt.stdout,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env=pg_env)
        decrypt.stdout.close()
        if restore.wait(timeout=900) != 0 or decrypt.wait(timeout=90) != 0:
            raise BackupError("Isolated database restore failed")
    except (OSError, subprocess.TimeoutExpired) as cause:
        raise BackupError("Isolated database restore could not complete") from cause
    finally:
        _terminate(restore)
        _terminate(decrypt)


def main() -> int:
    parser = argparse.ArgumentParser(description="Encrypted Zyvello backup and loopback-only restore rehearsal")
    sub = parser.add_subparsers(dest="action", required=True)
    backup = sub.add_parser("backup", help="Stream the direct Neon database dump to GPG encryption")
    backup.add_argument("--output-dir", required=True, type=Path)
    backup.add_argument("--recipient", required=True, help="Pinned GPG public-key fingerprint")
    backup.add_argument("--expected-host", required=True,
                        help="Exact direct Neon host for the Zyvello staging branch (no password)")
    restore = sub.add_parser("restore-test", help="Restore only to a named local disposable database")
    restore.add_argument("--archive", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.action == "backup":
            source_url = os.environ.get("ZYVELLO_BACKUP_DATABASE_URL") or getpass(
                "Paste the direct Zyvello Neon URL (hidden): ")
            result = backup_archive(source_url, args.expected_host,
                                    args.output_dir, args.recipient)
            print(f"Encrypted archive created: {result.name}")
        else:
            target_url = os.environ.get("ZYVELLO_REHEARSAL_DATABASE_URL") or getpass(
                "Paste the LOCAL disposable Postgres URL (hidden): ")
            restore_rehearsal(args.archive, target_url)
            print("PASS: encrypted archive authenticated and restored to the disposable local database")
    except BackupError as cause:
        print(f"FAILED: {cause}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
