"""Creature type line generation."""
from __future__ import annotations

import random
from collections import Counter

from .corpus import Corpus

# Momir games are famous for absurd type combos ("Zombie Dragon Wizard"), so
# we don't try to keep subtype pairings "sensible" -- we just weight by how
# often each subtype shows up at a similar mana value and let chaos reign.
SECOND_TYPE_CHANCE = 0.4


def _subtype_pool(corpus: Corpus, mana_value: int, mayhem: bool = False):
    if mayhem:
        return sum(corpus.subtypes_by_cmc.values(), Counter())

    pool = corpus.subtypes_by_cmc.get(mana_value)
    if pool:
        return pool

    available = [cmc for cmc, counter in corpus.subtypes_by_cmc.items() if counter]
    if not available:
        return None
    nearest = min(available, key=lambda cmc: (abs(cmc - mana_value), cmc))
    return corpus.subtypes_by_cmc[nearest]


def generate_type_line(
    corpus: Corpus, mana_value: int, rng: random.Random | None = None, mayhem: bool = False
) -> str:
    rng = rng or random
    pool = _subtype_pool(corpus, mana_value, mayhem=mayhem)
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
