import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler

from .config import load_config
from .overrides import load_keep_list, load_overrides, save_keep_list, save_overrides
from .steam_browser import ensure_steam_login
from .sync import compute_sync_plan, display_sync_plan, execute_sync_plan

console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_time=False, show_path=False)],
    )


@click.group(invoke_without_command=True)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """GLSA — Sync your Backloggd wishlist to Steam.

    \b
    Quick start:
      1. glsa setup          Install Playwright + create .env
      2. Fill in .env        Add your Backloggd username, Steam API key, Steam ID
      3. glsa login          Log into Steam in the browser (one-time)
      4. glsa sync --dry-run Preview what would change
      5. glsa sync           Execute the sync

    \b
    Fine-tuning matches:
      glsa override "Exodus" 3230960     Force a Steam app ID
      glsa override "Orbitals" skip      Mark as not on Steam
      glsa keep 1601580                  Never remove from wishlist
      Edit ~/.glsa/overrides.json and ~/.glsa/keep.json directly
    """
    _setup_logging(verbose)
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@main.command()
def setup() -> None:
    """Install Playwright browsers and create config template."""
    console.print("[bold]Installing Playwright Chromium...[/]")
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=True,
    )
    console.print("[green]Playwright Chromium installed![/]")

    env_example = Path(".env.example")
    env_file = Path(".env")
    if not env_file.exists() and env_example.exists():
        env_file.write_text(env_example.read_text())
        console.print("[green]Created .env from .env.example — fill in your values![/]")
    elif not env_file.exists():
        console.print("[yellow]No .env.example found. Create a .env file manually.[/]")
    else:
        console.print("[dim].env already exists[/]")

    # Copy override/keep examples to ~/.glsa/ if they don't exist
    config = load_config()
    data_dir = Path(config.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    for filename in ("overrides.json", "keep.json"):
        example = Path(f"{filename.replace('.json', '')}.example.json")
        target = data_dir / filename
        if not target.exists() and example.exists():
            target.write_text(example.read_text())
            console.print(f"[green]Created {target} from example[/]")
        elif not target.exists():
            console.print(f"[dim]No {filename} example found — you can create {target} manually[/]")


@main.command()
def login() -> None:
    """Open browser for Steam login."""
    config = load_config()
    asyncio.run(ensure_steam_login(config.browser_data_dir))


@main.command()
@click.option("--dry-run", is_flag=True, help="Show what would change without doing it")
@click.option("--no-remove", is_flag=True, help="Only add games, never remove from Steam")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt")
def sync(dry_run: bool, no_remove: bool, force: bool) -> None:
    """Sync Backloggd wishlist to Steam wishlist."""
    config = load_config()
    plan = compute_sync_plan(config)
    display_sync_plan(plan)

    if not plan.to_add and not plan.to_remove:
        return

    if dry_run:
        console.print("\n[dim]Dry run — no changes made.[/]")
        return

    if not force:
        if not click.confirm("\nProceed with sync?"):
            console.print("[dim]Cancelled.[/]")
            return

    execute_sync_plan(plan, config, no_remove=no_remove)


@main.command()
def status() -> None:
    """Show current state of Backloggd and Steam wishlists."""
    config = load_config()
    plan = compute_sync_plan(config)

    console.print(f"\n[bold]Summary:[/]")
    console.print(f"  Backloggd wishlist games matched: {len(plan.to_add) + len(plan.already_synced)}")
    console.print(f"  Already on Steam wishlist:        {len(plan.already_synced)}")
    console.print(f"  To add to Steam:                  {len(plan.to_add)}")
    console.print(f"  To remove from Steam:             {len(plan.to_remove)}")
    console.print(f"  Kept (won't remove):              {len(plan.kept)}")
    console.print(f"  Skipped (not on Steam):            {len(plan.skipped)}")
    console.print(f"  Unmatched (no Steam match):        {len(plan.unmatched)}")


@main.command(name="override")
@click.argument("game_name")
@click.argument("value")
def override_cmd(game_name: str, value: str) -> None:
    """Set a manual override for a game.

    \b
    VALUE can be:
      - A Steam app ID (e.g. 3230960) to force a match
      - "skip" or "null" to mark as not on Steam

    \b
    Examples:
      glsa override "Exodus" 3230960
      glsa override "Orbitals" skip
    """
    config = load_config()
    overrides = load_overrides(config.data_dir)

    if value.lower() in ("skip", "null", "none"):
        overrides[game_name] = None
        save_overrides(config.data_dir, overrides)
        console.print(f"[yellow]{game_name}[/] marked as not on Steam (skipped)")
    else:
        try:
            app_id = int(value)
        except ValueError:
            console.print(f"[red]Invalid value:[/] {value} — must be a Steam app ID or 'skip'")
            raise SystemExit(1)
        overrides[game_name] = app_id
        save_overrides(config.data_dir, overrides)
        console.print(f"[green]{game_name}[/] -> Steam app {app_id}")


@main.command()
@click.argument("app_id", type=int)
def keep(app_id: int) -> None:
    """Add a Steam app ID to the keep list (never remove from wishlist).

    \b
    Example:
      glsa keep 1601580
    """
    config = load_config()
    keep_ids = load_keep_list(config.data_dir)
    keep_ids.add(app_id)
    save_keep_list(config.data_dir, keep_ids)
    console.print(f"[green]App {app_id} added to keep list[/]")


@main.command()
@click.argument("name")
def match(name: str) -> None:
    """Test fuzzy matching for a single game name."""
    from .matcher import match_games
    from .steam_api import get_steam_app_list

    config = load_config()
    steam_apps = get_steam_app_list(config.cache_dir, config.steam_api_key)
    overrides = load_overrides(config.data_dir)

    results = match_games([name], steam_apps, threshold=0, overrides=overrides)
    r = results[0]

    if r.skipped:
        console.print("[yellow]Skipped[/] (marked as not on Steam in overrides.json)")
    elif r.override:
        console.print(f"[green]Override![/] {r.steam_name} (app ID: {r.steam_app_id}) {r.store_url}")
    elif r.matched and r.ambiguous:
        console.print(f"[yellow]Ambiguous![/] {r.steam_name} has {len(r.candidates)} games:")
        for app_id in r.candidates:
            console.print(f"  https://store.steampowered.com/app/{app_id}")
        console.print("Use: glsa override \"{name}\" <app_id>")
    elif r.matched:
        console.print(f"[green]Matched![/] {r.steam_name} (app ID: {r.steam_app_id}, score: {r.score}) {r.store_url}")
    else:
        console.print(f"[yellow]Best match:[/] {r.steam_name} (score: {r.score}, below threshold {config.match_threshold})")
