import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

STORE_URL = "https://store.steampowered.com"


@dataclass
class MatchResult:
    backloggd_name: str
    steam_app_id: int | None
    steam_name: str | None
    score: int  # 0-100
    matched: bool
    override: bool = False   # True if match came from overrides.json
    skipped: bool = False    # True if overrides.json says null (not on Steam)
    ambiguous: bool = False  # True if multiple Steam games share this name
    candidates: list[int] = field(default_factory=list)  # All app IDs for ambiguous matches

    @property
    def store_url(self) -> str | None:
        if self.steam_app_id:
            return f"{STORE_URL}/app/{self.steam_app_id}"
        return None


# Suffixes/words to strip for better matching
_STRIP_PATTERNS = re.compile(
    r"\b(goty|game of the year|definitive edition|remastered|"
    r"deluxe edition|complete edition|enhanced edition|"
    r"directors cut|director's cut|ultimate edition)\b",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^\w\s]")
_MULTI_SPACE = re.compile(r"\s+")

# Pattern for "Base Game: DLC/Subtitle" or "Base Game - DLC/Subtitle"
_DLC_SPLIT = re.compile(r"\s*[:.\u2013\u2014-]\s+")


def _normalize(name: str) -> str:
    """Normalize a game name for fuzzy matching."""
    name = name.lower()
    name = _STRIP_PATTERNS.sub("", name)
    name = _NON_ALNUM.sub(" ", name)
    name = _MULTI_SPACE.sub(" ", name).strip()
    return name


def _generate_variants(name: str) -> list[str]:
    """Generate name variants for matching (full name, then progressively shorter)."""
    normalized = _normalize(name)
    variants = [normalized]

    # For "Game: Subtitle" patterns, also try the full combined form without separator
    parts = _DLC_SPLIT.split(name)
    if len(parts) >= 2:
        combined = _normalize(" ".join(parts))
        if combined != normalized:
            variants.append(combined)

    return variants


def match_games(
    backloggd_names: list[str],
    steam_apps: dict[str, list[int]],
    threshold: int = 85,
    overrides: dict[str, int | None] | None = None,
) -> list[MatchResult]:
    """Match Backloggd game names to Steam app IDs using fuzzy matching."""
    overrides = overrides or {}
    steam_names_list = list(steam_apps.keys())
    # Reverse map for looking up names by app ID
    id_to_name: dict[int, str] = {}
    for name, app_ids in steam_apps.items():
        for app_id in app_ids:
            id_to_name[app_id] = name

    results: list[MatchResult] = []
    for bg_name in backloggd_names:
        # Check overrides first (case-insensitive lookup)
        override_val = _lookup_override(bg_name, overrides)
        if override_val is not None:
            app_id, found = override_val
            if not found:
                results.append(MatchResult(
                    backloggd_name=bg_name,
                    steam_app_id=None,
                    steam_name=None,
                    score=0,
                    matched=False,
                    override=True,
                    skipped=True,
                ))
                continue
            else:
                results.append(MatchResult(
                    backloggd_name=bg_name,
                    steam_app_id=app_id,
                    steam_name=id_to_name.get(app_id, f"App {app_id}"),
                    score=100,
                    matched=True,
                    override=True,
                ))
                continue

        # Try fuzzy matching with name variants
        best_match = None
        best_score = 0
        for variant in _generate_variants(bg_name):
            match = process.extractOne(
                variant,
                steam_names_list,
                scorer=fuzz.token_sort_ratio,
                score_cutoff=0,
            )
            if match and match[1] > best_score:
                best_match = match
                best_score = match[1]

        if best_match and best_score >= threshold:
            steam_name = best_match[0]
            app_ids = steam_apps[steam_name]
            is_ambiguous = len(app_ids) > 1

            results.append(MatchResult(
                backloggd_name=bg_name,
                steam_app_id=app_ids[0],
                steam_name=steam_name,
                score=int(best_score),
                matched=True,
                ambiguous=is_ambiguous,
                candidates=app_ids if is_ambiguous else [],
            ))
        else:
            score = int(best_match[1]) if best_match else 0
            results.append(MatchResult(
                backloggd_name=bg_name,
                steam_app_id=None,
                steam_name=best_match[0] if best_match else None,
                score=score,
                matched=False,
            ))

    return results


def _lookup_override(
    name: str, overrides: dict[str, int | None]
) -> tuple[int | None, bool] | None:
    """Look up a name in overrides (case-insensitive). Returns (app_id, found) or None."""
    lower = name.lower()
    for key, val in overrides.items():
        if key.lower() == lower:
            if val is None:
                return (None, False)
            return (val, True)
    return None
