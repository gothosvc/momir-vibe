"""
The local API server.

    GET /                               -> the card-mockup web page (static/)
    GET /cards/generate?mana_value=4    -> a single generated Card
    GET /cards/decode?code=...          -> reconstruct a Card from its share_code
    POST /cards/save                    -> persist a share_code, get a short id back
    GET /c/{id}                         -> the Card saved under that short id
    GET /health                         -> liveness check
    GET /docs                           -> interactive API docs (Swagger UI)

Run with:  python -m momir.main   (see main.py)
"""
from __future__ import annotations

import pathlib
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from . import store
from .card_builder import MAX_MANA_VALUE, MIN_MANA_VALUE, get_generator
from .codec import CardCodeError, decode_card
from .corpus import SUPPORTED_FORMATS, get_corpus
from .models import Card, SaveCardRequest, SaveCardResponse

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
store.init_db()

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


@app.get("/health")
def health() -> dict:
    raw_cards = get_corpus().raw_cards
    cards_by_format = {
        fmt: sum(1 for card in raw_cards if fmt in (card.get("legal_formats") or []))
        for fmt in SUPPORTED_FORMATS
    }
    return {"status": "ok", "cards_in_corpus": len(raw_cards), "cards_by_format": cards_by_format}


@app.get("/cards/generate", response_model=Card)
def generate_card(mana_value: int = MANA_VALUE_QUERY, format: Format | None = FORMAT_QUERY) -> Card:
    try:
        return get_generator(format).generate(mana_value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/cards/decode", response_model=Card)
def decode_card_endpoint(
    code: str = Query(..., description="A card's share_code, from a previously generated Card."),
) -> Card:
    """Reconstruct a previously generated card from its share_code. Pure
    decode -- no regeneration, no corpus/RNG involved -- so it always
    reproduces exactly the card the code came from, see momir/codec.py."""
    try:
        return decode_card(code)
    except CardCodeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/cards/save", response_model=SaveCardResponse)
def save_card(request: SaveCardRequest) -> SaveCardResponse:
    """Persist a card's share_code and hand back a short id for it, so a
    shareable link can be `/c/<id>` instead of the full share_code. Purely
    additive over /cards/decode -- the id is just a lookup for the same
    share_code, see momir/store.py."""
    try:
        card = decode_card(request.share_code)
    except CardCodeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SaveCardResponse(id=store.save_card(card))


@app.get("/c/{card_id}", response_model=Card)
def get_saved_card(card_id: str) -> Card:
    """Look up a card previously persisted via POST /cards/save."""
    share_code = store.get_share_code(card_id)
    if share_code is None:
        raise HTTPException(status_code=404, detail="No saved card with that id.")
    return decode_card(share_code)


# Mounted last and at "/" so it only ever catches requests the routes above
# didn't -- everything under static/ (index.html at "/", plus style.css /
# app.js by filename), same-origin with the API so the page's fetch() calls
# need no CORS setup.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
