import asyncio
import logging
import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler

from .config import DEFAULT_DATA_DIR, load_config
from .overrides import load_keep_list, load_overrides, save_keep_list, save_overrides
from .secure_config import config_exists, encrypt_config, get_config_path
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
    """GLSA -- Sync your Backloggd wishlist to Steam.

    \b
    Quick start:
      1. glsa setup            Install Playwright browser
      2. glsa configure        Set up credentials (encrypted)
      3. glsa login steam      Log into Steam in the browser (one-time)
      4. glsa login backloggd  Log into Backloggd (auto with credentials)
      5. glsa sync --dry-run   Preview what would change
      6. glsa sync             Execute the sync (Backloggd -> Steam)
      7. glsa sync-backloggd   Add Steam-only wishlist games to Backloggd

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
    """Install Playwright browsers and copy override/keep examples."""
    console.print("[bold]Installing Playwright Chromium...[/]")
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=True,
    )
    console.print("[green]Playwright Chromium installed![/]")

    # Copy override/keep examples to ~/.glsa/ if they don't exist
    data_dir = Path(DEFAULT_DATA_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)

    for filename in ("overrides.json", "keep.json"):
        example = Path(f"{filename.replace('.json', '')}.example.json")
        target = data_dir / filename
        if not target.exists() and example.exists():
            target.write_text(example.read_text())
            console.print(f"[green]Created {target} from example[/]")
        elif not target.exists():
            console.print(f"[dim]No {filename} example found -- you can create {target} manually[/]")

    if not config_exists(DEFAULT_DATA_DIR):
        console.print("\n[yellow]No config found. Run `glsa configure` to set up your credentials.[/]")


@main.command()
def configure() -> None:
    """Set up encrypted config with all credentials.

    \b
    Walks you through entering all required values, then encrypts
    them with a master password and saves to ~/.glsa/config.enc.

    To change your config, just run this command again.
    Set GLSA_MASTER_PASSWORD env var to skip the password prompt.
    """
    config_path = get_config_path(DEFAULT_DATA_DIR)

    if config_path.exists():
        console.print("[yellow]Existing config found. This will replace it.[/]")
        if not click.confirm("Continue?"):
            console.print("[dim]Cancelled.[/]")
            return

    console.print("\n[bold]GLSA Configuration[/]\n")
    console.print("Enter your credentials below. All values will be encrypted.\n")

    backloggd_username = click.prompt("Backloggd username")
    backloggd_password = click.prompt("Backloggd password", hide_input=True)
    steam_api_key = click.prompt("Steam API key (get one at steamcommunity.com/dev/apikey)", hide_input=True)
    steam_id = click.prompt("Steam ID (64-bit, find at steamid.io)")

    console.print("\n[dim]Optional settings (press Enter to use defaults):[/]")
    match_threshold = click.prompt("Match threshold", default=85, type=int)
    rate_limit_delay = click.prompt("Rate limit delay (seconds)", default=2.0, type=float)

    console.print("\n[bold]Set a master password to encrypt your config.[/]")
    console.print("[dim]You'll need this password each time you run glsa.[/]")
    console.print("[dim]Set GLSA_MASTER_PASSWORD env var to skip the prompt.[/]\n")

    while True:
        master_pw = click.prompt("Master password", hide_input=True)
        confirm_pw = click.prompt("Confirm master password", hide_input=True)
        if master_pw == confirm_pw:
            break
        console.print("[red]Passwords don't match. Try again.[/]")

    data = {
        "backloggd_username": backloggd_username,
        "backloggd_password": backloggd_password,
        "steam_api_key": steam_api_key,
        "steam_id": steam_id,
        "match_threshold": match_threshold,
        "rate_limit_delay": rate_limit_delay,
    }

    encrypt_config(data, master_pw, config_path)
    console.print(f"\n[green]Config saved to {config_path}[/]")
    console.print("[dim]Run `glsa login steam` and `glsa login backloggd` next.[/]")


@main.command()
@click.argument("service", type=click.Choice(["steam", "backloggd"]))
def login(service: str) -> None:
    """Open browser to log into a service (steam or backloggd)."""
    config = load_config()
    if service == "steam":
        asyncio.run(ensure_steam_login(config.browser_data_dir))
    else:
        from .backloggd_browser import ensure_backloggd_login
        asyncio.run(ensure_backloggd_login(
            config.backloggd_browser_data_dir,
            config.backloggd_username,
            config.backloggd_password,
        ))


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
        console.print("\n[dim]Dry run -- no changes made.[/]")
        return

    if not force:
        if not click.confirm("\nProceed with sync?"):
            console.print("[dim]Cancelled.[/]")
            return

    execute_sync_plan(plan, config, no_remove=no_remove)


@main.command(name="sync-backloggd")
@click.option("--dry-run", is_flag=True, help="Show what would be added without doing it")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt")
def sync_backloggd(dry_run: bool, force: bool) -> None:
    """Add Steam-only wishlist games to Backloggd wishlist."""
    from .backloggd_browser import search_and_add_to_wishlist

    config = load_config()
    plan = compute_sync_plan(config)

    if not plan.steam_only:
        console.print("[bold green]No Steam-only games to add to Backloggd![/]")
        return

    console.print(f"\n[bold]Found {len(plan.steam_only)} Steam-only games to add to Backloggd:[/]")
    for m in plan.steam_only:
        console.print(f"  - {m.steam_name} (app {m.steam_app_id})")

    if dry_run:
        console.print("\n[dim]Dry run -- no changes made.[/]")
        return

    if not force:
        if not click.confirm(f"\nAdd {len(plan.steam_only)} games to Backloggd wishlist?"):
            console.print("[dim]Cancelled.[/]")
            return

    game_names = [m.steam_name for m in plan.steam_only if m.steam_name]
    added = asyncio.run(
        search_and_add_to_wishlist(
            config.backloggd_browser_data_dir,
            game_names,
            config.backloggd_username,
            config.backloggd_password,
            config.rate_limit_delay,
        )
    )
    console.print(f"\n[green]Successfully added {len(added)}/{len(game_names)} games to Backloggd[/]")


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
    console.print(f"  Steam-only (not on Backloggd):     {len(plan.steam_only)}")


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
            console.print(f"[red]Invalid value:[/] {value} -- must be a Steam app ID or 'skip'")
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
