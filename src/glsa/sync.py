import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from .backloggd import get_backloggd_games
from .config import Config
from .matcher import STORE_URL, MatchResult, match_games
from .overrides import load_keep_list, load_overrides, save_overrides
from .steam_api import fill_wishlist_names, get_steam_app_list, get_steam_wishlist
from .steam_browser import add_to_wishlist, remove_from_wishlist

log = logging.getLogger(__name__)
console = Console()


@dataclass
class SyncPlan:
    to_add: list[MatchResult] = field(default_factory=list)
    to_remove: list[MatchResult] = field(default_factory=list)
    unmatched: list[MatchResult] = field(default_factory=list)
    already_synced: list[MatchResult] = field(default_factory=list)
    skipped: list[MatchResult] = field(default_factory=list)
    kept: list[MatchResult] = field(default_factory=list)
    steam_only: list[MatchResult] = field(default_factory=list)  # On Steam but not on Backloggd


def resolve_ambiguous_matches(
    matches: list[MatchResult],
    overrides: dict[str, int | None],
    data_dir: str,
) -> list[MatchResult]:
    """Interactively resolve ambiguous matches and save choices as overrides."""
    ambiguous = [m for m in matches if m.ambiguous and not m.override]
    if not ambiguous:
        return matches

    console.print(f"\n[bold yellow]Found {len(ambiguous)} ambiguous matches (multiple Steam games with same name):[/]\n")

    for m in ambiguous:
        console.print(f"  [bold]{m.backloggd_name}[/] matched to [cyan]{m.steam_name}[/] but there are {len(m.candidates)} games with this name:")
        for i, app_id in enumerate(m.candidates, 1):
            console.print(f"    {i}) {STORE_URL}/app/{app_id}")
        console.print(f"    s) Skip (not on Steam)")
        console.print()

        while True:
            choice = click.prompt(
                f"  Pick the correct one for '{m.backloggd_name}'",
                type=str,
                default="1",
            )
            if choice.lower() == "s":
                m.steam_app_id = None
                m.matched = False
                m.skipped = True
                m.ambiguous = False
                overrides[m.backloggd_name] = None
                break
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(m.candidates):
                    m.steam_app_id = m.candidates[idx]
                    m.ambiguous = False
                    overrides[m.backloggd_name] = m.candidates[idx]
                    break
            except ValueError:
                pass
            console.print(f"  [red]Invalid choice. Enter 1-{len(m.candidates)} or 's'[/]")

    # Save all choices so user never has to pick again
    save_overrides(data_dir, overrides)
    console.print(f"\n[dim]Saved {len(ambiguous)} choices to overrides.json[/]\n")

    return matches


