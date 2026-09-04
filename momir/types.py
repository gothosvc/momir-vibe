"""Creature type line generation."""
from __future__ import annotations

import random

from .corpus import Corpus, subtype_pool

# Momir games are famous for absurd type combos ("Zombie Dragon Wizard"), so
# we don't try to keep subtype pairings "sensible" -- we just weight by how
# often each subtype shows up at a similar mana value and let chaos reign.
SECOND_TYPE_CHANCE = 0.4


def generate_type_line(
    corpus: Corpus, mana_value: int, rng: random.Random | None = None, mayhem: bool = False, weighted: bool = True
) -> str:
    rng = rng or random
    pool = subtype_pool(corpus, mana_value, mayhem=mayhem, weighted=weighted)
    if not pool:
        return "Creature"

    subtypes = list(pool.keys())
    weights = list(pool.values())

    chosen = [rng.choices(subtypes, weights=weights)[0]]
    if rng.random() < SECOND_TYPE_CHANCE and len(subtypes) > 1:
        second = rng.choices(subtypes, weights=weights)[0]
        if second != chosen[0]:
            chosen.append(second)

    return f"Creature — {' '.join(chosen)}"
