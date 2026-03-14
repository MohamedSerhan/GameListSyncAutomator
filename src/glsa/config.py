from dataclasses import dataclass, field
from pathlib import Path
import os
import sys

from dotenv import load_dotenv


@dataclass
class Config:
    backloggd_username: str
    steam_api_key: str
    steam_id: str
    browser_data_dir: str = field(default_factory=lambda: str(Path.home() / ".glsa" / "browser-data"))
    backloggd_browser_data_dir: str = field(default_factory=lambda: str(Path.home() / ".glsa" / "browser-data-backloggd"))
    cache_dir: str = field(default_factory=lambda: str(Path.home() / ".glsa" / "cache"))
    data_dir: str = field(default_factory=lambda: str(Path.home() / ".glsa"))
    match_threshold: int = 85
    rate_limit_delay: float = 2.0


def load_config() -> Config:
    """Load configuration from .env file and environment variables."""
    load_dotenv()

    missing = []
    username = os.getenv("BACKLOGGD_USERNAME", "").strip()
    api_key = os.getenv("STEAM_API_KEY", "").strip()
    steam_id = os.getenv("STEAM_ID", "").strip()

    if not username:
        missing.append("BACKLOGGD_USERNAME")
    if not api_key:
        missing.append("STEAM_API_KEY")
    if not steam_id:
        missing.append("STEAM_ID")

    if missing:
        print(f"Missing required config: {', '.join(missing)}", file=sys.stderr)
        print("Copy .env.example to .env and fill in the values.", file=sys.stderr)
        raise SystemExit(1)

    config = Config(
        backloggd_username=username,
        steam_api_key=api_key,
        steam_id=steam_id,
    )

    # Override defaults from env if set
    if val := os.getenv("MATCH_THRESHOLD"):
        config.match_threshold = int(val)
    if val := os.getenv("RATE_LIMIT_DELAY"):
        config.rate_limit_delay = float(val)

    return config
