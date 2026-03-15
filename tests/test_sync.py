"""Tests for glsa.sync - sync plan computation logic."""
from unittest.mock import MagicMock, patch

import pytest

from glsa.sync import SyncPlan, compute_sync_plan

_PATCH = "glsa.sync"


def _make_config(**kwargs):
    """Return a minimal mock Config."""
    cfg = MagicMock()
    cfg.data_dir = "/fake/data"
    cfg.cache_dir = "/fake/cache"
    cfg.backloggd_username = "testuser"
    cfg.steam_api_key = "fakekey"
    cfg.steam_id = "76561198000000000"
    cfg.match_threshold = 85
    for k, v in kwargs.items():
        setattr(cfg, k, v)
    return cfg


def _apps(*name_id_pairs: tuple[str, int]) -> dict[str, list[int]]:
    return {name: [id_] for name, id_ in name_id_pairs}


def _run_plan(
    *,
    wishlist_names=None,
    played_names=None,
    backlog_names=None,
    playing_names=None,
    steam_wishlist=None,
    steam_apps=None,
    overrides=None,
    keep_ids=None,
    match_threshold=85,
):
    """Run compute_sync_plan with all external I/O mocked out."""
    wishlist_names = wishlist_names or []
    played_names = played_names or []
    backlog_names = backlog_names or []
    playing_names = playing_names or []
    steam_wishlist = steam_wishlist or {}
    steam_apps = steam_apps or {}
    overrides = overrides or {}
    keep_ids = keep_ids or set()

    def fake_backloggd(username, status):
        mapping = {
            "wishlist": wishlist_names,
            "played": played_names,
            "backlog": backlog_names,
            "playing": playing_names,
        }
        return mapping.get(status, [])

    config = _make_config(match_threshold=match_threshold)

    with (
        patch(f"{_PATCH}.load_overrides", return_value=overrides),
        patch(f"{_PATCH}.load_keep_list", return_value=keep_ids),
        patch(f"{_PATCH}.get_backloggd_games", side_effect=fake_backloggd),
        patch(f"{_PATCH}.get_steam_wishlist", return_value=steam_wishlist),
        patch(f"{_PATCH}.get_steam_app_list", return_value=steam_apps),
        patch(f"{_PATCH}.fill_wishlist_names", side_effect=lambda w, _: w),
    ):
        return compute_sync_plan(config, interactive=False)


# ---------------------------------------------------------------------------
# to_add
# ---------------------------------------------------------------------------

class TestToAdd:
    def test_backloggd_wishlist_game_not_on_steam_queued_to_add(self):
        plan = _run_plan(
            wishlist_names=["Celeste"],
            steam_wishlist={},
            steam_apps=_apps(("celeste", 504230)),
        )
        assert len(plan.to_add) == 1
        assert plan.to_add[0].steam_app_id == 504230

    def test_multiple_games_all_added(self):
        plan = _run_plan(
            wishlist_names=["Celeste", "Hollow Knight"],
            steam_wishlist={},
            steam_apps=_apps(("celeste", 504230), ("hollow knight", 367520)),
        )
        added_ids = {m.steam_app_id for m in plan.to_add}
        assert added_ids == {504230, 367520}

    def test_unmatched_game_not_in_to_add(self):
        plan = _run_plan(
            wishlist_names=["Obscure Game XYZ 9999"],
            steam_apps=_apps(("completely different", 1)),
        )
        assert len(plan.to_add) == 0

    def test_skipped_game_not_in_to_add(self):
        plan = _run_plan(
            wishlist_names=["Not On Steam"],
            steam_apps=_apps(("not on steam", 999)),
            overrides={"Not On Steam": None},
        )
        assert len(plan.to_add) == 0


# ---------------------------------------------------------------------------
# already_synced
# ---------------------------------------------------------------------------

class TestAlreadySynced:
    def test_game_on_both_wishlists_is_already_synced(self):
        plan = _run_plan(
            wishlist_names=["Celeste"],
            steam_wishlist={504230: "celeste"},
            steam_apps=_apps(("celeste", 504230)),
        )
        assert len(plan.to_add) == 0
        assert len(plan.already_synced) == 1
        assert plan.already_synced[0].steam_app_id == 504230

    def test_already_synced_not_double_counted_in_to_add(self):
        plan = _run_plan(
            wishlist_names=["Celeste"],
            steam_wishlist={504230: "celeste"},
            steam_apps=_apps(("celeste", 504230)),
        )
        assert len(plan.to_add) == 0


