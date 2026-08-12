"""Power/toughness generation, sampled from real creatures' stats at similar mana values."""
from __future__ import annotations

import random

from .corpus import Corpus


def _nearest_pt_pool(corpus: Corpus, mana_value: int) -> list[tuple[float, float]]:
    if corpus.pt_by_cmc.get(mana_value):
        return corpus.pt_by_cmc[mana_value]

    available = [cmc for cmc, pool in corpus.pt_by_cmc.items() if pool]
    if not available:
        return [(1.0, 1.0)]

    nearest = min(available, key=lambda cmc: (abs(cmc - mana_value), cmc))
    return corpus.pt_by_cmc[nearest]


def generate_power_toughness(
    corpus: Corpus, mana_value: int, rng: random.Random | None = None
) -> tuple[int, int]:
    rng = rng or random
    pool = _nearest_pt_pool(corpus, mana_value)
    power, toughness = rng.choice(pool)

    # Half-point stats (rare "*.5" oddities aside) round to nearest int so we
    # always hand back clean, playable numbers.
    return round(power), round(toughness)
