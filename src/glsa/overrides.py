"""User-editable overrides for game matching and sync behaviour.

Files live in ~/.glsa/ and are plain JSON so users can edit them by hand.

overrides.json — manual match corrections:
  {
    "Exodus": 3230960,           // force specific Steam app ID
    "Orbitals": null,            // skip — not on Steam
    "TR-49": 3838370             // fix bad fuzzy match
  }

keep.json — games to never remove from Steam wishlist:
  [1601580, 2791510]             // app IDs to keep even if in played/backlog
"""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to load %s: %s", path, e)
        return default


def load_overrides(data_dir: str) -> dict[str, int | None]:
    """Load overrides.json: {game_name: app_id | null}."""
    return _load_json(Path(data_dir) / "overrides.json", {})


def load_keep_list(data_dir: str) -> set[int]:
    """Load keep.json: list of app IDs to never remove."""
    items = _load_json(Path(data_dir) / "keep.json", [])
    return {int(x) for x in items}


def save_overrides(data_dir: str, overrides: dict[str, int | None]) -> None:
    path = Path(data_dir) / "overrides.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(overrides, f, indent=2, ensure_ascii=False)


def save_keep_list(data_dir: str, keep: set[int]) -> None:
    path = Path(data_dir) / "keep.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(sorted(keep), f, indent=2)
