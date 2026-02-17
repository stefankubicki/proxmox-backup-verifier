import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .config import load_config, save_config
from .verify import (
    discover_backups,
    verify_gzip_integrity,
    generate_checksums,
    verify_checksums,
    compare_remote_sizes,
    VerifyResult,
    _human_size,
)


def print_results(results: list[VerifyResult]):
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    for r in results:
        icon = "PASS" if r.passed else "FAIL"
        print(f"  [{icon}] {r.file}: {r.detail}")
    print(f"\n  {passed} passed, {failed} failed")
    return failed == 0


def cmd_status(config):
    backup_dir = Path(config["local_backup_dir"])
    if not backup_dir.exists():
        print(f"Error: local_backup_dir '{backup_dir}' does not exist")
        sys.exit(1)

    vms = discover_backups(backup_dir)
    if not vms:
        print("No backup files found.")
        return

    now = datetime.now()
    total_size = 0

    print(f"{'VM':>5}  {'Name':<28} {'Backups':>7}  {'Latest':>12}  {'Size':>10}  {'Age'}")
    print("-" * 90)
    for vm_id, vm in vms.items():
        latest = vm.latest
        if latest:
            age = now - latest.timestamp
            age_str = f"{age.days}d" if age.days > 0 else "today"
            if age > timedelta(days=14):
                age_str += " (!)"
            total_size += latest.size
            print(f"{vm_id:>5}  {vm.vm_name:<28} {vm.backup_count:>7}  "
                  f"{latest.timestamp.strftime('%Y-%m-%d'):>12}  "
                  f"{_human_size(latest.size):>10}  {age_str}")
        else:
            print(f"{vm_id:>5}  {vm.vm_name:<28} {vm.backup_count:>7}  {'N/A':>12}  {'N/A':>10}")

    total_vma = sum(
        b.size for vm in vms.values() for b in vm.backups if b.kind == "vma.gz"
    )
    print(f"\n{len(vms)} VMs, {sum(vm.backup_count for vm in vms.values())} total backups, "
          f"{_human_size(total_vma)} on disk")


def cmd_verify_gzip(config):
    backup_dir = Path(config["local_backup_dir"])
    vma_files = sorted(backup_dir.glob("*.vma.gz"))
    if not vma_files:
        print("No .vma.gz files found.")
        return

    print(f"Testing gzip integrity of {len(vma_files)} files...")
    print("(This reads every byte — will take a while for large files)\n")
    results = []
    for f in vma_files:
        print(f"  Checking {f.name} ({_human_size(f.stat().st_size)})...", end=" ", flush=True)
        r = verify_gzip_integrity(f)
        print("OK" if r.passed else f"FAIL: {r.detail}")
        results.append(r)

    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)


def cmd_checksum(config, verify_only=False):
    backup_dir = Path(config["local_backup_dir"])
    checksum_file = backup_dir / config["checksum_file"]

    if verify_only:
        print(f"Verifying checksums from {checksum_file}...\n")
        results = verify_checksums(backup_dir, checksum_file)
        if not print_results(results):
            sys.exit(1)
    else:
        print(f"Generating SHA-256 checksums for .vma.gz files...")
        print("(This reads every byte — will take a while)\n")
        entries = generate_checksums(backup_dir, checksum_file)
        for sha, name in entries:
            print(f"  {sha[:16]}…  {name}")
        print(f"\n{len(entries)} checksums written to {checksum_file}")


def cmd_compare(config):
    backup_dir = Path(config["local_backup_dir"])
    remote = config["rclone_remote"]
    print(f"Comparing local files against {remote}...\n")
    results = compare_remote_sizes(remote, backup_dir)
    if not print_results(results):
        sys.exit(1)


