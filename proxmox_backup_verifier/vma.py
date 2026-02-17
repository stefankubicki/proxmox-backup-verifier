"""Parse VMA (Virtual Machine Archive) headers without external tools."""

import gzip
import hashlib
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

VMA_MAGIC = b"VMA\x00"
VMA_HEADER_STRUCT_SIZE = 12288


@dataclass
class VMADevice:
    index: int
    name: str
    size: int

    @property
    def size_human(self):
        n = self.size
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} PB"


@dataclass
class VMAConfig:
    name: str
    data: str


@dataclass
class VMAHeader:
    version: int
    uuid: UUID
    ctime: datetime
    md5_valid: bool
    header_size: int
    devices: list[VMADevice] = field(default_factory=list)
    configs: list[VMAConfig] = field(default_factory=list)


def _read_blob(blob_buf: bytes, offset: int) -> bytes:
    if offset == 0 or offset >= len(blob_buf):
        return b""
    size = struct.unpack_from("<H", blob_buf, offset)[0]
    return blob_buf[offset + 2 : offset + 2 + size]


def parse_vma_header(path: Path) -> VMAHeader:
    with gzip.open(path, "rb") as f:
        magic = f.read(4)
        if magic != VMA_MAGIC:
            raise ValueError(f"Not a VMA file: magic={magic!r}")

        version = struct.unpack(">I", f.read(4))[0]
        uuid_bytes = f.read(16)
        ctime = struct.unpack(">q", f.read(8))[0]
        md5_stored = f.read(16)
        blob_offset = struct.unpack(">I", f.read(4))[0]
        blob_size = struct.unpack(">I", f.read(4))[0]
        header_size = struct.unpack(">I", f.read(4))[0]

        # Read the full header for MD5 validation and blob parsing
        f.seek(0)
        full_header = bytearray(f.read(header_size))

    # Validate MD5: zero out the md5sum field (offset 32, 16 bytes), compute
    md5_check = bytearray(full_header)
    md5_check[32:48] = b"\x00" * 16
    md5_computed = hashlib.md5(bytes(md5_check)).digest()
    md5_valid = md5_computed == md5_stored

    # Extract blob buffer
    blob_buf = bytes(full_header[blob_offset : blob_offset + blob_size])

    # Parse config entries (offset 2044: 256 x uint32 names, offset 3068: 256 x uint32 data)
    configs = []
    for i in range(256):
        name_ptr = struct.unpack_from(">I", full_header, 2044 + i * 4)[0]
        data_ptr = struct.unpack_from(">I", full_header, 3068 + i * 4)[0]
        if name_ptr == 0:
            continue
        name = _read_blob(blob_buf, name_ptr).rstrip(b"\x00").decode("utf-8", errors="replace")
        data = _read_blob(blob_buf, data_ptr).rstrip(b"\x00").decode("utf-8", errors="replace")
        configs.append(VMAConfig(name=name, data=data))

    # Parse device entries (offset 4096: 256 x 32-byte entries)
    devices = []
    for i in range(256):
        entry_offset = 4096 + i * 32
        devname_ptr = struct.unpack_from(">I", full_header, entry_offset)[0]
        dev_size = struct.unpack_from(">Q", full_header, entry_offset + 8)[0]
        if devname_ptr == 0 or dev_size == 0:
            continue
        name = _read_blob(blob_buf, devname_ptr).rstrip(b"\x00").decode("utf-8", errors="replace")
        devices.append(VMADevice(index=i, name=name, size=dev_size))

    return VMAHeader(
        version=version,
        uuid=UUID(bytes=uuid_bytes),
        ctime=datetime.fromtimestamp(ctime, tz=timezone.utc),
        md5_valid=md5_valid,
        header_size=header_size,
        devices=devices,
        configs=configs,
    )
