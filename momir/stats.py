"""Power/toughness generation, sampled from real creatures' stats at similar mana values."""
from __future__ import annotations

import itertools
import random

from .corpus import Corpus


def _nearest_pt_pool(corpus: Corpus, mana_value: int, mayhem: bool = False) -> list[tuple[float, float]]:
    if mayhem:
        pool = list(itertools.chain.from_iterable(corpus.pt_by_cmc.values()))
        return pool or [(1.0, 1.0)]

    if corpus.pt_by_cmc.get(mana_value):
        return corpus.pt_by_cmc[mana_value]

    available = [cmc for cmc, pool in corpus.pt_by_cmc.items() if pool]
    if not available:
        return [(1.0, 1.0)]

    nearest = min(available, key=lambda cmc: (abs(cmc - mana_value), cmc))
    return corpus.pt_by_cmc[nearest]


def generate_power_toughness(
    corpus: Corpus, mana_value: int, rng: random.Random | None = None, mayhem: bool = False
) -> tuple[int, int]:
    rng = rng or random
    pool = _nearest_pt_pool(corpus, mana_value, mayhem=mayhem)
    power, toughness = rng.choice(pool)

    # Half-point stats (rare "*.5" oddities aside) round to nearest int so we
    # always hand back clean, playable numbers.
    return round(power), round(toughness)