def cmd_sync(config):
    import subprocess
    remote = config["rclone_remote"]
    backup_dir = Path(config["local_backup_dir"])
    backup_dir.mkdir(parents=True, exist_ok=True)

    print(f"Copying {remote} -> {backup_dir}")
    print("(rclone copy — will not delete local files)\n")
    proc = subprocess.run(
        ["rclone", "copy", remote, str(backup_dir), "--progress", "--transfers", "2"],
    )
    if proc.returncode != 0:
        print(f"\nrclone sync failed with exit code {proc.returncode}")
        sys.exit(1)
    print("\nSync complete.")


def cmd_inspect(config, target=None):
    from .vma import parse_vma_header
    backup_dir = Path(config["local_backup_dir"])

    if target:
        files = [Path(target)] if Path(target).is_absolute() else [backup_dir / target]
    else:
        # Inspect latest backup of each VM
        vms = discover_backups(backup_dir)
        files = [vm.latest.path for vm in vms.values() if vm.latest]

    for path in files:
        if not path.exists():
            print(f"  File not found: {path}")
            continue
        print(f"--- {path.name} ---")
        try:
            hdr = parse_vma_header(path)
            print(f"  VMA version:  {hdr.version}")
            print(f"  UUID:         {hdr.uuid}")
            print(f"  Created:      {hdr.ctime.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            print(f"  Header MD5:   {'valid' if hdr.md5_valid else 'INVALID'}")
            print(f"  Header size:  {hdr.header_size} bytes")
            if hdr.devices:
                print(f"  Disks:")
                for d in hdr.devices:
                    print(f"    [{d.index}] {d.name}  {d.size_human}")
            if hdr.configs:
                for c in hdr.configs:
                    print(f"  Config ({c.name}):")
                    for line in c.data.splitlines():
                        print(f"    {line}")
        except Exception as e:
            print(f"  Error: {e}")
        print()


def cmd_init(config):
    local_dir = input(f"Local backup directory [{config['local_backup_dir'] or '(not set)'}]: ").strip()
    if local_dir:
        config["local_backup_dir"] = local_dir

    remote = input(f"Rclone remote [{config['rclone_remote']}]: ").strip()
    if remote:
        config["rclone_remote"] = remote

    save_config(config)
    print(f"\nConfig saved. Local dir: {config['local_backup_dir']}")


def main():
    parser = argparse.ArgumentParser(
        description="Verify Proxmox VMA backup integrity",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""commands:
  init       Configure local backup dir and rclone remote
  status     Show backup inventory (VMs, dates, sizes)
  sync       Sync backups from rclone remote to local dir
  compare    Compare local file sizes against rclone remote
  checksum   Generate SHA-256 checksums for .vma.gz files
  verify     Verify SHA-256 checksums against stored values
  gzip-test  Test gzip decompression integrity of all .vma.gz files
  inspect    Parse VMA headers (latest per VM, or specify a file)
  full       Run all checks: compare + verify + gzip-test
""",
    )
    parser.add_argument("command", choices=[
        "init", "status", "sync", "compare", "checksum", "verify",
        "gzip-test", "inspect", "full",
    ])
    parser.add_argument("-c", "--config", help="Path to config file")
    parser.add_argument("file", nargs="?", help="File to inspect (for inspect command)")

    args = parser.parse_args()
    config = load_config(args.config)

    if args.command == "init":
        cmd_init(config)
        return

    if not config["local_backup_dir"]:
        print("Error: local_backup_dir not set. Run 'proxmox-backup-verifier init' first.")
        sys.exit(1)

    match args.command:
        case "status":
            cmd_status(config)
        case "sync":
            cmd_sync(config)
        case "compare":
            cmd_compare(config)
        case "checksum":
            cmd_checksum(config)
        case "verify":
            cmd_checksum(config, verify_only=True)
        case "gzip-test":
            cmd_verify_gzip(config)
        case "inspect":
            cmd_inspect(config, args.file)
        case "full":
            print("=== Size comparison against remote ===\n")
            cmd_compare(config)
            print("\n=== SHA-256 checksum verification ===\n")
            cmd_checksum(config, verify_only=True)
            print("\n=== Gzip integrity test ===\n")
            cmd_verify_gzip(config)