# ---------------------------------------------------------------------------
# to_remove
# ---------------------------------------------------------------------------

class TestToRemove:
    def test_done_game_on_steam_wishlist_queued_to_remove(self):
        plan = _run_plan(
            played_names=["Celeste"],
            steam_wishlist={504230: "celeste"},
            steam_apps=_apps(("celeste", 504230)),
        )
        assert len(plan.to_remove) == 1
        assert plan.to_remove[0].steam_app_id == 504230

    def test_backlog_status_also_triggers_remove(self):
        plan = _run_plan(
            backlog_names=["Celeste"],
            steam_wishlist={504230: "celeste"},
            steam_apps=_apps(("celeste", 504230)),
        )
        assert len(plan.to_remove) == 1

    def test_playing_status_also_triggers_remove(self):
        plan = _run_plan(
            playing_names=["Celeste"],
            steam_wishlist={504230: "celeste"},
            steam_apps=_apps(("celeste", 504230)),
        )
        assert len(plan.to_remove) == 1

    def test_game_not_on_steam_wishlist_not_removed(self):
        # Done on Backloggd but NOT on Steam wishlist → nothing to remove
        plan = _run_plan(
            played_names=["Celeste"],
            steam_wishlist={},
            steam_apps=_apps(("celeste", 504230)),
        )
        assert len(plan.to_remove) == 0

    def test_multiple_done_games_all_removed(self):
        plan = _run_plan(
            played_names=["Celeste", "Hollow Knight"],
            steam_wishlist={504230: "celeste", 367520: "hollow knight"},
            steam_apps=_apps(("celeste", 504230), ("hollow knight", 367520)),
        )
        assert len(plan.to_remove) == 2


# ---------------------------------------------------------------------------
# keep list
# ---------------------------------------------------------------------------

class TestKeepList:
    def test_keep_list_protects_from_removal(self):
        plan = _run_plan(
            played_names=["Celeste"],
            steam_wishlist={504230: "celeste"},
            steam_apps=_apps(("celeste", 504230)),
            keep_ids={504230},
        )
        assert len(plan.to_remove) == 0
        assert len(plan.kept) == 1
        assert plan.kept[0].steam_app_id == 504230

    def test_keep_list_only_protects_matching_id(self):
        plan = _run_plan(
            played_names=["Celeste", "Hollow Knight"],
            steam_wishlist={504230: "celeste", 367520: "hollow knight"},
            steam_apps=_apps(("celeste", 504230), ("hollow knight", 367520)),
            keep_ids={504230},  # Only protect Celeste
        )
        assert len(plan.to_remove) == 1
        assert plan.to_remove[0].steam_app_id == 367520
        assert len(plan.kept) == 1
        assert plan.kept[0].steam_app_id == 504230

    def test_keep_list_has_no_effect_on_to_add(self):
        plan = _run_plan(
            wishlist_names=["Hollow Knight"],
            steam_wishlist={},
            steam_apps=_apps(("hollow knight", 367520)),
            keep_ids={367520},
        )
        assert len(plan.to_add) == 1
        assert plan.to_add[0].steam_app_id == 367520


# ---------------------------------------------------------------------------
# unmatched
# ---------------------------------------------------------------------------

class TestUnmatched:
    def test_unmatched_game_recorded(self):
        plan = _run_plan(
            wishlist_names=["Some Obscure Indie Game XYZ123"],
            steam_apps=_apps(("completely different title", 999)),
        )
        assert len(plan.unmatched) == 1
        assert plan.unmatched[0].backloggd_name == "Some Obscure Indie Game XYZ123"
        assert plan.unmatched[0].matched is False

    def test_unmatched_not_in_to_add(self):
        plan = _run_plan(
            wishlist_names=["XYZ Unmatched Game"],
            steam_apps={},
        )
        assert len(plan.to_add) == 0
        assert len(plan.unmatched) == 1


# ---------------------------------------------------------------------------
# skipped (null overrides)
# ---------------------------------------------------------------------------

