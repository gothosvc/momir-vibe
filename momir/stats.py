"""Power/toughness generation, sampled from real creatures' stats at similar mana values."""
from __future__ import annotations

import random

from .corpus import Corpus, mana_value_weight, nearest_cmc

PTPool = tuple[list[tuple[float, float]], list[float] | None]


def _nearest_pt_pool(corpus: Corpus, mana_value: int, mayhem: bool = False, weighted: bool = True) -> PTPool:
    if mayhem:
        pool: list[tuple[float, float]] = []
        weights: list[float] | None = [] if weighted else None
        for cmc, cmc_pool in corpus.pt_by_cmc.items():
            pool.extend(cmc_pool)
            if weighted:
                weights.extend([mana_value_weight(cmc, mana_value)] * len(cmc_pool))
        return (pool, weights) if pool else ([(1.0, 1.0)], None)

    if corpus.pt_by_cmc.get(mana_value):
        return corpus.pt_by_cmc[mana_value], None

    nearest = nearest_cmc(corpus.pt_by_cmc, mana_value)
    return (corpus.pt_by_cmc[nearest], None) if nearest is not None else ([(1.0, 1.0)], None)


def generate_power_toughness(
    corpus: Corpus, mana_value: int, rng: random.Random | None = None, mayhem: bool = False, weighted: bool = True
) -> tuple[int, int]:
    rng = rng or random
    pool, weights = _nearest_pt_pool(corpus, mana_value, mayhem=mayhem, weighted=weighted)
    power, toughness = rng.choices(pool, weights=weights)[0]

    # Half-point stats (rare "*.5" oddities aside) round to nearest int so we
    # always hand back clean, playable numbers.
    return round(power), round(toughness)
