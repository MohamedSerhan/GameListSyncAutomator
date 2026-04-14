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


CHALLENGE_TIMEOUT_MS = 45_000  # Bunny Shield proof-of-work takes a few seconds


async def _wait_for_challenge_clear(page: Page) -> None:
    """Block until the Bunny Shield challenge page navigates to real content.

    The challenge page has title "Establishing a secure connection ..."; once
    the JS proof-of-work completes it reloads into the real Backloggd HTML.
    Safe no-op if the page wasn't challenged (condition is already true).
    """
    await page.wait_for_function(
        "() => !document.title.includes('secure connection')",
        timeout=CHALLENGE_TIMEOUT_MS,
    )


async def scrape_games(
    browser_data_dir: str,
    username: str,
    password: str,
    statuses: list[str],
) -> dict[str, list[str]]:
    """Scrape a Backloggd user's game lists via Playwright.

    Backloggd uses Bunny Shield (a Cloudflare-style JS challenge) which:
      - blocks httpx entirely (no JS to solve the PoW)
      - blocks headless Chromium (fingerprint-detected; challenge never clears)
      - lets headful Chromium through (PoW clears in ~5s)

    So this runs headful. A browser window briefly appears for each sync.
    The `--headless=new` Chromium mode and Playwright's `context.request`
    HTTP client were both tested and still get challenge-blocked.

    Returns {status: [game_name, ...]} for each requested status.
    """
    if not statuses:
        return {}

    # Headful required — see docstring.
    pw, context = await _launch_context(browser_data_dir, headless=False)
    results: dict[str, list[str]] = {s: [] for s in statuses}
    try:
        page = await context.new_page()

        # Log in first so we have a session cookie + Bunny Shield clearance.
        await page.goto(BASE_URL, timeout=PAGE_TIMEOUT_MS)
        await _wait_for_challenge_clear(page)
        await page.wait_for_load_state("domcontentloaded")
        if not await _is_logged_in(page):
            await _login(page, username, password)

        for status in statuses:
            page_num = 1
            seen_titles: set[str] = set()
            while True:
                url = f"{BASE_URL}/u/{username}/games/added:desc/type:{status}/"
                if page_num > 1:
                    url += f"?page={page_num}"
                await page.goto(url, timeout=PAGE_TIMEOUT_MS)
                await _wait_for_challenge_clear(page)
                await page.wait_for_load_state("domcontentloaded")

                cards = await page.query_selector_all("div.rating-hover")
                if not cards:
                    break

                new_titles: list[str] = []
                for card in cards:
                    title_el = await card.query_selector("div.game-text-centered")
                    if not title_el:
                        continue
                    title = (await title_el.inner_text()).strip()
                    if title:
                        new_titles.append(title)

                # Backloggd clamps out-of-range page numbers to the last real
                # page (so ?page=999 returns the same cards as ?page=8).
                # Detect this by checking whether the current page contains
                # any titles we haven't already seen — if every title is a
                # duplicate, we've wrapped and should stop.
                fresh = [t for t in new_titles if t not in seen_titles]
                if not fresh:
                    break
                results[status].extend(fresh)
                seen_titles.update(fresh)

                # Primary termination: does the pagy nav expose a link to
                # page_num+1? If not, we're on the last page. This is more
                # efficient than navigating to an out-of-range page to find
                # out (which would burn a full challenge-clear cycle).
                next_link = await page.query_selector(
                    f"nav.pagy a[href*='page={page_num + 1}']"
                )
                if not next_link:
                    break
                page_num += 1

            log.info(
                "Scraped %d %s games for '%s' via Playwright",
                len(results[status]), status, username,
            )

        await page.close()
    finally:
        await context.close()
        await pw.stop()

    return results


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

    pw, context = await _launch_context(browser_data_dir, headless=True)
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


async def remove_from_wishlist(
    browser_data_dir: str,
    game_names: list[str],
    username: str = "",
    password: str = "",
    delay: float = 3.0,
) -> list[str]:
    """Search Backloggd for each game and remove it from wishlist.

    Returns list of game names that were successfully removed.
    """
    if not game_names:
        return []

    pw, context = await _launch_context(browser_data_dir, headless=True)
    succeeded: list[str] = []
    try:
        page = await context.new_page()

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
                result = await _remove_single_game(page, game_name)
                if result == "removed":
                    succeeded.append(game_name)
                    log.info("Removed '%s' from Backloggd wishlist", game_name)
                elif result == "not_on_wishlist":
                    log.info("'%s' not on Backloggd wishlist (already removed)", game_name)
                elif result == "not_found":
                    log.warning("Game not found on Backloggd: '%s'", game_name)
                else:
                    log.warning(
                        "Could not remove '%s' from Backloggd wishlist", game_name
                    )
            except Exception:
                log.warning(
                    "Failed to remove '%s' from Backloggd", game_name, exc_info=True
                )

            await asyncio.sleep(delay)

        await page.close()
    finally:
        await context.close()
        await pw.stop()

    return succeeded


async def _remove_single_game(page: Page, game_name: str) -> str:
    """Search for a game on Backloggd and remove it from wishlist.

    Returns:
        "removed" - successfully removed from wishlist
        "not_on_wishlist" - game found but not on wishlist
        "not_found" - game not found in search results
        "no_button" - game page found but no wishlist button
    """
    search_url = f"{BASE_URL}/search/games/{game_name}/"
    await page.goto(search_url, timeout=PAGE_TIMEOUT_MS)
    await page.wait_for_load_state("networkidle")

    game_links = await page.query_selector_all("a[href*='/games/']")
    game_href = None
    for link in game_links:
        href = await link.get_attribute("href") or ""
        if "/games/lib/" in href or "/search/" in href:
            continue
        if href.startswith("/games/"):
            game_href = href
            break

    if not game_href:
        return "not_found"

    await page.goto(f"{BASE_URL}{game_href}", timeout=PAGE_TIMEOUT_MS)
    await page.wait_for_load_state("networkidle")

    wishlist_btns = await page.query_selector_all("button.button-link.btn-play")
    for btn in wishlist_btns:
        text = (await btn.inner_text()).strip()
        if text != "Wishlist":
            continue
        if not await btn.is_visible():
            continue

        parent_cls = await btn.evaluate("el => el.parentElement.className")
        if "btn-play-fill" not in parent_cls:
            return "not_on_wishlist"

        # Click to remove from wishlist
        await btn.click()
        await asyncio.sleep(2)

        parent_cls_after = await btn.evaluate("el => el.parentElement.className")
        if "btn-play-fill" not in parent_cls_after:
            return "removed"

        log.warning("Clicked wishlist button but state didn't change")
        return "no_button"

    return "no_button"


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
