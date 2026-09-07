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
    power: int | str = Field(..., description="'*' when rules_text defines it (e.g. \"~'s power is equal to...\")")
    toughness: int | str = Field(..., description="'*' when rules_text defines it (e.g. \"~'s toughness is equal to...\")")
    keywords: list[str] = Field(default_factory=list)
    rules_text: list[str] = Field(default_factory=list)
    set_name: str = "Momir Vibe"
    set_code: str = "MMV"
    collector_number: str
    artist: str = "Markov Chain Studios"
    art_url: str | None = Field(
        None,
        description="Art crop from a real creature sharing this card's color "
        "identity -- unrelated to this card's name/text, picked only for a "
        "thematically plausible picture. None if the corpus has no art data "
        "(cache predates this feature) -- see momir/art.py.",
    )
