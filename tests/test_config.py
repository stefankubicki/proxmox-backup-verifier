import json
from pathlib import Path

from proxmox_backup_verifier.config import load_config, save_config, DEFAULT_CONFIG


def test_load_config_defaults(tmp_path):
    config = load_config(tmp_path / "nonexistent.json")
    assert config["rclone_remote"] == DEFAULT_CONFIG["rclone_remote"]
    assert config["checksum_file"] == "checksums.sha256"


def test_load_config_from_file(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "rclone_remote": "myremote:backups",
        "local_backup_dir": "/tmp/backups",
    }))
    config = load_config(cfg_path)
    assert config["rclone_remote"] == "myremote:backups"
    assert config["local_backup_dir"] == "/tmp/backups"
    # Defaults still present for unset keys
    assert config["checksum_file"] == "checksums.sha256"


def test_load_config_merges_with_defaults(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"local_backup_dir": "/data"}))
    config = load_config(cfg_path)
    assert config["local_backup_dir"] == "/data"
    assert "file_patterns" in config


def test_save_config(tmp_path):
    cfg_path = tmp_path / "config.json"
    save_config({"rclone_remote": "test:path", "local_backup_dir": "/tmp"}, cfg_path)
    assert cfg_path.exists()
    loaded = json.loads(cfg_path.read_text())
    assert loaded["rclone_remote"] == "test:path"


def test_save_and_reload(tmp_path):
    cfg_path = tmp_path / "config.json"
    original = {"rclone_remote": "r:p", "local_backup_dir": "/d", "checksum_file": "c.sha256"}
    save_config(original, cfg_path)
    reloaded = load_config(cfg_path)
    for key in original:
        assert reloaded[key] == original[key]
