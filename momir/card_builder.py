"""
The generator itself: wires the corpus + all the sub-generators together into
a single object that produces complete, playable-looking creature cards.

Training the Markov chains happens once, at construction time -- generating
an individual card is then cheap and can happen on every API request.
"""
from __future__ import annotations

import random

from . import colors, stats, text, types
from .corpus import Corpus, get_corpus
from .markov import CharMarkovChain, WordMarkovChain
from .models import Card
from .names import build_name_chain

RARITY_WEIGHTS = {"common": 40, "uncommon": 30, "rare": 20, "mythic": 10}
MIN_MANA_VALUE = 0
MAX_MANA_VALUE = 16


class CardGenerator:
    def __init__(self, corpus: Corpus | None = None) -> None:
        self.corpus = corpus or get_corpus()
        self.name_chain: CharMarkovChain = build_name_chain(self.corpus)
        # One text chain per mana value, so a 1-drop's generated text is only
        # ever trained on what real 1-drops say -- see momir/text.py.
        self.text_chains: dict[int, WordMarkovChain] = text.build_text_chains(
            self.corpus, range(MIN_MANA_VALUE, MAX_MANA_VALUE + 1)
        )
        self._next_collector_number = 1

    def _rarity(self, rng: random.Random) -> str:
        pool = list(RARITY_WEIGHTS.keys())
        weights = list(RARITY_WEIGHTS.values())
        return rng.choices(pool, weights=weights)[0]

    def _collector_number(self) -> str:
        number = self._next_collector_number
        self._next_collector_number += 1
        return str(number)

    def generate(self, mana_value: int, rng: random.Random | None = None) -> Card:
        if not (MIN_MANA_VALUE <= mana_value <= MAX_MANA_VALUE):
            raise ValueError(
                f"mana_value must be between {MIN_MANA_VALUE} and {MAX_MANA_VALUE}, got {mana_value}"
            )

        rng = rng or random

        name = self.name_chain.generate_title(rng=rng)
        mana_cost = colors.synthesize_mana_cost(self.corpus, mana_value, rng=rng)
        symbols = colors.parse_symbols(mana_cost)
        card_colors = colors.colors_in_symbols(symbols)

        type_line = types.generate_type_line(self.corpus, mana_value, rng=rng)
        power, toughness = stats.generate_power_toughness(self.corpus, mana_value, rng=rng)
        keywords = text.generate_keywords(self.corpus, mana_value, rng=rng)
        rules_text = text.generate_rules_text(self.text_chains[mana_value], name, rng=rng)

        return Card(
            name=name,
            mana_cost=mana_cost,
            mana_value=mana_value,
            colors=card_colors,
            color_identity=card_colors,
            type_line=type_line,
            power=power,
            toughness=toughness,
            keywords=keywords,
            rules_text=rules_text,
            rarity=self._rarity(rng),
            collector_number=self._collector_number(),
        )
