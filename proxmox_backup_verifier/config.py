from pathlib import Path
import json

DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.json"

DEFAULT_CONFIG = {
    "rclone_remote": "",
    "local_backup_dir": "",
    "checksum_file": "checksums.sha256",
    "file_patterns": ["*.vma.gz", "*.log", "*.vma.gz.notes"],
}


def load_config(path=None):
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    if path.exists():
        with open(path) as f:
            user_config = json.load(f)
        return {**DEFAULT_CONFIG, **user_config}
    return dict(DEFAULT_CONFIG)


def save_config(config, path=None):
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(path, "w") as f:
        json.dump(config, f, indent=2)
