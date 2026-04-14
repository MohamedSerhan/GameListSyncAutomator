"""Tests for glsa.snapshot - snapshot persistence and building."""
import json
import os
from pathlib import Path

import pytest

from glsa.snapshot import (
    GameEntry,
    Snapshot,
    build_snapshot_after_sync,
    load_snapshot,
    save_snapshot,
)


@pytest.fixture
def tmp_data_dir(tmp_path):
    return str(tmp_path)


class TestLoadSnapshot:
    def test_returns_none_when_missing(self, tmp_data_dir):
        assert load_snapshot(tmp_data_dir) is None

    def test_returns_none_on_corrupt_json(self, tmp_data_dir):
        path = Path(tmp_data_dir) / "snapshot.json"
        path.write_text("not json at all")
        assert load_snapshot(tmp_data_dir) is None

    def test_returns_none_on_bad_structure(self, tmp_data_dir):
        path = Path(tmp_data_dir) / "snapshot.json"
        path.write_text(json.dumps({"version": 1}))  # missing synced_games
        assert load_snapshot(tmp_data_dir) is None

    def test_loads_valid_snapshot(self, tmp_data_dir):
        data = {
            "version": 1,
            "timestamp": "2026-04-01T00:00:00+00:00",
            "synced_games": {
                "504230": {"steam_name": "Celeste", "backloggd_name": "Celeste"},
                "367520": {"steam_name": "Hollow Knight", "backloggd_name": "Hollow Knight"},
            },
        }
        path = Path(tmp_data_dir) / "snapshot.json"
        path.write_text(json.dumps(data))

        snap = load_snapshot(tmp_data_dir)
        assert snap is not None
        assert len(snap.synced_games) == 2
        assert 504230 in snap.synced_games
        assert snap.synced_games[504230].steam_name == "Celeste"


class TestSaveSnapshot:
    def test_save_and_reload(self, tmp_data_dir):
        snap = Snapshot(
            version=1,
            timestamp="2026-04-01T00:00:00+00:00",
            synced_games={
                504230: GameEntry(504230, "Celeste", "Celeste"),
            },
        )
        save_snapshot(tmp_data_dir, snap)

        loaded = load_snapshot(tmp_data_dir)
        assert loaded is not None
        assert len(loaded.synced_games) == 1
        assert loaded.synced_games[504230].steam_name == "Celeste"

    def test_save_creates_directory(self, tmp_path):
        nested = str(tmp_path / "a" / "b")
        snap = Snapshot(version=1, timestamp="t", synced_games={})
        save_snapshot(nested, snap)
        assert (Path(nested) / "snapshot.json").exists()


class TestBuildSnapshotAfterSync:
    def test_already_synced_included(self):
        snap = build_snapshot_after_sync(
            previous=None,
            already_synced_ids={504230: ("Celeste", "Celeste")},
            added_to_steam={},
            added_to_backloggd={},
            removed_from_steam=set(),
            removed_from_backloggd=set(),
        )
        assert 504230 in snap.synced_games

    def test_added_games_included(self):
        snap = build_snapshot_after_sync(
            previous=None,
            already_synced_ids={},
            added_to_steam={504230: ("Celeste", "Celeste")},
            added_to_backloggd={367520: ("Hollow Knight", "Hollow Knight")},
            removed_from_steam=set(),
            removed_from_backloggd=set(),
        )
        assert 504230 in snap.synced_games
        assert 367520 in snap.synced_games

    def test_removed_games_excluded(self):
        previous = Snapshot(
            version=1, timestamp="t",
            synced_games={504230: GameEntry(504230, "Celeste", "Celeste")},
        )
        snap = build_snapshot_after_sync(
            previous=previous,
            already_synced_ids={},
            added_to_steam={},
            added_to_backloggd={},
            removed_from_steam={504230},
            removed_from_backloggd=set(),
        )
        assert 504230 not in snap.synced_games

    def test_previous_entries_carried_forward(self):
        previous = Snapshot(
            version=1, timestamp="t",
            synced_games={
                504230: GameEntry(504230, "Celeste", "Celeste"),
                367520: GameEntry(367520, "Hollow Knight", "Hollow Knight"),
            },
        )
        snap = build_snapshot_after_sync(
            previous=previous,
            already_synced_ids={},
            added_to_steam={},
            added_to_backloggd={},
            removed_from_steam={504230},
            removed_from_backloggd=set(),
        )
        assert 504230 not in snap.synced_games
        assert 367520 in snap.synced_games
