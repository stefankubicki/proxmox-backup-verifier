import hashlib
import gzip
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class BackupFile:
    path: Path
    vm_id: str
    timestamp: datetime
    size: int
    kind: str  # "vma.gz", "log", "notes"

    @property
    def name(self):
        return self.path.name


@dataclass
class VMBackup:
    vm_id: str
    vm_name: str
    backups: list[BackupFile] = field(default_factory=list)

    @property
    def latest(self):
        vma_files = [b for b in self.backups if b.kind == "vma.gz"]
        return max(vma_files, key=lambda b: b.timestamp) if vma_files else None

    @property
    def backup_count(self):
        return len([b for b in self.backups if b.kind == "vma.gz"])


@dataclass
class VerifyResult:
    file: str
    check: str
    passed: bool
    detail: str = ""


FILENAME_RE = re.compile(
    r"vzdump-qemu-(\d+)-(\d{4}_\d{2}_\d{2}-\d{2}_\d{2}_\d{2})\.(vma\.gz|log|vma\.gz\.notes)$"
)


def parse_backup_filename(path: Path) -> BackupFile | None:
    m = FILENAME_RE.search(path.name)
    if not m:
        return None
    vm_id = m.group(1)
    ts = datetime.strptime(m.group(2), "%Y_%m_%d-%H_%M_%S")
    kind = m.group(3)
    size = path.stat().st_size if path.exists() else 0
    return BackupFile(path=path, vm_id=vm_id, timestamp=ts, size=size, kind=kind)


def discover_backups(backup_dir: Path) -> dict[str, VMBackup]:
    vms: dict[str, VMBackup] = {}
    for f in sorted(backup_dir.iterdir()):
        bf = parse_backup_filename(f)
        if bf is None:
            continue
        if bf.vm_id not in vms:
            vm_name = _read_vm_name(backup_dir, bf.vm_id)
            vms[bf.vm_id] = VMBackup(vm_id=bf.vm_id, vm_name=vm_name)
        vms[bf.vm_id].backups.append(bf)
    return dict(sorted(vms.items(), key=lambda kv: int(kv[0])))


def _read_vm_name(backup_dir: Path, vm_id: str) -> str:
    notes = sorted(backup_dir.glob(f"vzdump-qemu-{vm_id}-*.vma.gz.notes"))
    if notes:
        try:
            return notes[-1].read_text().strip()
        except Exception:
            pass
    return f"VM {vm_id}"


def verify_gzip_integrity(path: Path) -> VerifyResult:
    """Test gzip file by reading through it — catches truncation and corruption."""
    try:
        buf = bytearray(1024 * 1024)  # 1MB buffer
        with gzip.open(path, "rb") as f:
            while f.readinto(buf):
                pass
        return VerifyResult(file=path.name, check="gzip", passed=True, detail="OK")
    except Exception as e:
        return VerifyResult(file=path.name, check="gzip", passed=False, detail=str(e))


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024 * 8):
            h.update(chunk)
    return h.hexdigest()


def generate_checksums(backup_dir: Path, checksum_file: Path) -> list[tuple[str, str]]:
    entries = []
    for f in sorted(backup_dir.iterdir()):
        if f.name.endswith(".vma.gz"):
            sha = compute_sha256(f)
            entries.append((sha, f.name))
    with open(checksum_file, "w") as out:
        for sha, name in entries:
            out.write(f"{sha}  {name}\n")
    return entries


def verify_checksums(backup_dir: Path, checksum_file: Path) -> list[VerifyResult]:
    results = []
    if not checksum_file.exists():
        return [VerifyResult(file=str(checksum_file), check="checksum", passed=False,
                             detail="Checksum file not found. Run 'checksum' first.")]
    with open(checksum_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            expected_sha, name = line.split("  ", 1)
            fpath = backup_dir / name
            if not fpath.exists():
                results.append(VerifyResult(file=name, check="checksum", passed=False,
                                            detail="File missing from local backup"))
                continue
            actual_sha = compute_sha256(fpath)
            if actual_sha == expected_sha:
                results.append(VerifyResult(file=name, check="checksum", passed=True, detail="OK"))
            else:
                results.append(VerifyResult(file=name, check="checksum", passed=False,
                                            detail=f"Expected {expected_sha[:16]}… got {actual_sha[:16]}…"))
    return results


def compare_remote_sizes(rclone_remote: str, backup_dir: Path) -> list[VerifyResult]:
    """Compare local file sizes against rclone remote listing."""
    results = []
    try:
        proc = subprocess.run(
            ["rclone", "lsjson", rclone_remote],
            capture_output=True, text=True, timeout=120
        )
        if proc.returncode != 0:
            return [VerifyResult(file="rclone", check="size", passed=False,
                                 detail=f"rclone error: {proc.stderr.strip()}")]
        remote_files = {item["Name"]: item["Size"] for item in json.loads(proc.stdout)}
    except Exception as e:
        return [VerifyResult(file="rclone", check="size", passed=False, detail=str(e))]

    for f in sorted(backup_dir.iterdir()):
        if not f.name.endswith(".vma.gz"):
            continue
        if f.name not in remote_files:
            results.append(VerifyResult(file=f.name, check="size", passed=False,
                                        detail="Not found on remote"))
            continue
        local_size = f.stat().st_size
        remote_size = remote_files[f.name]
        if local_size == remote_size:
            results.append(VerifyResult(file=f.name, check="size", passed=True,
                                        detail=f"{_human_size(local_size)}"))
        else:
            results.append(VerifyResult(
                file=f.name, check="size", passed=False,
                detail=f"Local {_human_size(local_size)} vs remote {_human_size(remote_size)}"
            ))

    # Check for files on remote but missing locally
    local_names = {f.name for f in backup_dir.iterdir()}
    for name, size in remote_files.items():
        if name.endswith(".vma.gz") and name not in local_names:
            results.append(VerifyResult(file=name, check="size", passed=False,
                                        detail=f"Missing locally ({_human_size(size)} on remote)"))

    return results


def _human_size(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if nbytes < 1024:
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} PB"
