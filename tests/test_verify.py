import gzip
import hashlib
import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from proxmox_backup_verifier.verify import (
    BackupFile,
    VMBackup,
    VerifyResult,
    parse_backup_filename,
    discover_backups,
    verify_gzip_integrity,
    compute_sha256,
    generate_checksums,
    verify_checksums,
    compare_remote_sizes,
    _human_size,
)


# --- parse_backup_filename ---


def test_parse_vma_gz_filename(tmp_path):
    f = tmp_path / "vzdump-qemu-101-2026_02_10-05_00_01.vma.gz"
    f.write_bytes(b"x" * 100)
    bf = parse_backup_filename(f)
    assert bf is not None
    assert bf.vm_id == "101"
    assert bf.timestamp == datetime(2026, 2, 10, 5, 0, 1)
    assert bf.kind == "vma.gz"
    assert bf.size == 100


def test_parse_log_filename(tmp_path):
    f = tmp_path / "vzdump-qemu-200-2025_12_01-12_30_00.log"
    f.write_bytes(b"log data")
    bf = parse_backup_filename(f)
    assert bf is not None
    assert bf.vm_id == "200"
    assert bf.kind == "log"


def test_parse_notes_filename(tmp_path):
    f = tmp_path / "vzdump-qemu-100-2026_01_15-03_00_00.vma.gz.notes"
    f.write_text("my-vm-name")
    bf = parse_backup_filename(f)
    assert bf is not None
    assert bf.kind == "vma.gz.notes"


def test_parse_non_matching_filename(tmp_path):
    f = tmp_path / "random-file.txt"
    f.write_bytes(b"hi")
    assert parse_backup_filename(f) is None


def test_parse_partial_match(tmp_path):
    f = tmp_path / "vzdump-lxc-100-2026_01_01-00_00_00.tar.gz"
    f.write_bytes(b"hi")
    assert parse_backup_filename(f) is None


# --- discover_backups ---


def test_discover_backups_groups_by_vm(tmp_path):
    for name in [
        "vzdump-qemu-100-2026_01_01-00_00_00.vma.gz",
        "vzdump-qemu-100-2026_01_02-00_00_00.vma.gz",
        "vzdump-qemu-101-2026_01_01-00_00_00.vma.gz",
    ]:
        (tmp_path / name).write_bytes(b"x" * 50)

    vms = discover_backups(tmp_path)
    assert "100" in vms
    assert "101" in vms
    assert vms["100"].backup_count == 2
    assert vms["101"].backup_count == 1


def test_discover_backups_reads_vm_name_from_notes(tmp_path):
    (tmp_path / "vzdump-qemu-100-2026_01_01-00_00_00.vma.gz").write_bytes(b"x")
    (tmp_path / "vzdump-qemu-100-2026_01_01-00_00_00.vma.gz.notes").write_text("web-server")

    vms = discover_backups(tmp_path)
    assert vms["100"].vm_name == "web-server"


def test_discover_backups_default_vm_name(tmp_path):
    (tmp_path / "vzdump-qemu-100-2026_01_01-00_00_00.vma.gz").write_bytes(b"x")
    vms = discover_backups(tmp_path)
    assert vms["100"].vm_name == "VM 100"


def test_discover_backups_empty_dir(tmp_path):
    vms = discover_backups(tmp_path)
    assert vms == {}


def test_discover_backups_sorted_by_vm_id(tmp_path):
    for vm_id in ["200", "100", "150"]:
        (tmp_path / f"vzdump-qemu-{vm_id}-2026_01_01-00_00_00.vma.gz").write_bytes(b"x")
    vms = discover_backups(tmp_path)
    assert list(vms.keys()) == ["100", "150", "200"]


# --- VMBackup ---


def test_vmbackup_latest():
    vm = VMBackup(vm_id="100", vm_name="test")
    vm.backups = [
        BackupFile(path=Path("/a.vma.gz"), vm_id="100",
                   timestamp=datetime(2026, 1, 1), size=100, kind="vma.gz"),
        BackupFile(path=Path("/b.vma.gz"), vm_id="100",
                   timestamp=datetime(2026, 1, 5), size=200, kind="vma.gz"),
        BackupFile(path=Path("/c.log"), vm_id="100",
                   timestamp=datetime(2026, 1, 10), size=50, kind="log"),
    ]
    latest = vm.latest
    assert latest.path == Path("/b.vma.gz")


def test_vmbackup_latest_none():
    vm = VMBackup(vm_id="100", vm_name="test")
    vm.backups = [
        BackupFile(path=Path("/c.log"), vm_id="100",
                   timestamp=datetime(2026, 1, 10), size=50, kind="log"),
    ]
    assert vm.latest is None


