"""
Database backup/restore (blueprint section 31/6): Backup Now, Restore
Backup, Export Database. Backups are timestamped, encrypted copies of the
live SQLite file, stored under instance/backups/ -- never inside
app/static/, so Flask never serves them as public files. The only way to
read or restore one is through an owner-authenticated route.
"""
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List

from app.services.token_crypto import decrypt_bytes, encrypt_bytes

_FILENAME_RE = re.compile(r"^khidmat-backup-(\d{8})-(\d{6})-([0-9a-f]{8})\.db\.enc$")


class BackupError(RuntimeError):
    pass


class InvalidBackupFilenameError(BackupError):
    """Raised when a filename doesn't match the expected safe pattern -- blocks path traversal."""


@dataclass(frozen=True)
class BackupInfo:
    filename: str
    created_at: datetime
    size_bytes: int


def _validate_filename(filename: str) -> None:
    """
    Only ever accept filenames matching our own generated pattern. This is
    what makes restore/download safe against path traversal (e.g.
    '../../etc/passwd') -- an attacker-supplied filename can never match
    this pattern, so it's rejected before any filesystem access happens.
    """
    if not _FILENAME_RE.match(filename):
        raise InvalidBackupFilenameError(f"'{filename}' is not a valid backup filename.")


def create_backup(db_path: str, backup_dir: str, secret_key: str) -> BackupInfo:
    if not os.path.exists(db_path):
        raise BackupError("No database file found to back up yet.")

    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now(timezone.utc)
    unique_suffix = secrets.token_hex(4)  # guarantees no collision even for backups made in the same second
    filename = f"khidmat-backup-{timestamp.strftime('%Y%m%d-%H%M%S')}-{unique_suffix}.db.enc"
    _validate_filename(filename)  # defense-in-depth: our own generated name must satisfy the same pattern

    with open(db_path, "rb") as f:
        plaintext = f.read()
    encrypted = encrypt_bytes(plaintext, secret_key)

    dest_path = os.path.join(backup_dir, filename)
    with open(dest_path, "wb") as f:
        f.write(encrypted)

    return BackupInfo(filename=filename, created_at=timestamp, size_bytes=len(encrypted))


def list_backups(backup_dir: str) -> List[BackupInfo]:
    if not os.path.isdir(backup_dir):
        return []
    infos = []
    for entry in os.listdir(backup_dir):
        if not _FILENAME_RE.match(entry):
            continue  # ignore anything that isn't one of our own backups
        full_path = os.path.join(backup_dir, entry)
        stat = os.stat(full_path)
        m = re.match(r"khidmat-backup-(\d{8})-(\d{6})-[0-9a-f]{8}\.db\.enc", entry)
        created_at = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        infos.append(BackupInfo(filename=entry, created_at=created_at, size_bytes=stat.st_size))
    return sorted(infos, key=lambda b: b.created_at, reverse=True)


def decrypt_backup_for_download(filename: str, backup_dir: str, secret_key: str) -> bytes:
    """Returns the decrypted, plain SQLite file bytes for a safe, owner-initiated download."""
    _validate_filename(filename)
    path = os.path.join(backup_dir, filename)
    if not os.path.isfile(path):
        raise BackupError("Backup file not found.")
    with open(path, "rb") as f:
        encrypted = f.read()
    return decrypt_bytes(encrypted, secret_key)


def restore_backup(filename: str, backup_dir: str, db_path: str, secret_key: str) -> BackupInfo:
    """
    Restores the live database from a previously created backup. Takes a
    fresh safety backup of the CURRENT state first, so a restore is never
    a one-way door -- if you restore the wrong file, the state just
    before the restore is itself a backup you can restore back to.
    """
    _validate_filename(filename)
    path = os.path.join(backup_dir, filename)
    if not os.path.isfile(path):
        raise BackupError("Backup file not found.")

    with open(path, "rb") as f:
        encrypted = f.read()
    plaintext = decrypt_bytes(encrypted, secret_key)  # validates integrity before touching the live DB

    safety_backup = None
    if os.path.exists(db_path):
        safety_backup = create_backup(db_path, backup_dir, secret_key)

    with open(db_path, "wb") as f:
        f.write(plaintext)

    return safety_backup
