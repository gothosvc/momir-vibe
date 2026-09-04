"""
The generator itself: wires the corpus + all the sub-generators together into
a single object that produces complete, playable-looking creature cards.

Training the name Markov chains and building the sentence pools happens
once, at construction time -- generating an individual card is then cheap
and can happen on every API request.
"""
from __future__ import annotations

import functools
import random
from typing import Literal

from . import art, colors, stats, text, types
from .codec import encode_card
from .corpus import Corpus, get_corpus
from .models import Card
from .names import NameChains, build_name_chains, generate_name

RARITY_WEIGHTS = {"common": 40, "uncommon": 30, "rare": 20, "mythic": 10}
MIN_MANA_VALUE = 0
MAX_MANA_VALUE = 16

Mayhem = Literal["off", "text", "full", "unhinged"]


class CardGenerator:
    def __init__(self, corpus: Corpus | None = None) -> None:
        self.corpus = corpus or get_corpus()
        self.name_chains: NameChains = build_name_chains(self.corpus)
        # Detection vocab for the keyword-name/creature-subtype reroll pass
        # -- global (doesn't vary by mana value), built once and fed into
        # every sentence pool below so each pool's keyword_refs/subtype_refs
        # only include names actually seen in a safe context. See
        # momir/text.py.
        self.reroll_vocab: text.RerollVocab = text.build_reroll_vocab(self.corpus)
        # One sentence pool per mana value, so a 1-drop's generated text is
        # only ever sampled from what real 1-drops say -- see momir/text.py.
        self.sentence_pools: dict[int, text.SentencePool] = text.build_sentence_pools(
            self.corpus, range(MIN_MANA_VALUE, MAX_MANA_VALUE + 1), self.reroll_vocab
        )
        # mayhem=text/full pools sentences from every mana value, so it
        # doesn't vary by mana value like sentence_pools above -- one pool
        # covers every request.
        self.mayhem_sentence_pool: text.SentencePool = text.build_mayhem_sentence_pool(
            self.corpus, self.reroll_vocab
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

    def generate(self, mana_value: int, rng: random.Random | None = None, mayhem: Mayhem = "off") -> Card:
        if not (MIN_MANA_VALUE <= mana_value <= MAX_MANA_VALUE):
            raise ValueError(
                f"mana_value must be between {MIN_MANA_VALUE} and {MAX_MANA_VALUE}, got {mana_value}"
            )

        rng = rng or random
        full_mayhem = mayhem in ("full", "unhinged")
        text_mayhem = mayhem in ("text", "full", "unhinged")
        force_text = mayhem == "unhinged"

        name = generate_name(self.name_chains, rng=rng)
        mana_cost = colors.synthesize_mana_cost(self.corpus, mana_value, rng=rng, mayhem=full_mayhem)
        symbols = colors.parse_symbols(mana_cost)
        card_colors = colors.colors_in_symbols(symbols)

        type_line = types.generate_type_line(self.corpus, mana_value, rng=rng, mayhem=full_mayhem)
        power, toughness = stats.generate_power_toughness(self.corpus, mana_value, rng=rng, mayhem=full_mayhem)
        keywords = text.generate_keywords(self.corpus, mana_value, name, rng=rng, mayhem=text_mayhem)
        pool = self.mayhem_sentence_pool if text_mayhem else self.sentence_pools[mana_value]
        rules_text = text.generate_rules_text(pool, name, rng=rng, vocab=self.reroll_vocab, force=force_text)

        # A real creature's art, matched by color identity -- unrelated to
        # this card's name/text, just a thematically plausible picture. None
        # if the corpus has no art data (see momir/art.py), in which case
        # the artist stays the joke placeholder rather than crediting no one.
        selected_art = art.select_art(self.corpus, card_colors, rng=rng)
        art_url, artist = selected_art if selected_art else (None, "Markov Chain Studios")

        card = Card(
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
            artist=artist,
            art_url=art_url,
        )
        # Attach a share code so every generated card is reconstructible from
        # this response alone -- see momir/codec.py.
        card.share_code = encode_card(card)
        return card


@functools.lru_cache(maxsize=None)
def get_generator(legal_in: str | None = None) -> CardGenerator:
    """One process-wide singleton per `legal_in` value (see corpus.py's
    SUPPORTED_FORMATS) -- building a CardGenerator means training the name
    Markov chains and building every sentence pool, which is the expensive
    part, so each distinct format is only ever built once per server run
    rather than per request."""
    return CardGenerator(get_corpus(legal_in=legal_in))
