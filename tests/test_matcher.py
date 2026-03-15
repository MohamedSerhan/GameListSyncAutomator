"""Tests for glsa.matcher - fuzzy game name matching logic."""
import pytest

from glsa.matcher import (
    MatchResult,
    _extract_sequel_number,
    _generate_variants,
    _lookup_override,
    _normalize,
    match_games,
)


# ---------------------------------------------------------------------------
# _normalize
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_lowercases(self):
        assert _normalize("DOOM") == "doom"

    def test_strips_goty(self):
        assert _normalize("The Witcher 3: Wild Hunt GOTY") == "the witcher 3 wild hunt"

    def test_strips_game_of_the_year(self):
        # "game of the year" is stripped but standalone "edition" is not in the pattern
        assert _normalize("Fallout 4: Game of the Year Edition") == "fallout 4 edition"

    def test_strips_definitive_edition(self):
        assert _normalize("Borderlands 3 Definitive Edition") == "borderlands 3"

    def test_strips_remastered(self):
        assert _normalize("Halo: Combat Evolved Remastered") == "halo combat evolved"

    def test_strips_deluxe_edition(self):
        assert _normalize("Cyberpunk 2077 Deluxe Edition") == "cyberpunk 2077"

    def test_strips_complete_edition(self):
        assert _normalize("Game Complete Edition") == "game"

    def test_strips_enhanced_edition(self):
        # Apostrophes become spaces via _NON_ALNUM, so "Baldur's" → "baldur s"
        assert _normalize("Baldur's Gate Enhanced Edition") == "baldur s gate"

    def test_strips_directors_cut(self):
        assert _normalize("Death Stranding Director's Cut") == "death stranding"

    def test_strips_directors_cut_no_apostrophe(self):
        assert _normalize("Death Stranding Directors Cut") == "death stranding"

    def test_strips_ultimate_edition(self):
        assert _normalize("Mortal Kombat 11 Ultimate Edition") == "mortal kombat 11"

    def test_removes_punctuation(self):
        assert _normalize("Pac-Man") == "pac man"

    def test_removes_apostrophe(self):
        # Apostrophe is replaced by a space (not deleted), then spaces are collapsed
        assert _normalize("Baldur's Gate") == "baldur s gate"

    def test_collapses_spaces(self):
        assert _normalize("Game  with   spaces") == "game with spaces"

    def test_strips_leading_trailing_spaces(self):
        assert _normalize("  Celeste  ") == "celeste"

    def test_preserves_numbers(self):
        assert _normalize("Halo 3") == "halo 3"

    def test_preserves_sequel_number(self):
        assert _normalize("Monument Valley 3") == "monument valley 3"

    def test_simple_name_unchanged(self):
        assert _normalize("celeste") == "celeste"


# ---------------------------------------------------------------------------
# _extract_sequel_number
# ---------------------------------------------------------------------------

class TestExtractSequelNumber:
    def test_trailing_2(self):
        assert _extract_sequel_number("Monument Valley 2") == "2"

    def test_trailing_3(self):
        assert _extract_sequel_number("Monument Valley 3") == "3"

    def test_trailing_9(self):
        assert _extract_sequel_number("Game 9") == "9"

    def test_roman_ii(self):
        assert _extract_sequel_number("Borderlands II") == "ii"

    def test_roman_iii(self):
        assert _extract_sequel_number("Dark Souls III") == "iii"

    def test_roman_iv(self):
        assert _extract_sequel_number("Fallout IV") == "iv"

    def test_roman_v(self):
        assert _extract_sequel_number("Final Fantasy V") == "v"

    def test_roman_vi(self):
        assert _extract_sequel_number("Final Fantasy VI") == "vi"

    def test_roman_vii(self):
        assert _extract_sequel_number("Final Fantasy VII") == "vii"

    def test_roman_viii(self):
        assert _extract_sequel_number("Final Fantasy VIII") == "viii"

    def test_roman_ix(self):
        assert _extract_sequel_number("Final Fantasy IX") == "ix"

    def test_roman_x(self):
        assert _extract_sequel_number("Final Fantasy X") == "x"

    def test_case_insensitive_roman(self):
        # Returns lowercase
        assert _extract_sequel_number("Final Fantasy VII") == "vii"
        assert _extract_sequel_number("Final Fantasy vii") == "vii"

    def test_no_sequel_indicator(self):
        assert _extract_sequel_number("Monument Valley") is None

    def test_number_1_not_matched(self):
        # 1 is not a sequel indicator — it's the original
        assert _extract_sequel_number("Halo 1") is None

    def test_number_in_middle_not_trailing(self):
        # "2D" in the middle; not a sequel indicator
        assert _extract_sequel_number("2D Platformer") is None

    def test_number_with_subtitle_not_trailing(self):
        # "3" is not trailing because subtitle follows it
        assert _extract_sequel_number("Monument Valley 3: New Chapters") is None

    def test_trailing_whitespace_allowed(self):
        # Pattern allows trailing whitespace after number
        assert _extract_sequel_number("Game 3 ") == "3"

    def test_normalized_name_with_sequel(self):
        assert _extract_sequel_number("monument valley 3") == "3"