class TestSkipped:
    def test_null_override_puts_game_in_skipped(self):
        plan = _run_plan(
            wishlist_names=["Not On Steam"],
            steam_apps=_apps(("not on steam", 999)),
            overrides={"Not On Steam": None},
        )
        assert len(plan.skipped) == 1
        assert plan.skipped[0].backloggd_name == "Not On Steam"

    def test_skipped_game_not_in_to_add_or_unmatched(self):
        plan = _run_plan(
            wishlist_names=["Not On Steam"],
            steam_apps=_apps(("not on steam", 999)),
            overrides={"Not On Steam": None},
        )
        assert len(plan.to_add) == 0
        assert len(plan.unmatched) == 0


# ---------------------------------------------------------------------------
# steam_only
# ---------------------------------------------------------------------------

class TestSteamOnly:
    def test_steam_game_not_on_backloggd_is_steam_only(self):
        plan = _run_plan(
            wishlist_names=[],
            played_names=[],
            steam_wishlist={504230: "celeste"},
            steam_apps=_apps(("celeste", 504230)),
        )
        assert len(plan.steam_only) == 1
        assert plan.steam_only[0].steam_app_id == 504230

    def test_unknown_games_excluded_from_steam_only(self):
        """Steam apps with 'Unknown (appid)' names (delisted/unreleased) are ignored."""
        plan = _run_plan(
            wishlist_names=[],
            steam_wishlist={999: "Unknown (999)"},
            steam_apps={},
        )
        assert len(plan.steam_only) == 0

    def test_game_matched_from_wishlist_not_in_steam_only(self):
        """A game already matched from Backloggd wishlist should not appear in steam_only."""
        plan = _run_plan(
            wishlist_names=["Celeste"],
            steam_wishlist={504230: "celeste"},
            steam_apps=_apps(("celeste", 504230)),
        )
        assert len(plan.steam_only) == 0
        assert len(plan.already_synced) == 1

    def test_game_matched_from_done_not_in_steam_only(self):
        """A game matched from Backloggd done list should not appear in steam_only."""
        plan = _run_plan(
            played_names=["Celeste"],
            steam_wishlist={504230: "celeste"},
            steam_apps=_apps(("celeste", 504230)),
        )
        assert len(plan.steam_only) == 0
        # It's in to_remove, not steam_only
        assert len(plan.to_remove) == 1

    def test_steam_only_fuzzy_suppresses_false_positive(self):
        """If a Steam game's name fuzzy-matches a Backloggd name (score ≥ 80),
        it should NOT appear in steam_only.

        Scenario: 'Celeste' is on Backloggd wishlist, but steam_apps is empty so
        fuzzy matching fails. The Steam wishlist also has 'Celeste'. The steam_only
        fallback fuzzy check sees 'celeste' vs 'celeste' = 100, so it suppresses it.
        """
        plan = _run_plan(
            wishlist_names=["Celeste"],
            steam_wishlist={504230: "Celeste"},
            steam_apps={},  # Empty → fuzzy match fails, so unmatched
        )
        assert len(plan.steam_only) == 0

    def test_steam_only_fuzzy_threshold_is_80(self):
        """Steam game with fuzzy score < 80 vs all Backloggd names → reported as steam_only."""
        plan = _run_plan(
            wishlist_names=["Hollow Knight"],
            steam_wishlist={504230: "Celeste"},
            steam_apps=_apps(("hollow knight", 367520)),
        )
        # "Celeste" vs "hollow knight" → low score → steam_only
        assert any(m.steam_app_id == 504230 for m in plan.steam_only)

    def test_steam_only_fuzzy_raw_names_compared(self):
        """The steam_only fuzzy check compares raw lowercased names (not normalized).
        'NieR: Automata Ver1.1a' vs 'nier: automata' scores below 80, so it IS steam_only.
        This is a known limitation — the check helps with minor name differences but not
        version suffixes. The proper fix for this case is an override entry.
        """
        plan = _run_plan(
            wishlist_names=["Nier: Automata"],
            steam_wishlist={524220: "NieR: Automata Ver1.1a"},
            steam_apps={},  # Empty → match fails → unmatched
        )
        # Raw score is below 80 due to "Ver1.1a" suffix → appears as steam_only
        assert any(m.steam_app_id == 524220 for m in plan.steam_only)

    def test_steam_only_includes_truly_untracked_games(self):
        """A Steam game completely unrelated to any Backloggd name is steam_only."""
        plan = _run_plan(
            wishlist_names=["Hollow Knight"],
            played_names=["Celeste"],
            steam_wishlist={
                367520: "Hollow Knight",  # in wishlist → already_synced or to_add
                99999: "Some Random Game",  # not tracked anywhere
            },
            steam_apps=_apps(("hollow knight", 367520)),
        )
        assert any(m.steam_app_id == 99999 for m in plan.steam_only)
        assert not any(m.steam_app_id == 367520 for m in plan.steam_only)


