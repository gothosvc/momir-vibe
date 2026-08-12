"""
The local API server.

    GET /cards/generate?mana_value=4   -> a single generated Card
    GET /momir/match?mana_value=4      -> a Card for each of two players
    GET /health                        -> liveness check

Run with:  python -m momir.main   (see main.py)
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse

from .card_builder import MAX_MANA_VALUE, MIN_MANA_VALUE, CardGenerator
from .models import Card, MatchPair

app = FastAPI(
    title="Momir Vibe",
    description="Generates fake Magic: The Gathering creature cards for Momir-style play.",
    version="0.1.0",
)

# Trained once at process startup; reused (cheaply) for every request.
generator = CardGenerator()

MANA_VALUE_QUERY = Query(
    ...,
    ge=MIN_MANA_VALUE,
    le=MAX_MANA_VALUE,
    description="Mana value (converted mana cost) to generate a creature for, e.g. from a Momir Vig activation.",
)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "cards_in_corpus": len(generator.corpus.raw_cards)}


@app.get("/cards/generate", response_model=Card)
def generate_card(mana_value: int = MANA_VALUE_QUERY) -> Card:
    try:
        return generator.generate(mana_value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/momir/match", response_model=MatchPair)
def generate_match(mana_value: int = MANA_VALUE_QUERY) -> MatchPair:
    """Convenience endpoint: generate one card per player off the same mana value roll."""
    try:
        return MatchPair(
            player_one=generator.generate(mana_value),
            player_two=generator.generate(mana_value),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
