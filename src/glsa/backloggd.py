import logging
import time

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

BASE_URL = "https://backloggd.com"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
PAGE_DELAY = 0.5  # seconds between page requests

# Bunny Shield (Backloggd's anti-bot layer, similar to Cloudflare) serves a
# JS proof-of-work challenge page instead of the real HTML. httpx cannot solve
# it. These markers identify the challenge so we fail loudly instead of
# silently treating the empty challenge page as "user has 0 games".
_CHALLENGE_MARKERS = (
    "bunny-shield",
    "Establishing a secure connection",
    "shield-challenge.js",
)


class BackloggdChallengeError(RuntimeError):
    """Raised when Backloggd returns an anti-bot challenge page instead of content."""


def _is_challenge_page(html: str) -> bool:
    # Challenge pages are tiny (~1-2 KB) and contain the shield markers.
    # Real profile pages are 100+ KB.
    if len(html) > 10_000:
        return False
    return any(marker in html for marker in _CHALLENGE_MARKERS)


def get_backloggd_games(username: str, status: str) -> list[str]:
    """Return list of game titles for a given status type.

    Status can be: wishlist, played, playing, backlog
    """
    url = f"{BASE_URL}/u/{username}/games/added:desc/type:{status}/"
    games: list[str] = []
    page = 1

    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=30,
    ) as client:
        while True:
            params = {"page": page} if page > 1 else {}
            log.debug("Fetching %s page %d", status, page)

            resp = client.get(url, params=params)

            if resp.status_code == 404:
                if page == 1:
                    raise ValueError(
                        f"Backloggd user '{username}' not found or has no {status} games. "
                        f"Check the username and ensure your profile is public."
                    )
                break

            resp.raise_for_status()
            if _is_challenge_page(resp.text):
                raise BackloggdChallengeError(
                    f"Backloggd returned an anti-bot challenge page for "
                    f"{status} page {page} (Bunny Shield). httpx cannot solve it — "
                    f"use the Playwright-based scraper instead."
                )
            soup = BeautifulSoup(resp.text, "lxml")

            # Game cards are in div.rating-hover containers
            cards = soup.select("div.rating-hover")
            if not cards:
                if page == 1:
                    log.info("No %s games found for user '%s'", status, username)
                break

            for card in cards:
                # Title is in div.game-text-centered
                title_el = card.select_one("div.game-text-centered")
                if title_el:
                    title = title_el.get_text(strip=True)
                    if title:
                        games.append(title)

            # Check if there's a next page
            pagination = soup.select_one("nav.pagy")
            has_next = False
            if pagination:
                next_link = pagination.find("a", string=lambda s: s and "Next" in s)
                if next_link and next_link.get("href"):
                    has_next = True

            if not has_next:
                break

            page += 1
            time.sleep(PAGE_DELAY)

    log.info("Found %d %s games for '%s'", len(games), status, username)
    return games
