import asyncio
import logging
from pathlib import Path

from playwright.async_api import async_playwright, BrowserContext

log = logging.getLogger(__name__)

STORE_URL = "https://store.steampowered.com"
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
    """Check if the user is logged into Steam."""
    page = await context.new_page()
    try:
        await page.goto(STORE_URL, timeout=PAGE_TIMEOUT_MS)
        # Logged-in users have an account pulldown with their name
        account = await page.query_selector("#account_pulldown")
        return account is not None
    finally:
        await page.close()


async def ensure_steam_login(browser_data_dir: str) -> None:
    """Open a visible browser for manual Steam login if not already logged in."""
    pw, context = await _launch_context(browser_data_dir, headless=False)
    try:
        if await _is_logged_in(context):
            log.info("Already logged into Steam")
            return

        log.info("Please log into Steam in the browser window...")
        page = await context.new_page()
        await page.goto(f"{STORE_URL}/login/", timeout=PAGE_TIMEOUT_MS)

        # Wait for the user to complete login (account pulldown appears)
        await page.wait_for_selector(
            "#account_pulldown",
            timeout=LOGIN_TIMEOUT_MS,
        )
        log.info("Steam login successful!")
        await page.close()
    finally:
        await context.close()
        await pw.stop()


async def _handle_age_gate(page) -> None:
    """Handle Steam age verification gates."""
    # Check for age gate select (dropdown style)
    age_select = await page.query_selector("#ageYear")
    if age_select:
        await age_select.select_option("2000")
        btn = await page.query_selector("#view_product_page_btn")
        if btn:
            await btn.click()
            await page.wait_for_load_state("domcontentloaded")
            return

    # Check for age gate button (simple "Enter" style)
    enter_btn = await page.query_selector("a.btnv6_blue_hoverfade[href*='agecheck']")
    if enter_btn:
        await enter_btn.click()
        await page.wait_for_load_state("domcontentloaded")


async def add_to_wishlist(
    browser_data_dir: str,
    app_ids: list[int],
    delay: float = 2.0,
) -> list[int]:
    """Add games to Steam wishlist. Returns list of app_ids that succeeded."""
    if not app_ids:
        return []

    pw, context = await _launch_context(browser_data_dir, headless=True)
    succeeded: list[int] = []
    try:
        page = await context.new_page()
        for app_id in app_ids:
            try:
                url = f"{STORE_URL}/app/{app_id}"
                await page.goto(url, timeout=PAGE_TIMEOUT_MS)
                await _handle_age_gate(page)

                # Check if already on wishlist
                success_area = await page.query_selector("#add_to_wishlist_area_success")
                if success_area:
                    style = await success_area.get_attribute("style")
                    if style and "display: none" not in style:
                        log.info("App %d already on wishlist, skipping", app_id)
                        succeeded.append(app_id)
                        await asyncio.sleep(delay)
                        continue

                # Click "Add to your wishlist"
                add_btn = await page.query_selector("#add_to_wishlist_area")
                if add_btn:
                    await add_btn.click()
                    # Wait for success indicator
                    await page.wait_for_selector(
                        "#add_to_wishlist_area_success:not([style*='display: none'])",
                        timeout=10_000,
                    )
                    log.info("Added app %d to wishlist", app_id)
                    succeeded.append(app_id)
                else:
                    log.warning("No wishlist button found for app %d", app_id)

            except Exception:
                log.warning("Failed to add app %d to wishlist", app_id, exc_info=True)

            await asyncio.sleep(delay)

        await page.close()
    finally:
        await context.close()
        await pw.stop()

    return succeeded


async def remove_from_wishlist(
    browser_data_dir: str,
    app_ids: list[int],
    delay: float = 2.0,
) -> list[int]:
    """Remove games from Steam wishlist. Returns list of app_ids that succeeded."""
    if not app_ids:
        return []

    pw, context = await _launch_context(browser_data_dir, headless=True)
    succeeded: list[int] = []
    try:
        page = await context.new_page()
        for app_id in app_ids:
            try:
                url = f"{STORE_URL}/app/{app_id}"
                await page.goto(url, timeout=PAGE_TIMEOUT_MS)
                await _handle_age_gate(page)

                # Click "On Wishlist" button to remove
                on_wishlist = await page.query_selector("#add_to_wishlist_area_success")
                if on_wishlist:
                    style = await on_wishlist.get_attribute("style")
                    if style and "display: none" in style:
                        log.info("App %d not on wishlist, skipping", app_id)
                        succeeded.append(app_id)
                        await asyncio.sleep(delay)
                        continue

                    await on_wishlist.click()
                    # Wait for the add button to reappear (confirms removal)
                    await page.wait_for_selector(
                        "#add_to_wishlist_area:not([style*='display: none'])",
                        timeout=10_000,
                    )
                    log.info("Removed app %d from wishlist", app_id)
                    succeeded.append(app_id)
                else:
                    log.warning("No wishlist button found for app %d", app_id)

            except Exception:
                log.warning("Failed to remove app %d from wishlist", app_id, exc_info=True)

            await asyncio.sleep(delay)

        await page.close()
    finally:
        await context.close()
        await pw.stop()

    return succeeded
