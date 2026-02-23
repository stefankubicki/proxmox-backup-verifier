# > proxmox-backup-verifier

A zero-dependency CLI that checksums, decompresses, and parses VMA headers natively on macOS. Catches corruption, truncated transfers, and broken archives before you need them.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- [rclone](https://rclone.org/) (for sync/compare commands)
- Docker (optional, for full filesystem inspection)

## Quick start

```bash
git clone https://gitlab.com/cynium/proxmox-backup-verifier.git
cd proxmox-backup-verifier
uv sync
uv run proxmox-backup-verifier init
```

The `init` command walks you through configuring your rclone remote and local backup directory. Or configure manually:

```json
{
  "rclone_remote": "myremote:path/to/proxmox/dump",
  "local_backup_dir": "/path/to/local/backups",
  "checksum_file": "checksums.sha256"
}
```

## How it works

```
rclone remote ──> sync ──> checksum ──> verify + gzip-test + inspect
```

Backups are pulled from an rclone remote with `rclone copy` (local-only files are preserved). SHA-256 checksums are generated once and verified on subsequent runs. Gzip decompression tests every byte for corruption. VMA header parsing validates archive structure, VM config, and header MD5 — all natively on macOS without Proxmox tools.

## Features

**Backup inventory** — `status` shows all VMs with backup counts, latest dates, sizes, and age warnings. Backups older than 14 days are flagged.

**Integrity pipeline** — `full` runs compare + verify + gzip-test in sequence. Catches size mismatches, checksum failures, and compression corruption.

**Native VMA parsing** — `inspect` reads VMA archive headers directly, showing VM configuration (CPU, memory, network, disks), creation timestamp, UUID, and header MD5 validation. No Docker or Linux required.

**Docker inspection** — Optional deep inspection via libguestfs. Extract VMA to raw disk images, inspect filesystems, read files, check disk usage. Runs under Rosetta on Apple Silicon.

## Commands

```
uv run proxmox-backup-verifier <command> [options]
```

| Command | Description |
|---|---|
| `init` | Interactive config setup |
| `status` | Show backup inventory (VMs, dates, sizes, age warnings) |
| `sync` | Copy backups from rclone remote to local dir |
| `compare` | Compare local file sizes against rclone remote |
| `checksum` | Generate SHA-256 checksums for all .vma.gz files |
| `verify` | Verify local files against stored SHA-256 checksums |
| `gzip-test` | Decompress every byte of all .vma.gz files to detect corruption |
| `inspect` | Parse VMA headers natively (no Docker required) |
| `full` | Run all checks: compare + verify + gzip-test |

Options:
- `-c`, `--config` — path to config file (default: `config.json` in project root)
- `file` — optional file argument for `inspect` (inspects latest per VM if omitted)

## Recommended workflow

1. `sync` — pull latest backups from remote
2. `checksum` — generate SHA-256 hashes for new files
3. `full` — run all integrity checks (compare + verify + gzip-test)
4. `inspect` — spot-check VMA headers look correct
5. Docker inspection — occasionally verify actual filesystem contents

## Docker: full filesystem inspection

For inspecting actual file contents inside a backup, a Dockerfile is included that builds an amd64 image with Proxmox's `vma` tool and `libguestfs`.

```bash
# Build the image (once)
docker build --platform linux/amd64 -t proxmox-tools .

# Extract VMA to raw disk image
docker run --platform linux/amd64 --rm \
  -v /path/to/local/backups:/backups:ro \
  -v /tmp/vma-extracted:/tmp/extracted \
  proxmox-tools -c \
  "zcat /backups/vzdump-qemu-101-2026_02_10-05_00_01.vma.gz | vma extract - /tmp/extracted/vm101"

# Inspect the filesystem
docker run --platform linux/amd64 --rm \
  -v /tmp/vma-extracted:/tmp/extracted:ro \
  proxmox-tools -c "
    virt-filesystems -a /tmp/extracted/vm101/disk-drive-scsi0.raw --long --parts --filesystems
    virt-cat -a /tmp/extracted/vm101/disk-drive-scsi0.raw /etc/hostname
    virt-df -a /tmp/extracted/vm101/disk-drive-scsi0.raw -h
  "

# Clean up when done
rm -rf /tmp/vma-extracted
```

Runs under Rosetta on Apple Silicon. Slower than native but fine for spot-checks.

## License

[MIT](LICENSE)

***
© 2026 [Stefan Kubicki](https://kubicki.org) • a [CYNIUM](https://cynium.com) release • shipped from the [Atoll](https://kubicki.org/atoll)
***
Canonical URL: https://forge.cynium.com/stefan/proxmox-backup-verifier
