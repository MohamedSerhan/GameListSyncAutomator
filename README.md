# GLSA — Game List Sync Automator

Automatically sync your **Backloggd wishlist** with your **Steam wishlist**. Keep your game lists in sync across both platforms and get notified when games go on sale on Steam.

## Problem

You maintain your game wishlist on **Backloggd** as your source of truth, but Steam is where you get sale notifications. GLSA keeps them in sync so you don't have to manually add/remove games from Steam.

## Features

- ✅ **Bidirectional sync**: Backloggd → Steam and Steam → Backloggd
- ✅ **Automatic daily runs**: Set it and forget it (Windows Task Scheduler)
- ✅ **Smart matching**: Fuzzy name matching finds Steam games even with slight name differences
- ✅ **Manual overrides**: Fix ambiguous matches with a single command
- ✅ **Preserved sessions**: Log in once, runs silently in the background
- ✅ **Keep list**: Prevent specific games from being removed
- ✅ **Encrypted config**: Your credentials are encrypted with a master password
- ✅ **Dry-run mode**: Preview changes before syncing

## Quick Start

### 1. Install

```bash
git clone https://github.com/YOUR_USERNAME/GameListSyncAutomator.git
cd GameListSyncAutomator
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -e .
glsa setup
```

### 2. Configure

```bash
glsa configure
```

Walks you through entering:
- Backloggd username & password
- Steam API key (get one at [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey))
- Steam ID (find at [steamid.io](https://steamid.io))
- Optional: match threshold, rate limits

All credentials are encrypted with a master password.

### 3. Log In

```bash
glsa login steam       # QR code login (one-time)
glsa login backloggd   # Automatic with your credentials
```

Sessions persist in the browser data directory, so you only do this once.

### 4. Test

```bash
glsa sync --dry-run          # Preview Backloggd → Steam changes
glsa sync-backloggd --dry-run # Preview Steam-only games to add to Backloggd
```

### 5. Enable Automation

```bash
glsa schedule              # Daily at 09:00
glsa schedule --time 14:00 # Or at a custom time
```

Done! Both syncs run silently every day.

## Usage

### Manual Syncing

```bash
glsa sync                 # Sync Backloggd to Steam (with confirmation)
glsa sync --force         # Skip confirmation
glsa sync --no-remove     # Only add games, never remove
glsa sync-backloggd       # Add Steam-only games to Backloggd
```

### View Status

```bash
glsa status               # Show sync summary
glsa match "Game Name"    # Test fuzzy matching for a game
```

### Fine-Tune Matches

```bash
# Force a specific Steam app ID
glsa override "Exodus" 3230960

# Mark a game as not on Steam (skip it)
glsa override "Orbitals" skip

# Prevent a game from being removed from Steam wishlist
glsa keep 1601580
```

For more control, edit directly:
- `~/.glsa/overrides.json` — Steam app ID overrides
- `~/.glsa/keep.json` — Games to never remove from Steam

### Manage Automation

```bash
glsa schedule              # Create daily scheduled task
glsa schedule --time 22:00 # Change the run time
glsa unschedule            # Remove the scheduled task
```

View logs in Windows Task Scheduler or check `~/.glsa/sync.log`.

## How It Works

### Backloggd → Steam (`glsa sync`)

1. Fetches your Backloggd wishlist
2. Fetches your Backloggd played/backlog/playing lists
3. Fetches your Steam wishlist
4. Fuzzy-matches games by name to Steam app IDs
5. Adds matching Backloggd wishlist games to Steam
6. Removes games from Steam if they're in your Backloggd played/backlog/playing lists
7. Respects your keep list (never removes those games)

### Steam → Backloggd (`glsa sync-backloggd`)

1. Finds games on your Steam wishlist that aren't tracked on Backloggd
2. Searches Backloggd for each game
3. Automatically adds them to your Backloggd wishlist

## Configuration

### Environment Variables

```bash
# Skip the master password prompt (for automation)
$env:GLSA_MASTER_PASSWORD = "your_password"

# On Windows, set permanently:
setx GLSA_MASTER_PASSWORD "your_password"
```

### Config File Location

- **Encrypted config**: `~/.glsa/config.enc`
- **Overrides**: `~/.glsa/overrides.json`
- **Keep list**: `~/.glsa/keep.json`
- **Browser data** (sessions): `~/.glsa/browser_data/`
- **Logs**: `~/.glsa/sync.log`

To change your config, just run `glsa configure` again.

## Troubleshooting

### "Invalid Login or password"

Your Backloggd credentials are wrong. Run `glsa configure` again and double-check.

### "No search results found for [game]"

The game might not exist on Backloggd yet, or it uses a very different name. Check manually on [backloggd.com](https://backloggd.com) and use `glsa override` if needed.

### "Could not find Steam app"

The game isn't on Steam, or the fuzzy matching score is below the threshold (default: 85). Use `glsa match "Game Name"` to debug, then `glsa override "Game Name" skip` to mark it.

### "Scheduled task didn't run"

1. Check that `GLSA_MASTER_PASSWORD` is set as a system environment variable
2. Check that your PC was on and connected to the internet at the scheduled time
3. View the task in Windows Task Scheduler and check its last run status
4. Check `~/.glsa/sync.log` for errors

### Sessions expired

Rerun `glsa login steam` or `glsa login backloggd` to refresh.

## Development

```bash
# Install in dev mode with test dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Format code
black src/

# Type check
mypy src/
```

## Architecture

- **`backloggd_scraper.py`** — Scrapes Backloggd wishlist/backlog/played using httpx + BeautifulSoup
- **`steam_api.py`** — Fetches Steam wishlist and app list via Steam Web API
- **`steam_browser.py`** — Playwright for Steam login and wishlist mutations
- **`backloggd_browser.py`** — Playwright for Backloggd login and adding games to wishlist
- **`matcher.py`** — Fuzzy name matching to map Backloggd games to Steam app IDs
- **`sync.py`** — Core logic: compute what needs to change and execute changes
- **`scheduler.py`** — Windows Task Scheduler integration for daily automation

## Limitations

- **Windows only** (uses Windows Task Scheduler for automation)
- **Manual Steam login** requires QR code (only once, session persists)
- **Rate limiting**: Backloggd scraping has intentional delays to be respectful

## License

MIT

## Feedback

Found a bug? Game that won't match? [Open an issue](https://github.com/YOUR_USERNAME/GameListSyncAutomator/issues).
