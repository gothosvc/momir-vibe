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
    "User-Agent": "MomirVibeCardGenerator/0.1 (personal hobby project; https://github.com/gothosvc/momir-vibe)",
    "Accept": "application/json",
}

# Only real, paper-legal creature cards, deduplicated by oracle text (so we
# don't train on the same creature twice just because it was reprinted).
QUERY = "type:creature game:paper -is:funny"

# Fields we actually need to train the generator. Scryfall cards carry a lot
# of printing-specific metadata (set, most of image_uris, prices, ...) that
# we don't want to store or train on. "artist" is the one addition kept
# purely for display, never training -- see below, and momir/art.py.
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
    "artist",
)

# Formats corpus.py can filter the training data down to (see its
# SUPPORTED_FORMATS -- keep the two in sync if this changes). Scryfall's full
# `legalities` object has ~15 formats; we only ever want a handful, so we
# trim it down to a plain list of "legal in" names at fetch time rather than
# storing the whole dict per card.
TRACKED_FORMATS = ("standard", "pioneer", "modern")


MAX_RETRIES = 8
MAX_BACKOFF_SECONDS = 30


def _get_with_retry(url: str, params: dict | None) -> dict:
    """Scryfall's rate limit is a soft, bursty one -- a 429 here means back
    off and retry rather than give up, since the inter-request sleep below
    is only a courtesy minimum, not a guarantee against ever tripping it
    (it seems to deplete over a sustained run even while staying under
    that per-request minimum, rather than being a hard immediate block)."""
    for attempt in range(MAX_RETRIES):
        resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
        if resp.status_code == 429 and attempt < MAX_RETRIES - 1:
            wait = min(2**attempt, MAX_BACKOFF_SECONDS)
            print(f"  Rate limited, retrying in {wait}s...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("unreachable")  # loop always returns or raises


def fetch_all(max_cards: int | None = None) -> list[dict]:
    cards: list[dict] = []
    url = SEARCH_URL
    params: dict | None = {"q": QUERY, "unique": "cards", "order": "name"}

    while url:
        payload = _get_with_retry(url, params)

        for raw in payload.get("data", []):
            # Skip double-faced/split cards' front-only weirdness and cards
            # missing the fields we need to train on.
            if "power" not in raw or "toughness" not in raw:
                continue
            trimmed = {k: raw.get(k) for k in KEEP_FIELDS}
            # The art box alone (no card frame), for momir/art.py to hand
            # back as a generated card's picture -- see corpus.py's
            # art_by_colors/all_art. The only other image_uris entries are
            # full-card renders we have no use for, so this one field is
            # pulled out individually rather than adding all of image_uris.
            trimmed["art_crop_url"] = (raw.get("image_uris") or {}).get("art_crop")
            legalities = raw.get("legalities") or {}
            trimmed["legal_formats"] = [fmt for fmt in TRACKED_FORMATS if legalities.get(fmt) == "legal"]
            cards.append(trimmed)

        if max_cards is not None and len(cards) >= max_cards:
            cards = cards[:max_cards]
            break

        if payload.get("has_more"):
            url = payload["next_page"]
            params = None  # next_page URL already has query params baked in
        else:
            url = None

        # Be polite to Scryfall's API (they ask for 50-100ms between
        # requests; leaning toward the slower end since the retry loop above
        # shows the limit can still get tripped over a long sustained run).
        time.sleep(0.15)

    return cards


def main() -> None:
    print(f"Fetching creature cards from Scryfall ({QUERY!r})...")
    cards = fetch_all()
    print(f"Fetched {len(cards)} unique creature cards.")

    CACHE_PATH.write_text(json.dumps(cards, indent=0))
    print(f"Wrote cache to {CACHE_PATH} ({CACHE_PATH.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
