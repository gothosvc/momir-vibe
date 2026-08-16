"""Pydantic models for the generated card and API request/response shapes."""
from __future__ import annotations

from pydantic import BaseModel, Field

COLOR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}


class Card(BaseModel):
    name: str
    mana_cost: str = Field(..., description="Cost string, e.g. '{2}{R}{R}'")
    mana_value: int = Field(..., description="Converted mana cost / mana value")
    colors: list[str] = Field(default_factory=list, description="Color letters, e.g. ['R']")
    color_identity: list[str] = Field(default_factory=list)
    type_line: str
    power: int
    toughness: int
    keywords: list[str] = Field(default_factory=list)
    rules_text: list[str] = Field(default_factory=list)
    flavor_text: str | None = None
    rarity: str
    set_name: str = "Momir Vibe"
    set_code: str = "MMV"
    collector_number: str
    artist: str = "Markov Chain Studios"
    share_code: str | None = Field(
        None,
        description="Opaque string encoding this exact card. Feed it back into "
        "GET /cards/decode to reconstruct this card later, with no regeneration "
        "involved -- see momir/codec.py.",
    )


class MatchPair(BaseModel):
    player_one: Card
    player_two: Card


class SaveCardRequest(BaseModel):
    share_code: str = Field(..., description="A card's share_code to persist under a short id.")


class SaveCardResponse(BaseModel):
    id: str = Field(..., description="Short id for this card -- fetch it back via GET /c/{id}.")
