"""Tests for VMA header parsing.

Creates minimal synthetic VMA headers to test the parser without needing
real Proxmox backup files.
"""

import gzip
import hashlib
import struct
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from proxmox_backup_verifier.vma import (
    VMA_MAGIC,
    VMA_HEADER_STRUCT_SIZE,
    VMADevice,
    VMAConfig,
    VMAHeader,
    _read_blob,
    parse_vma_header,
)


def _make_blob(data: bytes) -> bytes:
    """Create a blob entry: 2-byte little-endian size + data."""
    return struct.pack("<H", len(data)) + data


def _build_vma_header(
    version=1,
    uuid_bytes=None,
    ctime=1707580800,  # 2024-02-11 00:00:00 UTC
    configs=None,
    devices=None,
):
    """Build a minimal valid VMA header for testing."""
    if uuid_bytes is None:
        uuid_bytes = UUID("12345678-1234-5678-1234-567812345678").bytes
    if configs is None:
        configs = []
    if devices is None:
        devices = []

    # Build blob buffer - start with a null byte at offset 0
    blob_parts = [b"\x00"]
    blob_offsets = {}

    for label, entries in [("configs", configs), ("devices", devices)]:
        for i, entry in enumerate(entries):
            for key, value in entry.items():
                if isinstance(value, str):
                    blob_offsets[(label, i, key)] = len(b"".join(blob_parts))
                    blob_parts.append(_make_blob(value.encode() + b"\x00"))

    blob_buf = b"".join(blob_parts)

    # Header is VMA_HEADER_STRUCT_SIZE bytes
    # Layout:
    #   0-3: magic "VMA\0"
    #   4-7: version (big-endian uint32)
    #   8-23: UUID (16 bytes)
    #   24-31: ctime (big-endian int64)
    #   32-47: MD5 (16 bytes, zeroed for computation)
    #   48-51: blob_offset (big-endian uint32)
    #   52-55: blob_size (big-endian uint32)
    #   56-59: header_size (big-endian uint32)
    #
    #   2044: config name pointers (256 x uint32)
    #   3068: config data pointers (256 x uint32)
    #   4096: device entries (256 x 32 bytes)

    # We need a header large enough: at least 4096 + 256*32 = 12288 for devices
    # Plus blob buffer appended after
    header_fixed_size = VMA_HEADER_STRUCT_SIZE
    blob_offset = header_fixed_size
    header_size = header_fixed_size + len(blob_buf)

    header = bytearray(header_size)

    # Magic
    header[0:4] = VMA_MAGIC
    # Version
    struct.pack_into(">I", header, 4, version)
    # UUID
    header[8:24] = uuid_bytes
    # ctime
    struct.pack_into(">q", header, 24, ctime)
    # MD5 placeholder (zeroed)
    header[32:48] = b"\x00" * 16
    # blob_offset
    struct.pack_into(">I", header, 48, blob_offset)
    # blob_size
    struct.pack_into(">I", header, 52, len(blob_buf))
    # header_size
    struct.pack_into(">I", header, 56, header_size)

    # Config entries
    for i, cfg in enumerate(configs):
        if ("configs", i, "name") in blob_offsets:
            struct.pack_into(">I", header, 2044 + i * 4, blob_offsets[("configs", i, "name")])
        if ("configs", i, "data") in blob_offsets:
            struct.pack_into(">I", header, 3068 + i * 4, blob_offsets[("configs", i, "data")])

    # Device entries (32 bytes each at offset 4096)
    for i, dev in enumerate(devices):
        entry_offset = 4096 + i * 32
        if ("devices", i, "name") in blob_offsets:
            struct.pack_into(">I", header, entry_offset, blob_offsets[("devices", i, "name")])
        if "size" in dev:
            struct.pack_into(">Q", header, entry_offset + 8, dev["size"])

    # Copy blob buffer
    header[blob_offset:blob_offset + len(blob_buf)] = blob_buf

    # Compute MD5
    md5_input = bytearray(header)
    md5_input[32:48] = b"\x00" * 16
    md5 = hashlib.md5(bytes(md5_input)).digest()
    header[32:48] = md5

    return bytes(header)


def _write_vma_gz(tmp_path, header_bytes, name="test.vma.gz"):
    path = tmp_path / name
    with gzip.open(path, "wb") as f:
        f.write(header_bytes)
    return path


# --- _read_blob ---


def test_read_blob_zero_offset():
    assert _read_blob(b"\x00" * 10, 0) == b""


def test_read_blob_out_of_range():
    assert _read_blob(b"\x00" * 10, 100) == b""


def test_read_blob_valid():
    blob = b"\x00" + _make_blob(b"hello")
    assert _read_blob(blob, 1) == b"hello"


# --- parse_vma_header ---


def test_parse_minimal_header(tmp_path):
    header = _build_vma_header()
    path = _write_vma_gz(tmp_path, header)
    result = parse_vma_header(path)

    assert result.version == 1
    assert result.uuid == UUID("12345678-1234-5678-1234-567812345678")
    assert result.ctime == datetime.fromtimestamp(1707580800, tz=timezone.utc)
    assert result.md5_valid is True
    assert result.devices == []
    assert result.configs == []


def test_parse_header_with_config(tmp_path):
    header = _build_vma_header(configs=[
        {"name": "qemu-server.conf", "data": "memory: 4096\ncores: 2"},
    ])
    path = _write_vma_gz(tmp_path, header)
    result = parse_vma_header(path)

    assert len(result.configs) == 1
    assert result.configs[0].name == "qemu-server.conf"
    assert "memory: 4096" in result.configs[0].data


def test_parse_header_with_device(tmp_path):
    disk_size = 32 * 1024 * 1024 * 1024  # 32 GB
    header = _build_vma_header(devices=[
        {"name": "drive-scsi0", "size": disk_size},
    ])
    path = _write_vma_gz(tmp_path, header)
    result = parse_vma_header(path)

    assert len(result.devices) == 1
    assert result.devices[0].name == "drive-scsi0"
    assert result.devices[0].size == disk_size
    assert "32.0 GB" in result.devices[0].size_human


def test_parse_header_md5_validation(tmp_path):
    header = _build_vma_header()
    # Corrupt a byte in the header to invalidate MD5
    corrupted = bytearray(header)
    corrupted[60] = (corrupted[60] + 1) % 256
    path = _write_vma_gz(tmp_path, bytes(corrupted))
    result = parse_vma_header(path)

    assert result.md5_valid is False


def test_parse_not_a_vma_file(tmp_path):
    path = tmp_path / "not_vma.vma.gz"
    with gzip.open(path, "wb") as f:
        f.write(b"NOT A VMA FILE")

    with pytest.raises(ValueError, match="Not a VMA file"):
        parse_vma_header(path)


def test_parse_header_multiple_devices(tmp_path):
    header = _build_vma_header(devices=[
        {"name": "drive-scsi0", "size": 32 * 1024 ** 3},
        {"name": "drive-scsi1", "size": 64 * 1024 ** 3},
    ])
    path = _write_vma_gz(tmp_path, header)
    result = parse_vma_header(path)

    assert len(result.devices) == 2
    names = [d.name for d in result.devices]
    assert "drive-scsi0" in names
    assert "drive-scsi1" in names


# --- VMADevice.size_human ---


def test_device_size_human_gb():
    d = VMADevice(index=0, name="disk", size=10 * 1024 ** 3)
    assert d.size_human == "10.0 GB"


def test_device_size_human_mb():
    d = VMADevice(index=0, name="disk", size=512 * 1024 ** 2)
    assert d.size_human == "512.0 MB"
