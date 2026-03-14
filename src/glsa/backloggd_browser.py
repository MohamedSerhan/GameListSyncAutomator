"""Backloggd browser automation for adding games to wishlist.

Uses Playwright with a persistent browser context so the user only
needs to log in once. Login is manual (visible browser), but adding
games to the wishlist is automated (headless).
"""

import asyncio
import logging
from pathlib import Path

from playwright.async_api import BrowserContext, async_playwright

log = logging.getLogger(__name__)

BASE_URL = "https://backloggd.com"
LOGIN_URL = f"{BASE_URL}/users/sign_in"
LOGIN_TIMEOUT_MS = 300_000  # 5 minutes for manual login
PAGE_TIMEOUT_MS = 30_000


async def _launch_context(
    browser_data_dir: str, headless: bool = True
) -> tuple:
    """Launch a persistent browser context. Returns (playwright, context)."""
    Path(browser_data_dir).mkdir(parents=True, exist_ok=True)
    pw = await async_playwright().start()
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=browser_data_dir,
        headless=headless,
        viewport={"width": 1280, "height": 720},
    )
    return pw, context


async def _is_logged_in(context: BrowserContext) -> bool:
    """Check if the user is logged into Backloggd."""
    page = await context.new_page()
    try:
        await page.goto(BASE_URL, timeout=PAGE_TIMEOUT_MS)
        # Logged-in users have a profile link or avatar in the nav
        # Look for the user dropdown/avatar which indicates logged-in state
        logged_in = await page.query_selector("a[href='/users/sign_out']")
        if logged_in:
            return True
        # Also check for profile link pattern
        profile = await page.query_selector("a.nav-link[href*='/u/']")
        return profile is not None
    finally:
        await page.close()


async def ensure_backloggd_login(
    browser_data_dir: str,
    username: str = "",
    password: str = "",
) -> None:
    """Log into Backloggd automatically using credentials, or manually if not provided."""
    pw, context = await _launch_context(browser_data_dir, headless=bool(username and password))
    try:
        if await _is_logged_in(context):
            log.info("Already logged into Backloggd")
            return

        page = await context.new_page()
        await page.goto(LOGIN_URL, timeout=PAGE_TIMEOUT_MS)
        await page.wait_for_load_state("domcontentloaded")

        if username and password:
            log.info("Logging into Backloggd automatically...")
            # Fill in the login form — Backloggd uses Devise (Rails)
            # The email/username field and password field
            email_field = await page.query_selector(
                "input[name='user[email]'], "
                "input[name='user[login]'], "
                "input[type='email'], "
                "input#user_email"
            )
            pass_field = await page.query_selector(
                "input[name='user[password]'], "
                "input[type='password'], "
                "input#user_password"
            )

            if not email_field or not pass_field:
                log.warning("Could not find login form fields, falling back to manual login")
                await page.wait_for_selector(
                    "a[href='/users/sign_out'], a.nav-link[href*='/u/']",
                    timeout=LOGIN_TIMEOUT_MS,
                )
            else:
                await email_field.fill(username)
                await pass_field.fill(password)

                # Click submit
                submit_btn = await page.query_selector(
                    "input[type='submit'], "
                    "button[type='submit'], "
                    "button:has-text('Log In'), "
                    "input[value='Log In'], "
                    "input[value='Log in']"
                )
                if submit_btn:
                    await submit_btn.click()
                else:
                    await pass_field.press("Enter")

                # Wait for login to complete
                await page.wait_for_selector(
                    "a[href='/users/sign_out'], a.nav-link[href*='/u/']",
                    timeout=PAGE_TIMEOUT_MS,
                )
                log.info("Backloggd login successful!")
        else:
            log.info("Please log into Backloggd in the browser window...")
            await page.wait_for_selector(
                "a[href='/users/sign_out'], a.nav-link[href*='/u/']",
                timeout=LOGIN_TIMEOUT_MS,
            )
            log.info("Backloggd login successful!")

        await page.close()
    finally:
        await context.close()
        await pw.stop()


async def search_and_add_to_wishlist(
    browser_data_dir: str,
    game_names: list[str],
    username: str = "",
    password: str = "",
    delay: float = 3.0,
) -> list[str]:
    """Search Backloggd for each game and add it to wishlist.

    Automatically logs in if credentials are provided and not already logged in.
    Returns list of game names that were successfully added.
    """
    if not game_names:
        return []

    # Ensure logged in before starting
    await ensure_backloggd_login(browser_data_dir, username, password)

    pw, context = await _launch_context(browser_data_dir, headless=False)
    succeeded: list[str] = []
    try:
        page = await context.new_page()
        for game_name in game_names:
            try:
                success = await _add_single_game(page, game_name)
                if success:
                    succeeded.append(game_name)
                    log.info("Added '%s' to Backloggd wishlist", game_name)
                else:
                    log.warning("Could not add '%s' to Backloggd wishlist", game_name)
            except Exception:
                log.warning("Failed to add '%s' to Backloggd", game_name, exc_info=True)

            await asyncio.sleep(delay)

        await page.close()
    finally:
        await context.close()
        await pw.stop()

    return succeeded


async def _add_single_game(page, game_name: str) -> bool:
    """Search for a game on Backloggd and add it to wishlist.

    Returns True if successfully added.
    """
    # Search for the game
    search_url = f"{BASE_URL}/search/games/{game_name}/"
    await page.goto(search_url, timeout=PAGE_TIMEOUT_MS)
    await page.wait_for_load_state("domcontentloaded")

    # Find the first game result and click into it
    # Game cards use div.rating-hover with game links inside
    first_result = await page.query_selector("div.rating-hover a, div.card-img a")
    if not first_result:
        # Try alternative selector — search results may use different structure
        first_result = await page.query_selector("a[href*='/games/']")

    if not first_result:
        log.warning("No search results found for '%s'", game_name)
        return False

    href = await first_result.get_attribute("href")
    if not href or "/games/" not in href:
        log.warning("First result for '%s' doesn't look like a game link: %s", game_name, href)
        return False

    # Navigate to the game page
    game_url = href if href.startswith("http") else f"{BASE_URL}{href}"
    await page.goto(game_url, timeout=PAGE_TIMEOUT_MS)
    await page.wait_for_load_state("domcontentloaded")

    # Look for the wishlist button
    # Backloggd uses status buttons — look for "Want to Play" or wishlist-related elements
    # Try multiple selector strategies

    # Strategy 1: Look for a "Want to Play" or "Wishlist" button
    wishlist_btn = await page.query_selector(
        "button:has-text('Wishlist'), "
        "button:has-text('Want to Play'), "
        "a:has-text('Wishlist'), "
        "a:has-text('Want to Play')"
    )
    if wishlist_btn:
        await wishlist_btn.click()
        await asyncio.sleep(1)
        return True

    # Strategy 2: Look for a status dropdown/selector and pick wishlist
    status_btn = await page.query_selector(
        "[data-target='#wishlistModal'], "
        ".game-status-btn, "
        "button[data-action*='wishlist'], "
        "#wishlist-btn"
    )
    if status_btn:
        await status_btn.click()
        await asyncio.sleep(1)
        return True

    # Strategy 3: Look for any button/link with wishlist in its attributes
    wishlist_el = await page.query_selector("[class*='wishlist'], [id*='wishlist']")
    if wishlist_el:
        await wishlist_el.click()
        await asyncio.sleep(1)
        return True

    log.warning(
        "Could not find wishlist button on game page for '%s' at %s. "
        "The Backloggd UI may have changed — please report this.",
        game_name,
        game_url,
    )
    return False
