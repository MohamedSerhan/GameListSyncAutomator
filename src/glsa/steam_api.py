import json
import logging
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

STEAM_API_BASE = "https://api.steampowered.com"
STORE_BASE = "https://store.steampowered.com"
CACHE_MAX_AGE_DAYS = 7


def get_steam_wishlist(api_key: str, steam_id: str) -> dict[int, str]:
    """Return {app_id: game_name} for the user's current Steam wishlist."""
    # Try undocumented endpoint first (returns names directly)
    wishlist = _get_wishlist_store(steam_id)
    if wishlist is not None:
        return wishlist

    # Fall back to official API (returns only app IDs)
    log.info("Store endpoint failed, falling back to official API")
    return _get_wishlist_official(api_key, steam_id)


def _get_wishlist_store(steam_id: str) -> dict[int, str] | None:
    """Try the undocumented store wishlist endpoint that includes game names."""
    wishlist: dict[int, str] = {}
    page = 0
    try:
        with httpx.Client(timeout=30) as client:
            while True:
                resp = client.get(
                    f"{STORE_BASE}/wishlist/profiles/{steam_id}/wishlistdata/",
                    params={"p": page},
                )
                if resp.status_code != 200:
                    return None

                data = resp.json()
                if not data or (isinstance(data, dict) and "success" in data):
                    break

                for app_id_str, info in data.items():
                    app_id = int(app_id_str)
                    name = info.get("name", f"Unknown ({app_id})")
                    wishlist[app_id] = name

                page += 1
                time.sleep(0.5)
    except Exception:
        log.debug("Store wishlist endpoint failed", exc_info=True)
        return None

    return wishlist


def _get_wishlist_official(api_key: str, steam_id: str) -> dict[int, str]:
    """Use the official IWishlistService API (returns app IDs without names)."""
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{STEAM_API_BASE}/IWishlistService/GetWishlist/v1/",
            params={"steamid": steam_id, "key": api_key},
        )
        resp.raise_for_status()
        data = resp.json()

    items = data.get("response", {}).get("items", [])
    # Returns {app_id: ""} — names will be filled in by the caller using the app list
    return {item["appid"]: "" for item in items}


def get_steam_app_list(cache_dir: str, api_key: str = "") -> dict[str, int]:
    """Return {normalized_name: app_id} for all Steam apps. Cached locally."""
    cache_path = Path(cache_dir) / "steam_apps.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    # Use cache if fresh
    if cache_path.exists():
        age_days = (time.time() - cache_path.stat().st_mtime) / 86400
        if age_days < CACHE_MAX_AGE_DAYS:
            log.info("Using cached Steam app list (%.1f days old)", age_days)
            with open(cache_path) as f:
                return json.load(f)

    log.info("Fetching Steam app list (this may take a moment)...")
    apps: list[dict] = []
    with httpx.Client(timeout=60) as client:
        last_appid = 0
        while True:
            params: dict = {
                "max_results": 50000,
                "include_games": "true",
                "include_dlc": "false",
                "include_hardware": "false",
            }
            if api_key:
                params["key"] = api_key
            if last_appid:
                params["last_appid"] = last_appid
            resp = client.get(
                f"{STEAM_API_BASE}/IStoreService/GetAppList/v1/",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

            batch = data.get("response", {}).get("apps", [])
            if not batch:
                break
            apps.extend(batch)
            # If there are more results, use have_more_results flag
            if not data.get("response", {}).get("have_more_results", False):
                break
            last_appid = batch[-1]["appid"]
            log.debug("Fetched %d apps so far...", len(apps))

    # Build normalized name -> app_id mapping
    # For duplicate names, keep the higher app_id (more recent)
    app_map: dict[str, int] = {}
    for app in apps:
        name = app.get("name", "").strip()
        if not name:
            continue
        normalized = name.lower()
        existing_id = app_map.get(normalized)
        if existing_id is None or app["appid"] > existing_id:
            app_map[normalized] = app["appid"]

    # Cache it
    with open(cache_path, "w") as f:
        json.dump(app_map, f)

    log.info("Cached %d Steam apps", len(app_map))
    return app_map


def fill_wishlist_names(
    wishlist: dict[int, str], app_map: dict[str, int]
) -> dict[int, str]:
    """Fill in missing names in the wishlist using the app list."""
    # Build reverse map: app_id -> name
    id_to_name: dict[int, str] = {}
    for name, app_id in app_map.items():
        id_to_name[app_id] = name

    filled: dict[int, str] = {}
    for app_id, name in wishlist.items():
        if name:
            filled[app_id] = name
        else:
            filled[app_id] = id_to_name.get(app_id, f"Unknown ({app_id})")

    return filled
