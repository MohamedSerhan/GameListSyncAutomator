"""Snapshot persistence for deletion propagation.

Tracks which games were confirmed on both platforms after each sync.
On subsequent syncs, games missing from one platform but present in
the snapshot are detected as deliberate removals and propagated.

File: ~/.glsa/snapshot.json
"""

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

SNAPSHOT_VERSION = 1


@dataclass
class GameEntry:
    steam_app_id: int
    steam_name: str
    backloggd_name: str


@dataclass
class Snapshot:
    version: int
    timestamp: str
    synced_games: dict[int, GameEntry]  # keyed by Steam app ID


def load_snapshot(data_dir: str) -> Snapshot | None:
    """Load snapshot.json. Returns None if missing or corrupt."""
    path = Path(data_dir) / "snapshot.json"
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        games: dict[int, GameEntry] = {}
        for app_id_str, entry in data["synced_games"].items():
            app_id = int(app_id_str)
            games[app_id] = GameEntry(
                steam_app_id=app_id,
                steam_name=entry["steam_name"],
                backloggd_name=entry["backloggd_name"],
            )
        return Snapshot(
            version=data.get("version", 1),
            timestamp=data.get("timestamp", ""),
            synced_games=games,
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError) as e:
        log.warning("Failed to load snapshot.json: %s — treating as first run", e)
        return None


def save_snapshot(data_dir: str, snapshot: Snapshot) -> None:
    """Write snapshot.json atomically (write to temp, then rename)."""
    path = Path(data_dir) / "snapshot.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "version": snapshot.version,
        "timestamp": snapshot.timestamp,
        "synced_games": {
            str(app_id): {
                "steam_name": entry.steam_name,
                "backloggd_name": entry.backloggd_name,
            }
            for app_id, entry in snapshot.synced_games.items()
        },
    }

    # Atomic write: temp file in same directory, then rename
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix="snapshot_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, str(path))
    except BaseException:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def build_snapshot_after_sync(
    previous: Snapshot | None,
    already_synced_ids: dict[int, tuple[str, str]],
    added_to_steam: dict[int, tuple[str, str]],
    added_to_backloggd: dict[int, tuple[str, str]],
    removed_from_steam: set[int],
    removed_from_backloggd: set[int],
) -> Snapshot:
    """Build the new snapshot reflecting post-sync state.

    Each dict maps app_id -> (steam_name, backloggd_name).
    Games that were successfully removed are excluded.
    Games that were already synced or successfully added are included.
    Games from the previous snapshot that weren't touched and are still
    on both platforms are carried forward.
    """
    games: dict[int, GameEntry] = {}

    # Carry forward previous snapshot entries that weren't removed
    all_removed = removed_from_steam | removed_from_backloggd
    if previous:
        for app_id, entry in previous.synced_games.items():
            if app_id not in all_removed:
                games[app_id] = entry

    # Add/update entries for games confirmed on both platforms
    for source in (already_synced_ids, added_to_steam, added_to_backloggd):
        for app_id, (steam_name, backloggd_name) in source.items():
            games[app_id] = GameEntry(
                steam_app_id=app_id,
                steam_name=steam_name,
                backloggd_name=backloggd_name,
            )

    return Snapshot(
        version=SNAPSHOT_VERSION,
        timestamp=datetime.now(timezone.utc).isoformat(),
        synced_games=games,
    )