# ---------------------------------------------------------------------------
# Complex / integration-style scenarios
# ---------------------------------------------------------------------------

class TestComplexScenarios:
    def test_all_categories_populated(self):
        """Full scenario exercising every plan category simultaneously."""
        plan = _run_plan(
            wishlist_names=["Celeste", "Hollow Knight", "Obscure Game XYZ 9999"],
            played_names=["Portal 2"],
            steam_wishlist={
                504230: "celeste",     # already synced
                620: "portal 2",       # to_remove (played on Backloggd)
                99999: "Steam Only",   # steam_only (not tracked on Backloggd)
            },
            steam_apps=_apps(
                ("celeste", 504230),
                ("hollow knight", 367520),
                ("portal 2", 620),
            ),
        )
        # Celeste: on both wishlists → already_synced
        assert any(m.steam_app_id == 504230 for m in plan.already_synced)
        # Hollow Knight: on Backloggd wishlist, not on Steam wishlist → to_add
        assert any(m.steam_app_id == 367520 for m in plan.to_add)
        # Obscure Game: no Steam match → unmatched
        assert any(m.backloggd_name == "Obscure Game XYZ 9999" for m in plan.unmatched)
        # Portal 2: played on Backloggd + on Steam wishlist → to_remove
        assert any(m.steam_app_id == 620 for m in plan.to_remove)
        # "Steam Only": not tracked on Backloggd at all → steam_only
        assert any(m.steam_app_id == 99999 for m in plan.steam_only)

    def test_no_cross_contamination_between_categories(self):
        """Each game appears in exactly one plan category."""
        plan = _run_plan(
            wishlist_names=["Celeste", "Hollow Knight"],
            played_names=["Portal 2"],
            steam_wishlist={504230: "celeste", 620: "portal 2"},
            steam_apps=_apps(
                ("celeste", 504230),
                ("hollow knight", 367520),
                ("portal 2", 620),
            ),
        )
        all_ids = (
            [m.steam_app_id for m in plan.to_add]
            + [m.steam_app_id for m in plan.already_synced]
            + [m.steam_app_id for m in plan.to_remove]
            + [m.steam_app_id for m in plan.steam_only]
            + [m.steam_app_id for m in plan.kept]
        )
        # No duplicates
        assert len(all_ids) == len(set(all_ids))

    def test_sequel_not_incorrectly_added_from_wishlist(self):
        """'Monument Valley 3' on Backloggd wishlist, only MV1/MV2 on Steam → unmatched."""
        plan = _run_plan(
            wishlist_names=["Monument Valley 3"],
            steam_apps=_apps(
                ("monument valley", 1014630),
                ("monument valley 2", 1049800),
            ),
        )
        assert len(plan.unmatched) == 1
        assert len(plan.to_add) == 0

    def test_done_list_deduplication(self):
        """Same game appearing in multiple done statuses counts once for to_remove."""
        plan = _run_plan(
            played_names=["Celeste"],
            backlog_names=["Celeste"],  # also in backlog (shouldn't double-count removal)
            steam_wishlist={504230: "celeste"},
            steam_apps=_apps(("celeste", 504230)),
        )
        assert len(plan.to_remove) == 1

    def test_empty_everything_produces_empty_plan(self):
        plan = _run_plan()
        assert plan.to_add == []
        assert plan.to_remove == []
        assert plan.unmatched == []
        assert plan.already_synced == []
        assert plan.skipped == []
        assert plan.kept == []
        assert plan.steam_only == []