def compute_sync_plan(config: Config, interactive: bool = True) -> SyncPlan:
    """Compute what needs to change between Backloggd and Steam."""
    overrides = load_overrides(config.data_dir)
    keep_ids = load_keep_list(config.data_dir)

    if overrides:
        console.print(f"[dim]Loaded {len(overrides)} overrides[/]")
    if keep_ids:
        console.print(f"[dim]Loaded {len(keep_ids)} keep-list entries[/]")

    console.print("[bold]Fetching Backloggd wishlist...[/]")
    wishlist_names = get_backloggd_games(config.backloggd_username, "wishlist")
    console.print(f"  Found {len(wishlist_names)} wishlist games")

    console.print("[bold]Fetching Backloggd played/backlog/playing...[/]")
    done_names: list[str] = []
    for status in ("played", "backlog", "playing"):
        try:
            names = get_backloggd_games(config.backloggd_username, status)
            done_names.extend(names)
            console.print(f"  Found {len(names)} {status} games")
        except ValueError:
            console.print(f"  No {status} games found")
    done_names = list(set(done_names))

    console.print("[bold]Fetching Steam wishlist...[/]")
    steam_wishlist = get_steam_wishlist(config.steam_api_key, config.steam_id)
    console.print(f"  Found {len(steam_wishlist)} games on Steam wishlist")

    console.print("[bold]Loading Steam app list...[/]")
    steam_apps = get_steam_app_list(config.cache_dir, config.steam_api_key)
    console.print(f"  Loaded {len(steam_apps)} Steam apps")

    # Fill in any missing names in wishlist
    steam_wishlist = fill_wishlist_names(steam_wishlist, steam_apps)
    steam_wishlist_ids = set(steam_wishlist.keys())

    console.print("[bold]Matching games...[/]")
    wishlist_matches = match_games(
        wishlist_names, steam_apps, config.match_threshold, overrides
    )
    done_matches = match_games(
        done_names, steam_apps, config.match_threshold, overrides
    )

    # Resolve ambiguous matches interactively before building the plan
    if interactive:
        wishlist_matches = resolve_ambiguous_matches(
            wishlist_matches, overrides, config.data_dir
        )
        done_matches = resolve_ambiguous_matches(
            done_matches, overrides, config.data_dir
        )

    done_app_ids = {
        m.steam_app_id for m in done_matches if m.matched and m.steam_app_id
    }

    plan = SyncPlan()
    for m in wishlist_matches:
        if m.skipped:
            plan.skipped.append(m)
        elif not m.matched:
            plan.unmatched.append(m)
        elif m.steam_app_id in steam_wishlist_ids:
            plan.already_synced.append(m)
        else:
            plan.to_add.append(m)

    # Collect all Backloggd-matched app IDs (wishlist + done)
    all_backloggd_app_ids: set[int] = set()
    for m in wishlist_matches:
        if m.matched and m.steam_app_id:
            all_backloggd_app_ids.add(m.steam_app_id)
    for m in done_matches:
        if m.matched and m.steam_app_id:
            all_backloggd_app_ids.add(m.steam_app_id)

    # Also build a set of all Backloggd game names (normalized) for fallback matching
    all_backloggd_names: set[str] = set()
    for name in wishlist_names + done_names:
        all_backloggd_names.add(name.strip().lower())

    # Games to remove: on Steam wishlist AND in done list on Backloggd
    # BUT not if they're in the keep list
    for app_id, name in steam_wishlist.items():
        if app_id in done_app_ids:
            if app_id in keep_ids:
                plan.kept.append(MatchResult(
                    backloggd_name=name,
                    steam_app_id=app_id,
                    steam_name=name,
                    score=100,
                    matched=True,
                ))
            else:
                plan.to_remove.append(MatchResult(
                    backloggd_name=name,
                    steam_app_id=app_id,
                    steam_name=name,
                    score=100,
                    matched=True,
                ))
        elif app_id not in all_backloggd_app_ids:
            # Skip "Unknown" games (not in Steam app list — delisted/unreleased)
            if name.startswith("Unknown ("):
                continue
            # Also skip if the Steam name already matches a Backloggd game name
            # (handles cases where fuzzy matching couldn't find the app ID)
            if name.strip().lower() in all_backloggd_names:
                continue
            # On Steam wishlist but not tracked anywhere on Backloggd
            plan.steam_only.append(MatchResult(
                backloggd_name="",
                steam_app_id=app_id,
                steam_name=name,
                score=0,
                matched=False,
            ))

    return plan


