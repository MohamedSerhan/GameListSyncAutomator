import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import click
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

from rapidfuzz import fuzz

from .backloggd import BackloggdChallengeError, get_backloggd_games
from .config import Config
from .matcher import STORE_URL, MatchResult, match_games
from .overrides import load_keep_list, load_overrides, save_overrides
from .snapshot import Snapshot, build_snapshot_after_sync, save_snapshot
from .steam_api import fill_wishlist_names, get_steam_app_list, get_steam_wishlist
from .steam_browser import add_to_wishlist, remove_from_wishlist

log = logging.getLogger(__name__)
console = Console()


@dataclass
class SyncPlan:
    to_add: list[MatchResult] = field(default_factory=list)
    to_remove: list[MatchResult] = field(default_factory=list)
    to_remove_propagated: list[MatchResult] = field(default_factory=list)  # Deleted from Backloggd -> remove from Steam
    to_remove_from_backloggd: list[MatchResult] = field(default_factory=list)  # Deleted from Steam -> remove from Backloggd
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


def compute_sync_plan(config: Config, interactive: bool = True, snapshot: Snapshot | None = None) -> SyncPlan:
    """Compute what needs to change between Backloggd and Steam."""
    overrides = load_overrides(config.data_dir)
    keep_ids = load_keep_list(config.data_dir)

    if overrides:
        console.print(f"[dim]Loaded {len(overrides)} overrides[/]")
    if keep_ids:
        console.print(f"[dim]Loaded {len(keep_ids)} keep-list entries[/]")

    console.print("[bold]Backloggd[/]")
    statuses = ("wishlist", "played", "backlog", "playing")
    scraped: dict[str, list[str]] = {}
    used_playwright = False
    try:
        scraped["wishlist"] = get_backloggd_games(config.backloggd_username, "wishlist")
        for status in statuses[1:]:
            try:
                scraped[status] = get_backloggd_games(config.backloggd_username, status)
            except ValueError:
                scraped[status] = []
    except BackloggdChallengeError as e:
        console.print(
            f"[yellow]Backloggd blocked httpx with an anti-bot challenge "
            f"(Bunny Shield). Falling back to Playwright...[/]"
        )
        log.debug("Challenge details: %s", e)
        from .backloggd_browser import scrape_games as _pw_scrape

        scraped = asyncio.run(
            _pw_scrape(
                config.backloggd_browser_data_dir,
                config.backloggd_username,
                config.backloggd_password,
                list(statuses),
            )
        )
        used_playwright = True

    wishlist_names = scraped.get("wishlist", [])
    console.print(f"  Wishlist:  {len(wishlist_names)} games")
    done_names: list[str] = []
    for status in statuses[1:]:
        names = scraped.get(status, [])
        done_names.extend(names)
        console.print(f"  {status.capitalize():<9} {len(names)} games")
    done_names = list(set(done_names))
    if used_playwright:
        console.print("[dim]  (scraped via Playwright)[/]")

    # Safety guard: if Backloggd returned 0 games across ALL categories but
    # the snapshot tracks games, the scrape almost certainly failed (site
    # change, network blip, profile went private, rate-limit). Treating this
    # as "user deleted everything" would propagate a mass-deletion to Steam.
    # Abort loudly rather than trust a suspiciously empty result.
    total_backloggd = len(wishlist_names) + len(done_names)
    snapshot_count = len(snapshot.synced_games) if snapshot else 0
    if total_backloggd == 0 and snapshot_count > 0:
        raise click.ClickException(
            f"Backloggd returned 0 games across wishlist/played/backlog/playing, "
            f"but the snapshot tracks {snapshot_count} games. This is almost "
            f"certainly a scrape failure, not a real empty state — refusing to "
            f"propagate mass-deletion to Steam. Rerun later, or check "
            f"https://backloggd.com/u/{config.backloggd_username}/games/added:desc/type:wishlist/ "
            f"in a browser. If your Backloggd is genuinely empty on purpose, "
            f"run `glsa snapshot reset` first."
        )

    console.print()
    console.print("[bold]Steam[/]")
    steam_wishlist = get_steam_wishlist(config.steam_api_key, config.steam_id)
    console.print(f"  Wishlist:  {len(steam_wishlist)} games")
    steam_apps = get_steam_app_list(config.cache_dir, config.steam_api_key)
    console.print(f"  App list:  {len(steam_apps)} apps")
    steam_wishlist = fill_wishlist_names(steam_wishlist, steam_apps)
    steam_wishlist_ids = set(steam_wishlist.keys())

    console.print()
    total_to_match = len(wishlist_names) + len(done_names)
    console.print(f"[bold]Matching[/] ({total_to_match} games)")
    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
        transient=False,
    ) as progress:
        overall = progress.add_task(
            f"[bold]Overall ({total_to_match} games)", total=total_to_match
        )
        wishlist_matches = match_games(
            wishlist_names, steam_apps, config.match_threshold, overrides,
            progress=progress, overall_task=overall,
            progress_description=f"  Wishlist ({len(wishlist_names)} games)",
        )
        done_matches = match_games(
            done_names, steam_apps, config.match_threshold, overrides,
            progress=progress, overall_task=overall,
            progress_description=f"  Played/backlog ({len(done_names)} games)",
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

    snapshot_ids = set(snapshot.synced_games.keys()) if snapshot else set()

    plan = SyncPlan()
    for m in wishlist_matches:
        if m.skipped:
            plan.skipped.append(m)
        elif not m.matched:
            plan.unmatched.append(m)
        elif m.steam_app_id in steam_wishlist_ids:
            plan.already_synced.append(m)
        elif m.steam_app_id in snapshot_ids and m.steam_app_id not in steam_wishlist_ids:
            # Was synced before, now missing from Steam → user removed from Steam
            entry = snapshot.synced_games[m.steam_app_id]
            plan.to_remove_from_backloggd.append(MatchResult(
                backloggd_name=entry.backloggd_name,
                steam_app_id=m.steam_app_id,
                steam_name=entry.steam_name,
                score=100,
                matched=True,
            ))
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

    # Build set of unmatched Backloggd names for snapshot safety check
    unmatched_bg_names = {m.backloggd_name.strip().lower() for m in plan.unmatched}

    # Also build a list of all Backloggd game names (normalized) for fallback fuzzy matching
    all_backloggd_names_list: list[str] = [name.strip().lower() for name in wishlist_names + done_names]

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
            # Also skip if the Steam name fuzzy-matches a Backloggd game name
            # (handles cases where fuzzy matching couldn't find the app ID, or names differ slightly)
            steam_name_normalized = name.strip().lower()
            best_match_score = max(
                (fuzz.token_sort_ratio(steam_name_normalized, bg_name) for bg_name in all_backloggd_names_list),
                default=0
            )
            if best_match_score >= 80:
                continue

            # Check snapshot: if this game was previously synced, it was deleted from Backloggd
            if app_id in snapshot_ids:
                entry = snapshot.synced_games[app_id]
                # Safety: if the snapshot's backloggd_name is in unmatched,
                # the game is still on Backloggd but failed matching — don't delete
                if entry.backloggd_name.strip().lower() in unmatched_bg_names:
                    continue
                if app_id in keep_ids:
                    plan.kept.append(MatchResult(
                        backloggd_name=entry.backloggd_name,
                        steam_app_id=app_id,
                        steam_name=entry.steam_name,
                        score=100,
                        matched=True,
                    ))
                else:
                    plan.to_remove_propagated.append(MatchResult(
                        backloggd_name=entry.backloggd_name,
                        steam_app_id=app_id,
                        steam_name=entry.steam_name,
                        score=100,
                        matched=True,
                    ))
            else:
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
    console.print("\n[bold]Report[/]")
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

    if plan.to_remove_propagated:
        table = Table(title="Games to REMOVE from Steam (deleted from Backloggd)", style="yellow")
        table.add_column("Game")
        table.add_column("App ID")
        for m in plan.to_remove_propagated:
            table.add_row(m.steam_name or m.backloggd_name, str(m.steam_app_id))
        console.print(table)

    if plan.to_remove_from_backloggd:
        table = Table(title="Games to REMOVE from Backloggd (deleted from Steam)", style="yellow")
        table.add_column("Game")
        table.add_column("App ID")
        for m in plan.to_remove_from_backloggd:
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

    if not any((plan.to_add, plan.to_remove, plan.to_remove_propagated, plan.to_remove_from_backloggd)):
        console.print("\n[bold green]Everything is in sync![/]")


def _write_sync_log(
    config: Config,
    added: list[int],
    removed: list[int],
    backloggd_removed: list[str],
    plan: SyncPlan,
) -> None:
    """Write a sync log entry for undo/audit purposes."""
    log_dir = Path(config.data_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"sync_{timestamp}.json"

    removed_set = set(removed)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "added": [
            {"app_id": m.steam_app_id, "name": m.steam_name, "backloggd": m.backloggd_name}
            for m in plan.to_add if m.steam_app_id in added
        ],
        "removed": [
            {"app_id": m.steam_app_id, "name": m.steam_name}
            for m in plan.to_remove if m.steam_app_id in removed_set
        ],
        "removed_propagated": [
            {"app_id": m.steam_app_id, "name": m.steam_name, "reason": "deleted from Backloggd"}
            for m in plan.to_remove_propagated if m.steam_app_id in removed_set
        ],
        "removed_from_backloggd": [
            {"app_id": m.steam_app_id, "name": m.backloggd_name, "reason": "deleted from Steam"}
            for m in plan.to_remove_from_backloggd if m.backloggd_name in backloggd_removed
        ],
    }

    with open(log_path, "w") as f:
        json.dump(entry, f, indent=2, ensure_ascii=False)

    console.print(f"[dim]Sync log saved to {log_path}[/]")


MASS_DELETION_THRESHOLD = 0.5  # fraction of snapshot


def execute_sync_plan(
    plan: SyncPlan,
    config: Config,
    no_remove: bool = False,
    snapshot: Snapshot | None = None,
    backloggd_sync: bool = False,
) -> None:
    """Execute the sync plan via Playwright browser automation."""
    # Safety: if the propagated-deletion list is a huge fraction of the
    # snapshot, something is probably wrong upstream (partial scrape failure,
    # Backloggd returned incomplete pages, etc.). Require explicit
    # confirmation even under --force.
    if not no_remove and snapshot and snapshot.synced_games:
        propagated = len(plan.to_remove_propagated)
        snap_size = len(snapshot.synced_games)
        if propagated / snap_size >= MASS_DELETION_THRESHOLD:
            console.print(
                f"\n[bold red]WARNING:[/] {propagated}/{snap_size} tracked games "
                f"({propagated * 100 // snap_size}%) are marked for removal from "
                f"Steam because they appear to be missing from Backloggd. "
                f"This is an unusually large propagated deletion and often "
                f"indicates a Backloggd scrape problem."
            )
            if not click.confirm(
                "Type-confirm this is really what you want?", default=False
            ):
                console.print("[dim]Aborted. Snapshot is untouched; rerun when ready.[/]")
                raise click.Abort()
    add_ids = [m.steam_app_id for m in plan.to_add if m.steam_app_id]
    remove_ids = (
        [] if no_remove
        else [m.steam_app_id for m in plan.to_remove if m.steam_app_id]
    )
    propagated_remove_ids = (
        [] if no_remove
        else [m.steam_app_id for m in plan.to_remove_propagated if m.steam_app_id]
    )
    # Combine all Steam removals
    all_steam_remove_ids = remove_ids + propagated_remove_ids

    added: list[int] = []
    removed: list[int] = []

    if add_ids:
        console.print(f"\n[bold]Adding {len(add_ids)} games to Steam wishlist...[/]")
        added = asyncio.run(
            add_to_wishlist(config.browser_data_dir, add_ids, config.rate_limit_delay)
        )
        console.print(f"  [green]Successfully added {len(added)}/{len(add_ids)} games[/]")

    if all_steam_remove_ids:
        console.print(
            f"\n[bold]Removing {len(all_steam_remove_ids)} games from Steam wishlist...[/]"
        )
        removed = asyncio.run(
            remove_from_wishlist(config.browser_data_dir, all_steam_remove_ids, config.rate_limit_delay)
        )
        console.print(
            f"  [red]Successfully removed {len(removed)}/{len(all_steam_remove_ids)} games[/]"
        )

    # Remove games from Backloggd (deletion propagation: Steam -> Backloggd)
    backloggd_removed_names: list[str] = []
    if backloggd_sync and plan.to_remove_from_backloggd and not no_remove:
        from .backloggd_browser import remove_from_wishlist as backloggd_remove

        bg_names = [m.backloggd_name for m in plan.to_remove_from_backloggd if m.backloggd_name]
        console.print(f"\n[bold]Removing {len(bg_names)} games from Backloggd wishlist...[/]")
        backloggd_removed_names = asyncio.run(
            backloggd_remove(
                config.backloggd_browser_data_dir,
                bg_names,
                config.backloggd_username,
                config.backloggd_password,
                config.rate_limit_delay,
            )
        )
        console.print(
            f"  [yellow]Successfully removed {len(backloggd_removed_names)}/{len(bg_names)} games from Backloggd[/]"
        )

    # Write sync log for undo/audit
    if added or removed or backloggd_removed_names:
        _write_sync_log(config, added, removed, backloggd_removed_names, plan)

    # Build and save snapshot
    already_synced_ids: dict[int, tuple[str, str]] = {}
    for m in plan.already_synced:
        if m.steam_app_id:
            already_synced_ids[m.steam_app_id] = (m.steam_name or "", m.backloggd_name)

    added_to_steam: dict[int, tuple[str, str]] = {}
    for m in plan.to_add:
        if m.steam_app_id and m.steam_app_id in added:
            added_to_steam[m.steam_app_id] = (m.steam_name or "", m.backloggd_name)

    added_to_backloggd: dict[int, tuple[str, str]] = {}
    # Will be populated by the caller if backloggd sync adds games

    new_snapshot = build_snapshot_after_sync(
        previous=snapshot,
        already_synced_ids=already_synced_ids,
        added_to_steam=added_to_steam,
        added_to_backloggd=added_to_backloggd,
        removed_from_steam=set(removed),
        removed_from_backloggd={
            m.steam_app_id for m in plan.to_remove_from_backloggd
            if m.steam_app_id and m.backloggd_name in backloggd_removed_names
        },
    )
    save_snapshot(config.data_dir, new_snapshot)
    console.print(f"[dim]Snapshot saved ({len(new_snapshot.synced_games)} games tracked)[/]")

    console.print("\n[bold green]Sync complete![/]")