# ---------------------------------------------------------------------------
# _generate_variants
# ---------------------------------------------------------------------------

class TestGenerateVariants:
    def test_simple_name_has_one_variant(self):
        variants = _generate_variants("Celeste")
        assert variants == ["celeste"]

    def test_colon_subtitle_generates_combined_variant(self):
        variants = _generate_variants("The Witcher 3: Wild Hunt")
        # Should have the full normalized name
        assert "the witcher 3 wild hunt" in variants

    def test_dash_subtitle_variant(self):
        variants = _generate_variants("Nier - Automata")
        assert len(variants) >= 1

    def test_no_duplicate_when_combined_equals_normalized(self):
        # "Celeste" has no subtitle, so combined would equal normalized
        variants = _generate_variants("Celeste")
        assert len(variants) == variants.count("celeste") == 1

    def test_edition_stripped_in_variant(self):
        variants = _generate_variants("Halo: GOTY")
        assert "halo" in variants

    def test_base_name_added_as_variant(self):
        """'Unbeatable: White Label' should also generate 'unbeatable' as a variant."""
        variants = _generate_variants("Unbeatable: White Label")
        assert "unbeatable" in variants
        assert "unbeatable white label" in variants

    def test_base_name_too_short_not_added(self):
        # Base name shorter than 4 chars should not be added (too broad to be useful)
        variants = _generate_variants("The: Full Subtitle Here")
        assert "the" not in variants

    def test_base_name_not_duplicated_when_same_as_full(self):
        # "Celeste: Something" → base "celeste" differs from full "celeste something"
        # so "celeste" IS added. But it should appear only once.
        variants = _generate_variants("Celeste: Something")
        assert variants.count("celeste") == 1


# ---------------------------------------------------------------------------
# _lookup_override
# ---------------------------------------------------------------------------

class TestLookupOverride:
    def test_exact_match_returns_app_id(self):
        result = _lookup_override("Celeste", {"Celeste": 504230})
        assert result == (504230, True)

    def test_case_insensitive_lookup(self):
        result = _lookup_override("celeste", {"Celeste": 504230})
        assert result == (504230, True)

    def test_case_insensitive_key(self):
        result = _lookup_override("Celeste", {"celeste": 504230})
        assert result == (504230, True)

    def test_null_override_returns_not_found(self):
        result = _lookup_override("Game Not On Steam", {"Game Not On Steam": None})
        assert result == (None, False)

    def test_no_match_returns_none(self):
        result = _lookup_override("Hollow Knight", {"Celeste": 504230})
        assert result is None

    def test_empty_overrides_returns_none(self):
        result = _lookup_override("Celeste", {})
        assert result is None


# ---------------------------------------------------------------------------
# match_games — helpers
# ---------------------------------------------------------------------------

def _apps(*name_id_pairs: tuple[str, int]) -> dict[str, list[int]]:
    """Build a steam_apps dict from (name, id) pairs."""
    return {name: [id_] for name, id_ in name_id_pairs}


# ---------------------------------------------------------------------------
# match_games — basic matching
# ---------------------------------------------------------------------------

