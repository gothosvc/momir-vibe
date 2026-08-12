"""
Minimal, dependency-free Markov chain implementations.

Two flavors are used elsewhere in the generator:

- ``CharMarkovChain``: character-level, trained on card names. This is what
  gives generated names their "sounds like Magic but isn't" quality (e.g.
  training on "Serra Angel" + "Shivan Dragon" can yield "Shivan Angel").

- ``WordMarkovChain``: word-level, trained on sentences pulled from real
  oracle text. Used to generate loose, flavorful extra rules text. Training
  sentences carry a "position" (their index within the source card's oracle
  text), and generation can request a position -- this is what keeps
  generated opening lines sounding like real openers instead of orphaned
  continuation clauses like "If a spell is countered this way, ..." that only
  make sense following the sentence that set them up.
"""
from __future__ import annotations

import random
from collections import Counter, defaultdict

START = "\0"
END = "\1"


class CharMarkovChain:
    """Character-level Markov chain over raw strings (e.g. card names)."""

    def __init__(self, order: int = 3) -> None:
        self.order = order
        self._model: dict[str, Counter] = defaultdict(Counter)

    def train(self, samples: list[str]) -> None:
        for sample in samples:
            if not sample:
                continue
            padded = (START * self.order) + sample + END
            for i in range(len(padded) - self.order):
                key = padded[i : i + self.order]
                nxt = padded[i + self.order]
                self._model[key][nxt] += 1

    def generate(self, max_len: int = 24, rng: random.Random | None = None) -> str:
        rng = rng or random
        key = START * self.order
        out: list[str] = []
        for _ in range(max_len):
            choices = self._model.get(key)
            if not choices:
                break
            nxt = rng.choices(list(choices.keys()), weights=list(choices.values()))[0]
            if nxt == END:
                break
            out.append(nxt)
            key = (key + nxt)[-self.order :]
        return "".join(out).strip()

    def generate_title(
        self, min_len: int = 4, max_len: int = 24, attempts: int = 25, rng: random.Random | None = None
    ) -> str:
        """Generate a name, retrying until it's a plausible length and doesn't
        trail off mid-phrase on a dangling stopword (e.g. "... of the")."""
        rng = rng or random
        best = ""
        for _ in range(attempts):
            candidate = self.generate(max_len=max_len, rng=rng)
            last_word = candidate.rsplit(" ", 1)[-1].lower() if candidate else ""
            if len(candidate) >= min_len and last_word not in _DANGLING_STOPWORDS:
                return candidate
            if len(candidate) > len(best):
                best = candidate
        return best or "Nameless Horror"


_DANGLING_STOPWORDS = {"of", "the", "and", "an", "a"}


class WordMarkovChain:
    """Word-level Markov chain over tokenized sentences (e.g. oracle text)."""

    def __init__(self, order: int = 2) -> None:
        self.order = order
        self._model: dict[tuple[str, ...], Counter] = defaultdict(Counter)
        # sentence position (0 = opener, 1 = second sentence, ...) -> start
        # keys seen at that position. Every trained sentence still feeds
        # self._model (mid-sentence transitions are valid regardless of where
        # the sentence sat in its source card), but only position-0 sentences
        # should ever be offered as a chain *opener*.
        self._starts_by_position: dict[int, list[tuple[str, ...]]] = defaultdict(list)

    def train(self, sentences: list[tuple[str, int]]) -> None:
        for sentence, position in sentences:
            words = sentence.split()
            if len(words) < self.order + 1:
                continue
            padded = [START] * self.order + words + [END]
            start_key = tuple(padded[: self.order])
            self._starts_by_position[position].append(start_key)
            for i in range(len(padded) - self.order):
                key = tuple(padded[i : i + self.order])
                nxt = padded[i + self.order]
                self._model[key][nxt] += 1

    def generate(
        self, max_words: int = 25, rng: random.Random | None = None, position: int = 0
    ) -> str:
        rng = rng or random
        # Fall back to the opener pool (always the best-populated) if this
        # position wasn't seen often enough at this mana value to have its
        # own starts -- e.g. a sparse mana value borrowing neighbor sentences.
        starts = self._starts_by_position.get(position) or self._starts_by_position.get(0)
        if not starts:
            return ""
        key = rng.choice(starts)
        out: list[str] = []
        for _ in range(max_words):
            choices = self._model.get(key)
            if not choices:
                break
            nxt = rng.choices(list(choices.keys()), weights=list(choices.values()))[0]
            if nxt == END:
                break
            if nxt != START:
                out.append(nxt)
            key = (key + (nxt,))[-self.order :]
        return " ".join(out).strip()