# --- gzip integrity ---


def test_verify_gzip_valid(tmp_path):
    f = tmp_path / "test.vma.gz"
    with gzip.open(f, "wb") as gz:
        gz.write(b"hello world" * 1000)
    result = verify_gzip_integrity(f)
    assert result.passed is True


def test_verify_gzip_corrupt(tmp_path):
    f = tmp_path / "test.vma.gz"
    with gzip.open(f, "wb") as gz:
        gz.write(b"hello world" * 1000)
    # Corrupt the file by truncating it
    data = f.read_bytes()
    f.write_bytes(data[:len(data) // 2])
    result = verify_gzip_integrity(f)
    assert result.passed is False


# --- checksums ---


def test_compute_sha256(tmp_path):
    f = tmp_path / "test.bin"
    content = b"test content for hashing"
    f.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()
    assert compute_sha256(f) == expected


def test_generate_and_verify_checksums(tmp_path):
    for name in ["vzdump-qemu-100-2026_01_01-00_00_00.vma.gz",
                 "vzdump-qemu-101-2026_01_01-00_00_00.vma.gz"]:
        (tmp_path / name).write_bytes(b"fake vma content " + name.encode())

    checksum_file = tmp_path / "checksums.sha256"
    entries = generate_checksums(tmp_path, checksum_file)
    assert len(entries) == 2
    assert checksum_file.exists()

    # Verify should pass
    results = verify_checksums(tmp_path, checksum_file)
    assert all(r.passed for r in results)
    assert len(results) == 2


def test_verify_checksums_detects_corruption(tmp_path):
    f = tmp_path / "vzdump-qemu-100-2026_01_01-00_00_00.vma.gz"
    f.write_bytes(b"original content")

    checksum_file = tmp_path / "checksums.sha256"
    generate_checksums(tmp_path, checksum_file)

    # Corrupt the file
    f.write_bytes(b"corrupted content")
    results = verify_checksums(tmp_path, checksum_file)
    assert len(results) == 1
    assert results[0].passed is False


def test_verify_checksums_missing_file(tmp_path):
    f = tmp_path / "vzdump-qemu-100-2026_01_01-00_00_00.vma.gz"
    f.write_bytes(b"content")

    checksum_file = tmp_path / "checksums.sha256"
    generate_checksums(tmp_path, checksum_file)

    f.unlink()
    results = verify_checksums(tmp_path, checksum_file)
    assert results[0].passed is False
    assert "missing" in results[0].detail.lower()


def test_verify_checksums_no_checksum_file(tmp_path):
    results = verify_checksums(tmp_path, tmp_path / "checksums.sha256")
    assert len(results) == 1
    assert results[0].passed is False


# --- compare_remote_sizes ---


def test_compare_remote_sizes_match(tmp_path):
    f = tmp_path / "vzdump-qemu-100-2026_01_01-00_00_00.vma.gz"
    f.write_bytes(b"x" * 1024)

    remote_json = json.dumps([{"Name": f.name, "Size": 1024}])
    with patch("proxmox_backup_verifier.verify.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = remote_json
        results = compare_remote_sizes("remote:path", tmp_path)

    assert len(results) == 1
    assert results[0].passed is True


def test_compare_remote_sizes_mismatch(tmp_path):
    f = tmp_path / "vzdump-qemu-100-2026_01_01-00_00_00.vma.gz"
    f.write_bytes(b"x" * 512)

    remote_json = json.dumps([{"Name": f.name, "Size": 1024}])
    with patch("proxmox_backup_verifier.verify.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = remote_json
        results = compare_remote_sizes("remote:path", tmp_path)

    assert len(results) == 1
    assert results[0].passed is False


def test_compare_remote_sizes_missing_locally(tmp_path):
    remote_json = json.dumps([{"Name": "vzdump-qemu-100-2026_01_01-00_00_00.vma.gz", "Size": 1024}])
    with patch("proxmox_backup_verifier.verify.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = remote_json
        results = compare_remote_sizes("remote:path", tmp_path)

    assert any("missing locally" in r.detail.lower() for r in results)


def test_compare_remote_sizes_rclone_error(tmp_path):
    with patch("proxmox_backup_verifier.verify.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "connection failed"
        results = compare_remote_sizes("remote:path", tmp_path)

    assert len(results) == 1
    assert results[0].passed is False


# --- _human_size ---


def test_human_size_bytes():
    assert _human_size(500) == "500.0 B"


def test_human_size_gb():
    assert _human_size(1024 ** 3 * 7.9) == "7.9 GB"


def test_human_size_tb():
    assert _human_size(1024 ** 4 * 2) == "2.0 TB"
