"""Card art selection.

Rather than generating an image (a whole separate, heavier project) or
inventing a placeholder, we borrow a real creature's art -- picked by
matching the generated card's color identity, so the picture reads as
thematically plausible even though it's for a different (fake) creature.
Same "sample from the real distribution" approach the rest of the card
uses (mana cost, type line, power/toughness), applied to art instead.

See corpus.py's art_by_colors/all_art (populated from data/fetch_cards.py's
art_crop_url/artist fields) for where the pool comes from.
"""
from __future__ import annotations

import random

from .corpus import Corpus


def select_art(
    corpus: Corpus, colors: list[str], rng: random.Random | None = None
) -> tuple[str, str] | None:
    """Returns (art_crop_url, artist) for a real creature sharing this exact
    color identity, falling back to any real creature's art if that exact
    combination has none of its own (uncommon, e.g. 4-5 color identities).

    Returns None if the corpus has no art data at all -- a cache fetched
    before data/fetch_cards.py started keeping art_crop_url/artist -- so
    card_builder.py can treat that as "no picture available" rather than
    an error; re-running the fetch is what turns this feature on.
    """
    rng = rng or random
    pool = corpus.art_by_colors.get(tuple(colors)) or corpus.all_art
    if not pool:
        return None
    return rng.choice(pool)
