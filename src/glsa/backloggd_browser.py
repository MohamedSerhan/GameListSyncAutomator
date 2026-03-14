"""Backloggd browser automation for adding games to wishlist.

Uses Playwright with a persistent browser context so the user only
needs to log in once.
"""

import asyncio
import logging
from pathlib import Path

from playwright.async_api import BrowserContext, Page, async_playwright

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


async def _is_logged_in(page: Page) -> bool:
    """Check if the user is logged into Backloggd by checking the current page."""
    sign_in_link = await page.query_selector("a[href='/users/sign_in']")
    return sign_in_link is None


async def _login(
    page: Page, username: str, password: str, manual: bool = False
) -> None:
    """Log into Backloggd on the given page."""
    await page.goto(LOGIN_URL, timeout=PAGE_TIMEOUT_MS)
    await page.wait_for_load_state("domcontentloaded")

    if not manual and username and password:
        log.info("Logging into Backloggd automatically...")
        await page.fill("#user_login", username)
        await page.fill("#user_password", password)
        remember = await page.query_selector("#user_remember_me")
        if remember and not await remember.is_checked():
            await remember.check()
        await page.click("button[type='submit'][name='commit']")

        try:
            await page.wait_for_url(
                lambda url: "/users/sign_in" not in url,
                timeout=PAGE_TIMEOUT_MS,
            )
        except Exception:
            alert = await page.query_selector(".alert")
            msg = await alert.inner_text() if alert else "Unknown error"
            raise RuntimeError(f"Backloggd login failed: {msg.strip()}")
        log.info("Backloggd login successful!")
    else:
        log.info("Please log into Backloggd in the browser window...")
        await page.wait_for_url(
            lambda url: "/users/sign_in" not in url,
            timeout=LOGIN_TIMEOUT_MS,
        )
        log.info("Backloggd login successful!")


async def ensure_backloggd_login(
    browser_data_dir: str,
    username: str = "",
    password: str = "",
) -> None:
    """Log into Backloggd. Used by `glsa login backloggd`."""
    pw, context = await _launch_context(
        browser_data_dir, headless=bool(username and password)
    )
    try:
        page = await context.new_page()
        await page.goto(BASE_URL, timeout=PAGE_TIMEOUT_MS)
        await page.wait_for_load_state("domcontentloaded")

        if await _is_logged_in(page):
            log.info("Already logged into Backloggd")
        else:
            await _login(page, username, password)

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

    Handles login within the same browser context to avoid session issues.
    Returns list of game names that were successfully added.
    """
    if not game_names:
        return []

    pw, context = await _launch_context(browser_data_dir, headless=False)
    succeeded: list[str] = []
    try:
        page = await context.new_page()

        # Check login and log in if needed (same context)
        await page.goto(BASE_URL, timeout=PAGE_TIMEOUT_MS)
        await page.wait_for_load_state("domcontentloaded")
        if not await _is_logged_in(page):
            await _login(page, username, password)

        # Dismiss cookie banner if present
        try:
            cookie_btn = await page.wait_for_selector(
                "#cookie-banner-accept", timeout=3000
            )
            if cookie_btn:
                await cookie_btn.click(force=True)
                await asyncio.sleep(1)
        except Exception:
            pass

        for game_name in game_names:
            try:
                result = await _add_single_game(page, game_name)
                if result == "added":
                    succeeded.append(game_name)
                    log.info("Added '%s' to Backloggd wishlist", game_name)
                elif result == "already":
                    log.info("'%s' already on Backloggd wishlist", game_name)
                elif result == "not_found":
                    log.warning("Game not found on Backloggd: '%s'", game_name)
                else:
                    log.warning(
                        "Could not add '%s' to Backloggd wishlist", game_name
                    )
            except Exception:
                log.warning(
                    "Failed to add '%s' to Backloggd", game_name, exc_info=True
                )

            await asyncio.sleep(delay)

        await page.close()
    finally:
        await context.close()
        await pw.stop()

    return succeeded


async def _add_single_game(page: Page, game_name: str) -> str:
    """Search for a game on Backloggd and add it to wishlist.

    Returns:
        "added" - successfully added to wishlist
        "already" - already on wishlist
        "not_found" - game not found in search results
        "no_button" - game page found but no wishlist button
    """
    # Search for the game
    search_url = f"{BASE_URL}/search/games/{game_name}/"
    await page.goto(search_url, timeout=PAGE_TIMEOUT_MS)
    await page.wait_for_load_state("networkidle")

    # Find the first actual game result link (skip nav/library links)
    game_links = await page.query_selector_all("a[href*='/games/']")
    game_href = None
    for link in game_links:
        href = await link.get_attribute("href") or ""
        # Skip navigation links
        if "/games/lib/" in href or "/search/" in href:
            continue
        if href.startswith("/games/"):
            game_href = href
            break

    if not game_href:
        return "not_found"

    # Navigate to the game page
    await page.goto(f"{BASE_URL}{game_href}", timeout=PAGE_TIMEOUT_MS)
    await page.wait_for_load_state("networkidle")

    # Find the visible Wishlist button
    # Backloggd uses: button.button-link.btn-play inside a
    # div.wishlist-btn-container. The parent has "btn-play-fill"
    # class when the game is already on the wishlist.
    wishlist_btns = await page.query_selector_all("button.button-link.btn-play")
    for btn in wishlist_btns:
        text = (await btn.inner_text()).strip()
        if text != "Wishlist":
            continue
        if not await btn.is_visible():
            continue

        # Check if already on wishlist via parent's class
        parent_cls = await btn.evaluate("el => el.parentElement.className")
        if "btn-play-fill" in parent_cls:
            return "already"

        # Click to add to wishlist
        await btn.click()
        await asyncio.sleep(2)

        # Verify it worked
        parent_cls_after = await btn.evaluate("el => el.parentElement.className")
        if "btn-play-fill" in parent_cls_after:
            return "added"

        log.warning("Clicked wishlist button but state didn't change")
        return "no_button"

    return "no_button"