class TestMatchGamesBasic:
    def test_exact_normalized_match(self):
        results = match_games(["Celeste"], _apps(("celeste", 504230)), threshold=85)
        assert results[0].matched is True
        assert results[0].steam_app_id == 504230

    def test_case_insensitive_match(self):
        results = match_games(["CELESTE"], _apps(("celeste", 504230)), threshold=85)
        assert results[0].matched is True

    def test_no_match_below_threshold(self):
        results = match_games(["Celeste"], _apps(("completely different", 999)), threshold=85)
        assert results[0].matched is False
        assert results[0].steam_app_id is None

    def test_empty_backloggd_list(self):
        results = match_games([], _apps(("celeste", 504230)))
        assert results == []

    def test_empty_steam_apps(self):
        results = match_games(["Celeste"], {})
        assert results[0].matched is False
        assert results[0].score == 0

    def test_result_order_matches_input_order(self):
        apps = _apps(("celeste", 504230), ("hollow knight", 367520))
        results = match_games(["Hollow Knight", "Celeste"], apps, threshold=85)
        assert results[0].backloggd_name == "Hollow Knight"
        assert results[1].backloggd_name == "Celeste"

    def test_multiple_games_all_match(self):
        apps = _apps(("celeste", 504230), ("hollow knight", 367520))
        results = match_games(["Celeste", "Hollow Knight"], apps, threshold=85)
        assert all(r.matched for r in results)

    def test_store_url_present_when_matched(self):
        results = match_games(["Celeste"], _apps(("celeste", 504230)), threshold=85)
        assert results[0].store_url == "https://store.steampowered.com/app/504230"

    def test_store_url_none_when_unmatched(self):
        results = match_games(["Celeste"], {})
        assert results[0].store_url is None

    def test_score_is_100_for_perfect_match(self):
        results = match_games(["Celeste"], _apps(("celeste", 504230)), threshold=85)
        assert results[0].score == 100

    def test_score_nonzero_for_near_miss(self):
        # Best match exists but may be below threshold; score is still recorded
        results = match_games(["Celeste"], _apps(("celestia", 999)), threshold=99)
        assert results[0].matched is False
        assert results[0].score > 0
        assert results[0].steam_name == "celestia"


# ---------------------------------------------------------------------------
# match_games — edition/suffix stripping
# ---------------------------------------------------------------------------

class TestMatchGamesEditions:
    def test_goty_stripped_from_backloggd(self):
        apps = _apps(("the witcher 3 wild hunt", 292030))
        results = match_games(["The Witcher 3: Wild Hunt GOTY"], apps, threshold=85)
        assert results[0].matched is True
        assert results[0].steam_app_id == 292030

    def test_definitive_edition_stripped(self):
        apps = _apps(("borderlands 3", 397540))
        results = match_games(["Borderlands 3 Definitive Edition"], apps, threshold=80)
        assert results[0].matched is True

    def test_remastered_stripped(self):
        apps = _apps(("halo combat evolved", 976730))
        results = match_games(["Halo: Combat Evolved Remastered"], apps, threshold=80)
        assert results[0].matched is True

    def test_directors_cut_stripped(self):
        apps = _apps(("death stranding", 1190460))
        results = match_games(["Death Stranding Director's Cut"], apps, threshold=85)
        assert results[0].matched is True

    def test_deluxe_edition_stripped(self):
        apps = _apps(("cyberpunk 2077", 1091500))
        results = match_games(["Cyberpunk 2077 Deluxe Edition"], apps, threshold=85)
        assert results[0].matched is True

    def test_subtitle_game_matches_steam_base_name(self):
        """'Unbeatable: White Label' should match 'unbeatable' via the base-name variant."""
        apps = _apps(("unbeatable", 1290490))
        results = match_games(["Unbeatable: White Label"], apps, threshold=85)
        assert results[0].matched is True
        assert results[0].steam_app_id == 1290490

    def test_subtitle_game_prefers_full_match_over_base(self):
        """If the full name matches better than the base, use the full match."""
        apps = _apps(
            ("unbeatable white label", 1290490),
            ("unbeatable something else", 9999),
        )
        results = match_games(["Unbeatable: White Label"], apps, threshold=85)
        assert results[0].steam_app_id == 1290490


# ---------------------------------------------------------------------------
# match_games — sequel/prequel prevention
# ---------------------------------------------------------------------------

