from dataclasses import dataclass, field
from pathlib import Path
import os
import sys

import click
from cryptography.fernet import InvalidToken

from .secure_config import config_exists, decrypt_config, get_config_path

# Default data dir used before config is loaded
DEFAULT_DATA_DIR = str(Path.home() / ".glsa")


@dataclass
class Config:
    backloggd_username: str
    backloggd_password: str
    steam_api_key: str
    steam_id: str
    browser_data_dir: str = field(default_factory=lambda: str(Path.home() / ".glsa" / "browser-data"))
    backloggd_browser_data_dir: str = field(default_factory=lambda: str(Path.home() / ".glsa" / "browser-data-backloggd"))
    cache_dir: str = field(default_factory=lambda: str(Path.home() / ".glsa" / "cache"))
    data_dir: str = field(default_factory=lambda: DEFAULT_DATA_DIR)
    match_threshold: int = 85
    rate_limit_delay: float = 2.0


def _get_master_password() -> str:
    """Get master password from env var or prompt."""
    pw = os.getenv("GLSA_MASTER_PASSWORD", "").strip()
    if pw:
        return pw
    return click.prompt("Master password", hide_input=True)


def load_config() -> Config:
    """Load configuration from the encrypted config file."""
    if not config_exists(DEFAULT_DATA_DIR):
        print("No config found. Run `glsa configure` to set up.", file=sys.stderr)
        raise SystemExit(1)
    return _load_encrypted_config()


def _load_encrypted_config() -> Config:
    """Load config from encrypted file."""
    config_path = get_config_path(DEFAULT_DATA_DIR)
    master_pw = _get_master_password()

    try:
        data = decrypt_config(master_pw, config_path)
    except InvalidToken:
        print("Wrong master password.", file=sys.stderr)
        raise SystemExit(1)

    missing = []
    for key in ("backloggd_username", "backloggd_password", "steam_api_key", "steam_id"):
        if not data.get(key):
            missing.append(key)

    if missing:
        print(f"Encrypted config is missing: {', '.join(missing)}", file=sys.stderr)
        print("Run `glsa configure` to set up your config.", file=sys.stderr)
        raise SystemExit(1)

    config = Config(
        backloggd_username=data["backloggd_username"],
        backloggd_password=data["backloggd_password"],
        steam_api_key=data["steam_api_key"],
        steam_id=data["steam_id"],
    )

    if val := data.get("match_threshold"):
        config.match_threshold = int(val)
    if val := data.get("rate_limit_delay"):
        config.rate_limit_delay = float(val)

    return config
