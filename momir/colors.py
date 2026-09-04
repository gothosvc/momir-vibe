"""
Mana cost synthesis.

Rather than inventing color-pip distributions from scratch, we lean on the
real data: for a requested mana value we sample an *actual* mana cost that a
real creature card used at that mana value (e.g. asking for mv=3 might hand
back the pip pattern from "{1}{G}{G}"). This keeps generated costs looking
exactly like something Wizards would print, while the rest of the card
(name, type, text, stats) is generated fresh.

If no real creature exists at the exact requested mana value (uncommon, but
possible at the high end), we fall back to adapting the pip pattern from the
nearest mana value we do have data for.
"""
from __future__ import annotations

import random
import re

from .corpus import Corpus, mana_value_weight

_SYMBOL_RE = re.compile(r"\{([^}]+)\}")
_COLOR_ORDER = "WUBRG"


def parse_symbols(cost: str) -> list[str]:
    return _SYMBOL_RE.findall(cost)


def build_cost_string(symbols: list[str]) -> str:
    return "".join(f"{{{s}}}" for s in symbols)


def _is_generic(symbol: str) -> bool:
    return symbol.isdigit()


def colors_in_symbols(symbols: list[str]) -> list[str]:
    found = set()
    for symbol in symbols:
        for letter in symbol.upper():
            if letter in _COLOR_ORDER:
                found.add(letter)
    return [c for c in _COLOR_ORDER if c in found]


def _nearest_available_cmc(corpus: Corpus, mana_value: int) -> int | None:
    available = corpus.available_cmcs
    if not available:
        return None
    return min(available, key=lambda cmc: (abs(cmc - mana_value), cmc))


def _adapt_to_mana_value(template: str, mana_value: int) -> str:
    """Rebuild a real cost's colored-pip pattern so its total mana value
    matches mana_value -- for borrowing a template from a different cmc,
    whether because the exact one was sparse or, under mayhem, because the
    template was drawn from an unrelated cmc on purpose."""
    symbols = [s for s in parse_symbols(template) if not _is_generic(s)]

    # Each non-generic symbol (colored pip, hybrid, Phyrexian, ...)
    # contributes 1 to mana value in the overwhelming common case.
    while symbols and len(symbols) > mana_value:
        symbols.pop()

    remaining = mana_value - len(symbols)
    out = ([str(remaining)] if remaining > 0 else []) + symbols
    return build_cost_string(out) or f"{{{mana_value}}}"


def synthesize_mana_cost(
    corpus: Corpus, mana_value: int, rng: random.Random | None = None, mayhem: bool = False, weighted: bool = True
) -> str:
    """`weighted` only matters under mayhem: True (the default) favors cmcs
    near mana_value (see mana_value_weight), same as every other mayhem
    pool; False draws the color-pip template from every cmc bucket with
    equal odds, for a mode that wants the pip pattern itself unmoored from
    mana_value too, not just "nearby-favored" -- see momir/card_builder.py's
    'unhinged' mayhem level."""
    rng = rng or random

    if mana_value <= 0:
        return "{0}"

    if mayhem:
        items: list[str] = []
        weights: list[float] | None = [] if weighted else None
        for cmc, pool in corpus.mana_costs_by_cmc.items():
            items.extend(pool)
            if weighted:
                weights.extend([mana_value_weight(cmc, mana_value)] * len(pool))
        if not items:
            return build_cost_string([str(mana_value)])
        return _adapt_to_mana_value(rng.choices(items, weights=weights)[0], mana_value)

    exact_matches = corpus.mana_costs_by_cmc.get(mana_value)
    if exact_matches:
        return rng.choice(exact_matches)

    # Fall back: borrow the colored-pip pattern from the nearest mana value
    # we have real data for, then rebuild the generic portion so the total
    # mana value matches what was requested.
    nearest = _nearest_available_cmc(corpus, mana_value)
    if nearest is None:
        return build_cost_string([str(mana_value)])

    return _adapt_to_mana_value(rng.choice(corpus.mana_costs_by_cmc[nearest]), mana_value)