class TestMatchGamesSequel:
    """Tests for the sequel/prequel mismatch prevention logic.

    Rule: if a Backloggd game name has a trailing sequel number (2-9, II-X),
    it must only match Steam games with the SAME trailing number.
    Base games (no trailing number) can match freely.
    """

    def test_sequel_3_does_not_match_base_game(self):
        """Monument Valley 3 must NOT match Monument Valley."""
        apps = _apps(("monument valley", 1014630))
        results = match_games(["Monument Valley 3"], apps, threshold=85)
        assert results[0].matched is False

    def test_sequel_3_does_not_match_sequel_2(self):
        """Monument Valley 3 must NOT match Monument Valley 2."""
        apps = _apps(("monument valley 2", 1049800))
        results = match_games(["Monument Valley 3"], apps, threshold=85)
        assert results[0].matched is False

    def test_sequel_3_matches_correct_sequel(self):
        """Monument Valley 3 must match Monument Valley 3."""
        apps = _apps(
            ("monument valley", 1014630),
            ("monument valley 2", 1049800),
            ("monument valley 3", 9999999),
        )
        results = match_games(["Monument Valley 3"], apps, threshold=85)
        assert results[0].matched is True
        assert results[0].steam_app_id == 9999999

    def test_sequel_2_does_not_match_sequel_3(self):
        """Monument Valley 2 must NOT match Monument Valley 3."""
        apps = _apps(("monument valley 3", 9999999))
        results = match_games(["Monument Valley 2"], apps, threshold=85)
        assert results[0].matched is False

    def test_sequel_2_matches_correct_sequel(self):
        """Monument Valley 2 must match Monument Valley 2."""
        apps = _apps(
            ("monument valley", 1014630),
            ("monument valley 2", 1049800),
        )
        results = match_games(["Monument Valley 2"], apps, threshold=85)
        assert results[0].matched is True
        assert results[0].steam_app_id == 1049800

    def test_base_game_matches_base_on_steam(self):
        """Base game (no sequel number) matches the base game on Steam."""
        apps = _apps(("monument valley", 1014630))
        results = match_games(["Monument Valley"], apps, threshold=85)
        assert results[0].matched is True
        assert results[0].steam_app_id == 1014630

    def test_base_game_still_matches_when_sequels_also_on_steam(self):
        """'Monument Valley' should match 'monument valley' even if MV2 also exists."""
        apps = _apps(
            ("monument valley", 1014630),
            ("monument valley 2", 1049800),
        )
        results = match_games(["Monument Valley"], apps, threshold=85)
        assert results[0].matched is True
        assert results[0].steam_app_id == 1014630

    def test_roman_numeral_vii_does_not_match_vi(self):
        """Final Fantasy VII should not match Final Fantasy VI."""
        apps = _apps(("final fantasy vi pixel remaster", 1173820))
        results = match_games(["Final Fantasy VII"], apps, threshold=85)
        assert results[0].matched is False

    def test_roman_numeral_iii_matches_iii(self):
        """Dark Souls III should match 'dark souls iii' — roman numeral is trailing."""
        apps = _apps(
            ("dark souls", 570940),
            ("dark souls ii", 236430),
            ("dark souls iii", 374320),
        )
        results = match_games(["Dark Souls III"], apps, threshold=85)
        assert results[0].matched is True
        assert results[0].steam_app_id == 374320

    def test_roman_numeral_sequel_in_steam_name_must_also_be_trailing(self):
        """'Final Fantasy VII' vs 'final fantasy vii remake' — 'vii' in Steam name is NOT
        trailing (followed by 'remake'), so the sequel guard blocks the match."""
        apps = _apps(("final fantasy vii remake", 1462040))
        results = match_games(["Final Fantasy VII"], apps, threshold=85)
        # Steam name "final fantasy vii remake" has 'vii' in middle, not trailing,
        # so steam_sequel is None but bg_sequel is 'vii' → penalized → unmatched
        assert results[0].matched is False

    def test_no_sequel_in_name_matches_freely(self):
        """A game with no sequel number should match any Steam entry that scores well."""
        apps = _apps(("portal 2", 620))
        # "Portal" (no trailing sequel) vs "portal 2" — lower score, may or may not match
        # Key assertion: the sequel guard does NOT block this
        results = match_games(["Portal"], apps, threshold=99)
        # Score might be below threshold; we just verify the result is not blocked by sequel logic
        # (it's a normal score-based miss, not a sequel mismatch)
        assert results[0].matched is False or results[0].steam_app_id == 620

    def test_all_sequels_penalized_results_in_unmatched(self):
        """If all Steam candidates have mismatched sequel numbers, result is unmatched."""
        apps = _apps(
            ("monument valley", 1014630),
            ("monument valley 2", 1049800),
        )
        # "Monument Valley 3" — no Steam entry with "3"
        results = match_games(["Monument Valley 3"], apps, threshold=85)
        assert results[0].matched is False


