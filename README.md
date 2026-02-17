# 🔒 proxmox-backup-verifier

**Problem:** You sync Proxmox backups off-site via rclone, but have no way to verify they're intact — especially on macOS where Proxmox's native tools don't run.

**Solution:** A zero-dependency CLI that checksums, decompresses, and parses VMA headers natively on macOS. Catches corruption, truncated transfers, and broken archives before you need them.

## Features

- Sync backups from rclone remote (uses `rclone copy`, won't delete local files)
- Compare local file sizes against remote
- SHA-256 checksum generation and verification
- Gzip decompression integrity testing
- Native VMA header parsing (VM config, disk info, header MD5 validation)
- Docker-based full filesystem inspection via libguestfs

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://gitlab.com/cynium/proxmox-backup-verifier.git
cd proxmox-backup-verifier
uv sync
```

Configure `config.json` with your rclone remote and local backup directory:

```json
{
  "rclone_remote": "myremote:path/to/proxmox/dump",
  "local_backup_dir": "/path/to/local/backups",
  "checksum_file": "checksums.sha256"
}
```

Or run the interactive setup:

```bash
uv run proxmox-backup-verifier init
```

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

## Usage examples

### Check backup status

```
$ uv run proxmox-backup-verifier status

   VM  Name                         Backups        Latest        Size  Age
------------------------------------------------------------------------------------------
  100  db-01                              6    2026-02-06      7.9 GB  4d
  101  ingress-01                         6    2026-02-10      1.5 GB  today
  102  nextcloud-01                       6    2026-02-10     35.8 GB  today
  ...

16 VMs, 80 total backups, 448.8 GB on disk
```

Backups older than 14 days are flagged with `(!)`.

### Sync from remote

```bash
uv run proxmox-backup-verifier sync
```

Uses `rclone copy` so local-only files (e.g. older backups rotated off the remote) are preserved.

### Run full verification

```bash
# Generate checksums first (only needed once, or after each sync)
uv run proxmox-backup-verifier checksum

# Then run all checks
uv run proxmox-backup-verifier full
```

### Inspect VMA headers

Parses the VMA archive header natively on macOS. Shows VM configuration, disk layout, and validates the header MD5 — without needing Docker or Linux.

```bash
# Inspect latest backup of every VM
uv run proxmox-backup-verifier inspect

# Inspect a specific file
uv run proxmox-backup-verifier inspect vzdump-qemu-101-2026_02_10-05_00_01.vma.gz
```

Output includes VM config (CPU, memory, network, disks), creation timestamp, UUID, and header integrity.

## Docker: full filesystem inspection

For inspecting actual file contents inside a backup, a Dockerfile is included that builds an amd64 image with Proxmox's `vma` tool and `libguestfs`.

### Build the image (once)

```bash
docker build --platform linux/amd64 -t proxmox-tools .
```

### Extract and inspect a VM

```bash
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
    virt-cat -a /tmp/extracted/vm101/disk-drive-scsi0.raw /etc/os-release
    virt-df -a /tmp/extracted/vm101/disk-drive-scsi0.raw -h
    virt-ls -a /tmp/extracted/vm101/disk-drive-scsi0.raw /home/
  "

# Clean up when done
rm -rf /tmp/vma-extracted
```

Runs under Rosetta on Apple Silicon. Slower than native but fine for spot-checks.

## Recommended workflow

1. `sync` — pull latest backups from remote
2. `checksum` — generate SHA-256 hashes for new files
3. `full` — run all integrity checks (compare + verify + gzip-test)
4. `inspect` — spot-check VMA headers look correct
5. Docker inspection — occasionally verify actual filesystem contents

## License

MIT — see [LICENSE](LICENSE).
