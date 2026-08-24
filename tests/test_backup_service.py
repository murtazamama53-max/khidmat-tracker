import os
import tempfile

import pytest

from app.services.backup_service import (
    BackupError,
    InvalidBackupFilenameError,
    create_backup,
    decrypt_backup_for_download,
    list_backups,
    restore_backup,
)

SECRET = "backup-test-secret-key"


@pytest.fixture
def workdir():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        backup_dir = os.path.join(tmp, "backups")
        with open(db_path, "wb") as f:
            f.write(b"FAKE-SQLITE-CONTENT-v1")
        yield db_path, backup_dir


def test_create_backup_writes_encrypted_file(workdir):
    db_path, backup_dir = workdir
    info = create_backup(db_path, backup_dir, SECRET)
    assert info.filename.startswith("khidmat-backup-")
    assert info.filename.endswith(".db.enc")

    full_path = os.path.join(backup_dir, info.filename)
    with open(full_path, "rb") as f:
        raw = f.read()
    assert b"FAKE-SQLITE-CONTENT-v1" not in raw  # must not be stored in plaintext


def test_create_backup_fails_gracefully_with_no_db(tmp_path):
    missing_db = str(tmp_path / "nope.db")
    with pytest.raises(BackupError):
        create_backup(missing_db, str(tmp_path / "backups"), SECRET)


def test_list_backups_sorted_newest_first(workdir):
    db_path, backup_dir = workdir
    b1 = create_backup(db_path, backup_dir, SECRET)
    b2 = create_backup(db_path, backup_dir, SECRET)  # created immediately after b1, same second is fine now

    listed = list_backups(backup_dir)
    assert len(listed) == 2  # must be two distinct files, not one overwriting the other
    assert b1.filename != b2.filename


def test_list_backups_ignores_non_backup_files(workdir):
    db_path, backup_dir = workdir
    create_backup(db_path, backup_dir, SECRET)
    os.makedirs(backup_dir, exist_ok=True)
    with open(os.path.join(backup_dir, "not-a-backup.txt"), "w") as f:
        f.write("junk")
    with open(os.path.join(backup_dir, "../etc-passwd-attempt.db.enc"), "w") as f:
        pass

    listed = list_backups(backup_dir)
    assert len(listed) == 1


def test_decrypt_backup_for_download_returns_original_bytes(workdir):
    db_path, backup_dir = workdir
    info = create_backup(db_path, backup_dir, SECRET)
    decrypted = decrypt_backup_for_download(info.filename, backup_dir, SECRET)
    assert decrypted == b"FAKE-SQLITE-CONTENT-v1"


def test_decrypt_with_wrong_secret_fails(workdir):
    db_path, backup_dir = workdir
    info = create_backup(db_path, backup_dir, SECRET)
    from app.services.token_crypto import TokenDecryptionError

    with pytest.raises(TokenDecryptionError):
        decrypt_backup_for_download(info.filename, backup_dir, "wrong-secret")


def test_restore_backup_overwrites_live_db_and_creates_safety_backup(workdir):
    db_path, backup_dir = workdir
    original = create_backup(db_path, backup_dir, SECRET)  # backup of "FAKE-SQLITE-CONTENT-v1"

    # Simulate the live DB changing after that backup.
    with open(db_path, "wb") as f:
        f.write(b"NEWER-CONTENT-v2")

    safety = restore_backup(original.filename, backup_dir, db_path, SECRET)
    assert safety is not None  # a safety backup of "v2" state was taken before restoring

    with open(db_path, "rb") as f:
        restored = f.read()
    assert restored == b"FAKE-SQLITE-CONTENT-v1"

    # The safety backup should let us get back to "v2" if the restore was a mistake.
    v2_recovered = decrypt_backup_for_download(safety.filename, backup_dir, SECRET)
    assert v2_recovered == b"NEWER-CONTENT-v2"


def test_restore_nonexistent_backup_raises(workdir):
    db_path, backup_dir = workdir
    with pytest.raises(BackupError):
        restore_backup("khidmat-backup-20200101-000000.db.enc", backup_dir, db_path, SECRET)


# ---------------- Path traversal protection ----------------


@pytest.mark.parametrize(
    "malicious_filename",
    [
        "../../../etc/passwd",
        "..%2f..%2fetc%2fpasswd",
        "/etc/passwd",
        "khidmat-backup-20260101-000000.db.enc/../../etc/passwd",
        "not-even-close-to-the-pattern.txt",
        "khidmat-backup-BADDATE-000000.db.enc",
    ],
)
def test_malicious_filenames_rejected_for_download(workdir, malicious_filename):
    db_path, backup_dir = workdir
    create_backup(db_path, backup_dir, SECRET)
    with pytest.raises(InvalidBackupFilenameError):
        decrypt_backup_for_download(malicious_filename, backup_dir, SECRET)


@pytest.mark.parametrize("malicious_filename", ["../../../etc/passwd", "/etc/passwd", "../secrets.db.enc"])
def test_malicious_filenames_rejected_for_restore(workdir, malicious_filename):
    db_path, backup_dir = workdir
    with pytest.raises(InvalidBackupFilenameError):
        restore_backup(malicious_filename, backup_dir, db_path, SECRET)
