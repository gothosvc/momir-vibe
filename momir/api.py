"""
The local API server.

    GET /                               -> the card-mockup web page (static/)
    GET /cards/generate?mana_value=4    -> a single generated Card (optional &mayhem=text|full|unhinged)
    GET /cards/generate/image?mana_value=4  -> the same, rendered as a B&W PNG for thermal-printed proxies
    GET /health                         -> liveness check
    GET /docs                           -> interactive API docs (Swagger UI)

Run with:  python -m momir.main   (see main.py)
"""
from __future__ import annotations

import pathlib
import random
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.staticfiles import StaticFiles

from . import render
from .card_builder import MAX_MANA_VALUE, MIN_MANA_VALUE, Mayhem, get_generator
from .corpus import SUPPORTED_FORMATS, get_corpus
from .models import Card

STATIC_DIR = pathlib.Path(__file__).parent.parent / "static"

app = FastAPI(
    title="Momir Vibe",
    description="Generates fake Magic: The Gathering creature cards for Momir-style play.",
    version="0.1.0",
)

# The unrestricted (no format filter) generator is trained eagerly at
# startup so the first request doesn't pay that cost; format-scoped
# generators are built lazily on first use and cached from then on -- see
# card_builder.get_generator.
get_generator()

Format = Literal["standard", "pioneer", "modern"]

MANA_VALUE_QUERY = Query(
    ...,
    ge=MIN_MANA_VALUE,
    le=MAX_MANA_VALUE,
    description="Mana value (converted mana cost) to generate a creature for, e.g. from a Momir Vig activation.",
)
FORMAT_QUERY = Query(
    None,
    description="Restrict training data to cards legal in this format. Omit for the full, unrestricted card pool.",
)
MAYHEM_QUERY = Query(
    "off",
    description="Break mana-value scoping on purpose. 'text' samples rules text/keywords from the whole "
    "(format-filtered) pool instead of just this mana value; 'full' also does that for mana cost, "
    "power/toughness, and type line (still favoring nearby mana values), so the card can come back "
    "off-curve; 'unhinged' is 'full' but with that nearby-mana-value favoring dropped too (every mana "
    "value equally likely to be sampled from) and at least one line of rules text forced instead of left "
    "to chance. 'off' (default) is normal, curve-appropriate generation.",
)
SEED_QUERY = Query(
    None,
    description="Replay a specific generation instead of a fresh random one -- every card generated from "
    "the same (seed, mana_value, format, mayhem) is identical (down to art/keywords/rules text), as long "
    "as the corpus hasn't changed since (a server restart or re-fetch can invalidate this). Omit for a "
    "fresh random card; GET /cards/generate returns the seed it used in the X-Momir-Seed response header "
    "so a caller can request the exact same card again, e.g. as a printable image.",
)


def _new_rng(seed: int | None) -> tuple[random.Random, int]:
    """(rng, seed) -- picks a fresh random seed if none was given, so the
    caller always has one to hand back to the client even for an ordinary
    (non-replayed) request."""
    seed = seed if seed is not None else random.getrandbits(64)
    return random.Random(seed), seed


@app.get("/health")
def health() -> dict:
    raw_cards = get_corpus().raw_cards
    cards_by_format = {
        fmt: sum(1 for card in raw_cards if fmt in (card.get("legal_formats") or []))
        for fmt in SUPPORTED_FORMATS
    }
    return {"status": "ok", "cards_in_corpus": len(raw_cards), "cards_by_format": cards_by_format}


@app.get("/cards/generate", response_model=Card)
def generate_card(
    response: Response,
    mana_value: int = MANA_VALUE_QUERY,
    format: Format | None = FORMAT_QUERY,
    mayhem: Mayhem = MAYHEM_QUERY,
    seed: int | None = SEED_QUERY,
) -> Card:
    rng, seed = _new_rng(seed)
    try:
        card = get_generator(format).generate(mana_value, rng=rng, mayhem=mayhem)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response.headers["X-Momir-Seed"] = str(seed)
    return card


@app.get("/cards/generate/image")
def generate_card_image(
    mana_value: int = MANA_VALUE_QUERY,
    format: Format | None = FORMAT_QUERY,
    mayhem: Mayhem = MAYHEM_QUERY,
    seed: int | None = SEED_QUERY,
) -> Response:
    """A rendered B&W, card-shaped PNG for a thermal printer that expects
    to pull a card *image* rather than JSON. Without a seed, a fresh
    random card each time (same relationship to /cards/generate as
    Scryfall's own /cards/random); with one, an exact replay of a specific
    card seen before (see SEED_QUERY) -- e.g. the mockup page's "Printable
    image" button passing back the seed /cards/generate just handed it, to
    print the same card rather than a new random one. Rendered straight
    into memory (see render.py) -- nothing is ever written to disk."""
    rng, seed = _new_rng(seed)
    try:
        card = get_generator(format).generate(mana_value, rng=rng, mayhem=mayhem)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(content=render.render_card_png(card), media_type="image/png")


# Mounted last and at "/" so it only ever catches requests the routes above
# didn't -- everything under static/ (index.html at "/", plus style.css /
# app.js by filename), same-origin with the API so the page's fetch() calls
# need no CORS setup.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