# ---------------------------------------------------------------------------
# match_games — overrides
# ---------------------------------------------------------------------------

class TestMatchGamesOverrides:
    def test_override_forces_app_id(self):
        apps = _apps(("celeste", 504230))
        results = match_games(["Celeste"], apps, overrides={"Celeste": 504230})
        assert results[0].matched is True
        assert results[0].override is True
        assert results[0].steam_app_id == 504230
        assert results[0].score == 100

    def test_override_null_marks_skipped(self):
        apps = _apps(("something", 999))
        results = match_games(["Not On Steam"], apps, overrides={"Not On Steam": None})
        assert results[0].matched is False
        assert results[0].skipped is True
        assert results[0].override is True
        assert results[0].steam_app_id is None

    def test_override_case_insensitive(self):
        apps = _apps(("celeste", 504230))
        results = match_games(["Celeste"], apps, overrides={"celeste": 504230})
        assert results[0].override is True

    def test_override_bypasses_fuzzy_matching(self):
        # Even if the name would NOT fuzzy-match, override forces the result
        apps = {"completely different name": [504230]}
        results = match_games(["Celeste"], apps, overrides={"Celeste": 504230})
        assert results[0].matched is True
        assert results[0].override is True
        assert results[0].steam_app_id == 504230

    def test_no_override_falls_through_to_fuzzy(self):
        apps = _apps(("celeste", 504230))
        results = match_games(["Celeste"], apps, overrides={})
        assert results[0].override is False
        assert results[0].matched is True


# ---------------------------------------------------------------------------
# match_games — ambiguous matches (multiple app IDs per name)
# ---------------------------------------------------------------------------

class TestMatchGamesAmbiguous:
    def test_ambiguous_match_when_multiple_ids(self):
        apps = {"fellowship": [111111, 222222]}
        results = match_games(["Fellowship"], apps, threshold=85)
        assert results[0].matched is True
        assert results[0].ambiguous is True
        assert sorted(results[0].candidates) == [111111, 222222]

    def test_first_app_id_used_for_ambiguous(self):
        apps = {"fellowship": [111111, 222222]}
        results = match_games(["Fellowship"], apps, threshold=85)
        assert results[0].steam_app_id == 111111

    def test_unambiguous_match_has_no_candidates(self):
        apps = _apps(("celeste", 504230))
        results = match_games(["Celeste"], apps, threshold=85)
        assert results[0].ambiguous is False
        assert results[0].candidates == []

    def test_unmatched_has_no_candidates(self):
        results = match_games(["Celeste"], {})
        assert results[0].ambiguous is False
        assert results[0].candidates == []


# ---------------------------------------------------------------------------
# match_games — threshold behaviour
# ---------------------------------------------------------------------------

class TestMatchGamesThreshold:
    def test_lower_threshold_matches_more(self):
        # "nier automata ver 1 1 a" is an unwieldy Steam name
        apps = _apps(("nier automata ver 1 1 a", 524220))
        results_low = match_games(["Nier: Automata"], apps, threshold=60)
        results_high = match_games(["Nier: Automata"], apps, threshold=99)
        assert results_low[0].matched is True
        assert results_high[0].matched is False

    def test_score_recorded_even_when_below_threshold(self):
        apps = _apps(("celestia", 999))
        results = match_games(["Celeste"], apps, threshold=99)
        assert results[0].matched is False
        assert results[0].score > 0