def display_sync_plan(plan: SyncPlan) -> None:
    """Display the sync plan as a rich table."""
    if plan.to_add:
        table = Table(title="Games to ADD to Steam Wishlist", style="green")
        table.add_column("Backloggd Name")
        table.add_column("Steam Match")
        table.add_column("Score")
        table.add_column("Source")
        table.add_column("Verify")
        for m in plan.to_add:
            source = "override" if m.override else "fuzzy"
            url = f"store.steampowered.com/app/{m.steam_app_id}" if m.steam_app_id else ""
            table.add_row(m.backloggd_name, m.steam_name or "", str(m.score), source, url)
        console.print(table)

    if plan.to_remove:
        table = Table(title="Games to REMOVE from Steam Wishlist", style="red")
        table.add_column("Game")
        table.add_column("App ID")
        for m in plan.to_remove:
            table.add_row(m.steam_name or m.backloggd_name, str(m.steam_app_id))
        console.print(table)

    if plan.kept:
        table = Table(title="Games KEPT on Steam Wishlist (keep list)", style="cyan")
        table.add_column("Game")
        table.add_column("App ID")
        for m in plan.kept:
            table.add_row(m.steam_name or m.backloggd_name, str(m.steam_app_id))
        console.print(table)

    if plan.unmatched:
        table = Table(title="Unmatched Games (not found on Steam)", style="yellow")
        table.add_column("Backloggd Name")
        table.add_column("Best Match")
        table.add_column("Score")
        for m in plan.unmatched:
            table.add_row(m.backloggd_name, m.steam_name or "-", str(m.score))
        console.print(table)

    if plan.steam_only:
        table = Table(title="Steam-only Games (not on Backloggd)", style="magenta")
        table.add_column("Steam Game")
        table.add_column("App ID")
        table.add_column("Store URL")
        for m in plan.steam_only:
            table.add_row(
                m.steam_name or "?",
                str(m.steam_app_id),
                f"store.steampowered.com/app/{m.steam_app_id}",
            )
        console.print(table)
        console.print("[dim]These games are on your Steam wishlist but not tracked on Backloggd.[/]")

    if plan.skipped:
        console.print(
            f"\n[dim]{len(plan.skipped)} games skipped (not on Steam per overrides.json)[/]"
        )

    if plan.already_synced:
        console.print(
            f"\n[dim]{len(plan.already_synced)} games already on Steam wishlist (no action needed)[/]"
        )

    if not plan.to_add and not plan.to_remove:
        console.print("\n[bold green]Everything is in sync![/]")


def _write_sync_log(
    config: Config,
    added: list[int],
    removed: list[int],
    plan: SyncPlan,
) -> None:
    """Write a sync log entry for undo/audit purposes."""
    log_dir = Path(config.data_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"sync_{timestamp}.json"

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "added": [
            {"app_id": m.steam_app_id, "name": m.steam_name, "backloggd": m.backloggd_name}
            for m in plan.to_add if m.steam_app_id in added
        ],
        "removed": [
            {"app_id": m.steam_app_id, "name": m.steam_name}
            for m in plan.to_remove if m.steam_app_id in removed
        ],
    }

    with open(log_path, "w") as f:
        json.dump(entry, f, indent=2, ensure_ascii=False)

    console.print(f"[dim]Sync log saved to {log_path}[/]")


def execute_sync_plan(
    plan: SyncPlan, config: Config, no_remove: bool = False
) -> None:
    """Execute the sync plan via Playwright browser automation."""
    add_ids = [m.steam_app_id for m in plan.to_add if m.steam_app_id]
    remove_ids = (
        [] if no_remove
        else [m.steam_app_id for m in plan.to_remove if m.steam_app_id]
    )

    added: list[int] = []
    removed: list[int] = []

    if add_ids:
        console.print(f"\n[bold]Adding {len(add_ids)} games to Steam wishlist...[/]")
        added = asyncio.run(
            add_to_wishlist(config.browser_data_dir, add_ids, config.rate_limit_delay)
        )
        console.print(f"  [green]Successfully added {len(added)}/{len(add_ids)} games[/]")

    if remove_ids:
        console.print(
            f"\n[bold]Removing {len(remove_ids)} games from Steam wishlist...[/]"
        )
        removed = asyncio.run(
            remove_from_wishlist(config.browser_data_dir, remove_ids, config.rate_limit_delay)
        )
        console.print(
            f"  [red]Successfully removed {len(removed)}/{len(remove_ids)} games[/]"
        )

    # Write sync log for undo/audit
    if added or removed:
        _write_sync_log(config, added, removed, plan)

    console.print("\n[bold green]Sync complete![/]")
