"""
One-time (or occasional) fetch of real creature card data from the Scryfall API.

This is the *only* place in the project that talks to the network. Everything
downstream (Markov training, card generation, the API server) runs entirely
offline against the cached JSON this script produces.

Usage:
    python -m data.fetch_cards
"""
from __future__ import annotations

import json
import pathlib
import time

import requests

CACHE_PATH = pathlib.Path(__file__).parent / "cards_cache.json"
SEARCH_URL = "https://api.scryfall.com/cards/search"

# Scryfall rejects requests with a generic/default User-Agent (see
# https://scryfall.com/docs/api). Identify ourselves as their guidelines ask.
HEADERS = {
    "User-Agent": "MomirVibeCardGenerator/0.1 (personal hobby project)",
    "Accept": "application/json",
}

# Only real, paper-legal creature cards, deduplicated by oracle text (so we
# don't train on the same creature twice just because it was reprinted).
QUERY = "type:creature game:paper -is:funny"

# Fields we actually need to train the generator. Scryfall cards carry a lot
# of printing-specific metadata (set, image URIs, prices, legality, ...) that
# we don't want to store or train on.
KEEP_FIELDS = (
    "name",
    "mana_cost",
    "cmc",
    "type_line",
    "oracle_text",
    "power",
    "toughness",
    "colors",
    "keywords",
    "rarity",
)


def fetch_all(max_cards: int | None = None) -> list[dict]:
    cards: list[dict] = []
    url = SEARCH_URL
    params: dict | None = {"q": QUERY, "unique": "cards", "order": "name"}

    while url:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        payload = resp.json()

        for raw in payload.get("data", []):
            # Skip double-faced/split cards' front-only weirdness and cards
            # missing the fields we need to train on.
            if "power" not in raw or "toughness" not in raw:
                continue
            trimmed = {k: raw.get(k) for k in KEEP_FIELDS}
            cards.append(trimmed)

        if max_cards is not None and len(cards) >= max_cards:
            cards = cards[:max_cards]
            break

        if payload.get("has_more"):
            url = payload["next_page"]
            params = None  # next_page URL already has query params baked in
        else:
            url = None

        # Be polite to Scryfall's API (they ask for 50-100ms between requests).
        time.sleep(0.1)

    return cards


def main() -> None:
    print(f"Fetching creature cards from Scryfall ({QUERY!r})...")
    cards = fetch_all()
    print(f"Fetched {len(cards)} unique creature cards.")

    CACHE_PATH.write_text(json.dumps(cards, indent=0))
    print(f"Wrote cache to {CACHE_PATH} ({CACHE_PATH.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
