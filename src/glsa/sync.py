import asyncio
import logging
from dataclasses import dataclass, field

from rich.console import Console
from rich.table import Table

from .backloggd import get_backloggd_games
from .config import Config
from .matcher import MatchResult, match_games
from .overrides import load_keep_list, load_overrides
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


def compute_sync_plan(config: Config) -> SyncPlan:
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

    return plan


def display_sync_plan(plan: SyncPlan) -> None:
    """Display the sync plan as a rich table."""
    if plan.to_add:
        table = Table(title="Games to ADD to Steam Wishlist", style="green")
        table.add_column("Backloggd Name")
        table.add_column("Steam Match")
        table.add_column("Score")
        table.add_column("Source")
        for m in plan.to_add:
            source = "override" if m.override else "fuzzy"
            table.add_row(m.backloggd_name, m.steam_name or "", str(m.score), source)
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
            table.add_row(m.backloggd_name, m.steam_name or "—", str(m.score))
        console.print(table)

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


def execute_sync_plan(
    plan: SyncPlan, config: Config, no_remove: bool = False
) -> None:
    """Execute the sync plan via Playwright browser automation."""
    add_ids = [m.steam_app_id for m in plan.to_add if m.steam_app_id]
    remove_ids = (
        [] if no_remove
        else [m.steam_app_id for m in plan.to_remove if m.steam_app_id]
    )

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

    console.print("\n[bold green]Sync complete![/]")
