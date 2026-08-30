"""Power/toughness generation, sampled from real creatures' stats at similar mana values."""
from __future__ import annotations

import random

from .corpus import Corpus, mana_value_weight

PTPool = tuple[list[tuple[float, float]], list[float] | None]


def _nearest_pt_pool(corpus: Corpus, mana_value: int, mayhem: bool = False) -> PTPool:
    if mayhem:
        pool: list[tuple[float, float]] = []
        weights: list[float] = []
        for cmc, cmc_pool in corpus.pt_by_cmc.items():
            weight = mana_value_weight(cmc, mana_value)
            pool.extend(cmc_pool)
            weights.extend([weight] * len(cmc_pool))
        return (pool, weights) if pool else ([(1.0, 1.0)], None)

    if corpus.pt_by_cmc.get(mana_value):
        return corpus.pt_by_cmc[mana_value], None

    available = [cmc for cmc, pool in corpus.pt_by_cmc.items() if pool]
    if not available:
        return [(1.0, 1.0)], None

    nearest = min(available, key=lambda cmc: (abs(cmc - mana_value), cmc))
    return corpus.pt_by_cmc[nearest], None


def generate_power_toughness(
    corpus: Corpus, mana_value: int, rng: random.Random | None = None, mayhem: bool = False
) -> tuple[int, int]:
    rng = rng or random
    pool, weights = _nearest_pt_pool(corpus, mana_value, mayhem=mayhem)
    power, toughness = rng.choices(pool, weights=weights)[0]

    # Half-point stats (rare "*.5" oddities aside) round to nearest int so we
    # always hand back clean, playable numbers.
    return round(power), round(toughness)
