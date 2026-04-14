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


async def _get_session_id(context: BrowserContext) -> str | None:
    """Extract the Steam `sessionid` cookie from the persistent context.

    A persistent context loads profile cookies lazily — until a page visits
    the domain, `context.cookies()` returns []. We warm the cookie jar by
    opening (and closing) a page on store.steampowered.com first.
    """
    warmup = await context.new_page()
    try:
        await warmup.goto(STORE_URL, timeout=PAGE_TIMEOUT_MS)
    finally:
        await warmup.close()

    for cookie in await context.cookies():
        if cookie.get("name") == "sessionid" and "store.steampowered.com" in cookie.get("domain", ""):
            return cookie.get("value")
    return None


async def _api_add_to_wishlist(
    context: BrowserContext, session_id: str, app_id: int
) -> bool:
    """POST to Steam's /api/addtowishlist. Returns True on success.

    This is what the store page's own JS calls — far more reliable than
    clicking the DOM button, which has at least two store-page layouts
    (old `#add_to_wishlist_area`, new `#add_to_wishlist_area2` reminder box)
    and fails silently on the new one.
    """
    resp = await context.request.post(
        f"{STORE_URL}/api/addtowishlist",
        form={"sessionid": session_id, "appid": str(app_id)},
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Origin": STORE_URL,
            "Referer": f"{STORE_URL}/app/{app_id}",
        },
    )
    if not resp.ok:
        log.warning("Wishlist API returned HTTP %d for app %d", resp.status, app_id)
        return False
    try:
        data = await resp.json()
    except Exception:
        body = await resp.text()
        log.warning("Wishlist API non-JSON response for app %d: %s", app_id, body[:200])
        return False
    if not data.get("success"):
        log.warning("Wishlist API reported failure for app %d: %s", app_id, data)
        return False
    return True


async def _fetch_current_wishlist_ids(
    context: BrowserContext, session_id: str
) -> set[int] | None:
    """Fetch the logged-in user's current wishlist app IDs via the store API.

    Returns None if the request fails. The endpoint returns the user's
    wishlist without needing their steamid since the session cookie
    identifies them.
    """
    resp = await context.request.get(
        f"{STORE_URL}/dynamicstore/userdata/",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    if not resp.ok:
        return None
    try:
        data = await resp.json()
    except Exception:
        return None
    wishlist = data.get("rgWishlist") or []
    try:
        return {int(x) for x in wishlist}
    except (TypeError, ValueError):
        return None


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
        session_id = await _get_session_id(context)
        if not session_id:
            log.warning(
                "No Steam sessionid cookie found — run `glsa login steam` first. "
                "Falling back to DOM-click flow."
            )

        # Pre-fetch current wishlist so we can skip already-present apps.
        # Steam's add API returns success:false for duplicates, which would
        # otherwise look like a failure.
        current_wishlist: set[int] = set()
        if session_id:
            fetched = await _fetch_current_wishlist_ids(context, session_id)
            if fetched is not None:
                current_wishlist = fetched

        page = await context.new_page()
        for app_id in app_ids:
            try:
                if app_id in current_wishlist:
                    log.info("App %d already on wishlist, skipping", app_id)
                    succeeded.append(app_id)
                    await asyncio.sleep(delay)
                    continue

                # Primary path: hit Steam's wishlist API directly. Works
                # regardless of which store-page layout is served (old
                # `add_to_wishlist_area` button vs. newer `_area2` reminder).
                if session_id and await _api_add_to_wishlist(context, session_id, app_id):
                    log.info("Added app %d to wishlist (via API)", app_id)
                    succeeded.append(app_id)
                    current_wishlist.add(app_id)
                    await asyncio.sleep(delay)
                    continue

                # Fallback: legacy DOM click. Kept for cases where the API
                # rejects (age-restricted region gates, etc.).
                url = f"{STORE_URL}/app/{app_id}"
                await page.goto(url, timeout=PAGE_TIMEOUT_MS)
                await _handle_age_gate(page)

                success_area = await page.query_selector("#add_to_wishlist_area_success")
                if success_area:
                    style = await success_area.get_attribute("style")
                    if style and "display: none" not in style:
                        log.info("App %d already on wishlist, skipping", app_id)
                        succeeded.append(app_id)
                        await asyncio.sleep(delay)
                        continue

                add_btn = await page.query_selector("#add_to_wishlist_area")
                if add_btn:
                    await add_btn.click()
                    await page.wait_for_selector(
                        "#add_to_wishlist_area_success:not([style*='display: none'])",
                        timeout=10_000,
                    )
                    log.info("Added app %d to wishlist (via DOM fallback)", app_id)
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


async def _api_remove_from_wishlist(
    context: BrowserContext, session_id: str, app_id: int
) -> bool:
    """POST to Steam's /api/removefromwishlist. Returns True on success."""
    resp = await context.request.post(
        f"{STORE_URL}/api/removefromwishlist",
        form={"sessionid": session_id, "appid": str(app_id)},
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Origin": STORE_URL,
            "Referer": f"{STORE_URL}/app/{app_id}",
        },
    )
    if not resp.ok:
        log.warning("Wishlist remove API returned HTTP %d for app %d", resp.status, app_id)
        return False
    try:
        data = await resp.json()
    except Exception:
        return False
    return bool(data.get("success"))


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
        session_id = await _get_session_id(context)
        if not session_id:
            log.warning(
                "No Steam sessionid cookie found — run `glsa login steam` first. "
                "Falling back to DOM-click flow."
            )

        # Pre-fetch wishlist so we can short-circuit apps already absent.
        # Steam's remove API returns success:false when the app isn't there,
        # which would otherwise look like a failure.
        current_wishlist: set[int] = set()
        if session_id:
            fetched = await _fetch_current_wishlist_ids(context, session_id)
            if fetched is not None:
                current_wishlist = fetched

        page = await context.new_page()
        for app_id in app_ids:
            try:
                if current_wishlist and app_id not in current_wishlist:
                    log.info("App %d already not on wishlist, skipping", app_id)
                    succeeded.append(app_id)
                    await asyncio.sleep(delay)
                    continue

                if session_id and await _api_remove_from_wishlist(context, session_id, app_id):
                    log.info("Removed app %d from wishlist (via API)", app_id)
                    succeeded.append(app_id)
                    await asyncio.sleep(delay)
                    continue

                # Fallback: legacy DOM click.
                url = f"{STORE_URL}/app/{app_id}"
                await page.goto(url, timeout=PAGE_TIMEOUT_MS)
                await _handle_age_gate(page)

                on_wishlist = await page.query_selector("#add_to_wishlist_area_success")
                if on_wishlist:
                    style = await on_wishlist.get_attribute("style")
                    if style and "display: none" in style:
                        log.info("App %d not on wishlist, skipping", app_id)
                        succeeded.append(app_id)
                        await asyncio.sleep(delay)
                        continue

                    await on_wishlist.click()
                    await page.wait_for_selector(
                        "#add_to_wishlist_area:not([style*='display: none'])",
                        timeout=10_000,
                    )
                    log.info("Removed app %d from wishlist (via DOM fallback)", app_id)
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
