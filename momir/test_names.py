"""Regression check for names.py: a legendary name's given-name half must
never leak a comma or a bare-epithet lead-in word, and its epithet half
(when present) must be built from real epithet words of the matching
convention (comma vs. bare "the"/"of"), not the invented syllables the
given-name chain uses.

Run directly: python -m momir.test_names
"""
from __future__ import annotations

import random

from .corpus import Corpus
from .names import build_name_chains, generate_name

COMMA_NAMES = ["Aang, Airbending Master", "Chandra, Fire Artisan", "Adeliz, the Cinder Wind"]
BARE_EPITHET_NAMES = ["Abaddon the Despoiler", "Zur the Enchanter", "Araumi of the Dead Tide"]
BARE_CHARACTER_NAMES = ["Jace", "Gideon"]
CHARACTER_NAMES = COMMA_NAMES + BARE_EPITHET_NAMES + BARE_CHARACTER_NAMES
COMMON_NAMES = ["Grizzly Bears", "Goblin Piker", "Elite Vanguard", "Wind Drake"]
KNOWN_COMMA_EPITHET_WORDS = {w for name in COMMA_NAMES for w in name.partition(",")[2].strip().split()}
KNOWN_BARE_EPITHET_WORDS = {w for name in BARE_EPITHET_NAMES for w in name.split()[1:]}


def _build_corpus() -> Corpus:
    corpus = Corpus(raw_cards=[])
    corpus.character_names = list(CHARACTER_NAMES)
    corpus.common_names = list(COMMON_NAMES)
    return corpus


class _ScriptedRandom:
    """A random.Random stand-in whose random() replies come from a scripted
    queue, so a test can force generate_name down one specific branch
    deterministically -- everything else (rng.choice/choices, used for
    actual content) delegates to a real Random, so output is still built
    from real trained data, just along a chosen path."""

    def __init__(self, script: list[float], seed: int = 0) -> None:
        self._script = list(script)
        self._real = random.Random(seed)

    def random(self) -> float:
        return self._script.pop(0) if self._script else self._real.random()

    def __getattr__(self, attr):
        return getattr(self._real, attr)


def main() -> None:
    corpus = _build_corpus()
    chains = build_name_chains(corpus)
    assert chains.epithet_chance == 6 / 8, chains.epithet_chance
    assert chains.comma_epithet_share == 3 / 6, chains.comma_epithet_share
    assert 0 < chains.character_chance < 1 and 0 < chains.epithet_chance < 1, "fixture must exercise both branches"

    for seed in range(200):
        # Force: character path, has an epithet, comma convention.
        name = generate_name(chains, rng=_ScriptedRandom([0.0, 0.0, 0.0], seed=seed))
        given, sep, epithet = name.partition(",")
        assert sep, f"expected a comma epithet: {name!r}"
        assert "," not in given, f"given-name half leaked a comma: {name!r}"
        epithet = epithet.strip()
        assert epithet, f"dangling comma with no epithet: {name!r}"
        for word in epithet.split():
            assert word in KNOWN_COMMA_EPITHET_WORDS, f"epithet word not from real pool: {word!r} in {name!r}"

        # Force: character path, has an epithet, bare "the"/"of" convention.
        name = generate_name(chains, rng=_ScriptedRandom([0.0, 0.0, 1.0], seed=seed))
        assert "," not in name, f"bare-epithet name must have no comma: {name!r}"
        words = name.split()
        assert words[-1] != "the" and words[-1] != "of", f"epithet-only output, given name missing: {name!r}"
        lead = next((i for i, w in enumerate(words) if w.lower() in ("the", "of")), None)
        assert lead is not None, f"bare epithet must open with the/of: {name!r}"
        for word in words[lead + 1 :]:
            assert word in KNOWN_BARE_EPITHET_WORDS, f"bare epithet word not from real pool: {word!r} in {name!r}"

        # Force: character path, no epithet at all.
        name = generate_name(chains, rng=_ScriptedRandom([0.0, 1.0], seed=seed))
        assert "," not in name, f"bare given name must have no comma: {name!r}"

        # Force: common (non-legendary) name path.
        name = generate_name(chains, rng=_ScriptedRandom([1.0], seed=seed))
        assert name, "common-name path must never return empty"

    empty_chains = build_name_chains(Corpus(raw_cards=[]))
    assert empty_chains.epithet_chance == 0.0
    assert generate_name(empty_chains, rng=random.Random(0)) == "Nameless Horror"

    print("ok")


if __name__ == "__main__":
    main()
