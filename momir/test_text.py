"""Regression check for generate_rules_text: every generated line must be
built entirely from real sentence pieces -- either a whole real sentence, or
a real condition/cost half concatenated with a real effect half of the same
shape -- never anything else.

Run directly: python -m momir.test_text
"""
from __future__ import annotations

import random

from .corpus import Corpus
from .text import build_sentence_pools, generate_rules_text

CARD_NAME = "Test Creature"
MANA_VALUE = 3

TRIGGER_SENTENCES = [
    "Whenever this creature attacks, target creature gets +1/+1 until end of turn.",
    "Whenever ~ enters, you gain 2 life.",
]
ACTIVATED_SENTENCES = [
    "{1}, Sacrifice this creature: Draw a card.",
    "{T}: Add one mana of any color.",
]
STATIC_SENTENCES = [
    "~ can't be blocked except by creatures with flying.",
    "Other creatures you control get +1/+0.",
]


def _build_pool():
    corpus = Corpus(raw_cards=[])
    corpus.sentences_by_cmc[MANA_VALUE] = (
        [(s, 0, "trigger") for s in TRIGGER_SENTENCES]
        + [(s, 0, "activated") for s in ACTIVATED_SENTENCES]
        + [(s, 0, "static") for s in STATIC_SENTENCES]
    )
    return build_sentence_pools(corpus, range(MANA_VALUE, MANA_VALUE + 1))[MANA_VALUE]


def main() -> None:
    pool = _build_pool()

    assert not pool.heads.get(("static", 0)), "static sentences must never be split"
    assert not pool.tails.get(("static", 0)), "static sentences must never be split"

    whole = {s.replace("~", CARD_NAME) for s in TRIGGER_SENTENCES + ACTIVATED_SENTENCES + STATIC_SENTENCES}
    combos = {
        head.replace("~", CARD_NAME) + tail.replace("~", CARD_NAME)
        for shape in ("trigger", "activated")
        for head in pool.heads[(shape, 0)]
        for tail in pool.tails[(shape, 0)]
    }

    for seed in range(500):
        rng = random.Random(seed)
        lines = generate_rules_text(pool, CARD_NAME, rng=rng)
        assert len(lines) <= 2, f"got {len(lines)} lines, expected at most MAX_EXTRA_SENTENCES"
        for line in lines:
            assert line in whole or line in combos, f"line built from unknown pieces: {line!r}"

    empty_pool = build_sentence_pools(Corpus(raw_cards=[]), range(0, 1))[0]
    assert generate_rules_text(empty_pool, CARD_NAME, rng=random.Random(0)) == []

    print("ok")


if __name__ == "__main__":
    main()
