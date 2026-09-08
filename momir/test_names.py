"""Regression check for names.py: a legendary name's given-name half must
never leak a comma, and its epithet half (when present) must be built from
real epithet words, not the invented syllables the given-name chain uses.

Run directly: python -m momir.test_names
"""
from __future__ import annotations

import random

from .corpus import Corpus
from .names import build_name_chains, generate_name

CHARACTER_NAMES = [
    "Aang, Airbending Master",
    "Chandra, Fire Artisan",
    "Adeliz, the Cinder Wind",
    "Jace",
    "Gideon",
]
COMMON_NAMES = ["Grizzly Bears", "Goblin Piker", "Elite Vanguard", "Wind Drake"]
KNOWN_EPITHET_WORDS = {
    w for name in CHARACTER_NAMES if "," in name for w in name.partition(",")[2].strip().split()
}


def _build_corpus() -> Corpus:
    corpus = Corpus(raw_cards=[])
    corpus.character_names = list(CHARACTER_NAMES)
    corpus.common_names = list(COMMON_NAMES)
    return corpus


def main() -> None:
    corpus = _build_corpus()
    chains = build_name_chains(corpus)
    assert chains.epithet_chance == 3 / 5, chains.epithet_chance

    saw_epithet = saw_bare_character = False
    for seed in range(500):
        name = generate_name(chains, rng=random.Random(seed))
        assert name, "generate_name must never return empty"
        given, sep, epithet = name.partition(",")
        assert "," not in given, f"given-name half leaked a comma: {name!r}"
        if sep:
            epithet = epithet.strip()
            assert epithet, f"dangling comma with no epithet: {name!r}"
            for word in epithet.split():
                assert word in KNOWN_EPITHET_WORDS, f"epithet word not from real pool: {word!r} in {name!r}"
            saw_epithet = True
        elif name in ("Jace", "Gideon") or any(name.startswith(w) for w in ("Aang", "Chandra", "Adeliz")):
            saw_bare_character = True

    assert saw_epithet, "epithet path never exercised across 500 seeds"
    assert saw_bare_character, "bare given-name path never exercised across 500 seeds"

    empty_chains = build_name_chains(Corpus(raw_cards=[]))
    assert empty_chains.epithet_chance == 0.0
    assert generate_name(empty_chains, rng=random.Random(0)) == "Nameless Horror"

    print("ok")


if __name__ == "__main__":
    main()
