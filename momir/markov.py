"""
Minimal, dependency-free Markov chain implementations.

Two flavors are used elsewhere in the generator:

- ``CharMarkovChain``: character-level, trained on card names. This is what
  gives generated names their "sounds like Magic but isn't" quality (e.g.
  training on "Serra Angel" + "Shivan Dragon" can yield "Shivan Angel").

- ``WordMarkovChain``: word-level, trained on sentences pulled from real
  oracle text. Used to generate loose, flavorful extra rules text. Training
  sentences carry a "position" (their index within the source card's oracle
  text) and a "shape" (its construct type -- trigger/activated/static; see
  momir/corpus.py's _sentence_shape). Generation can request either:

  - Position keeps generated opening lines sounding like real openers,
    instead of orphaned continuation clauses like "If a spell is countered
    this way, ..." that only make sense following the sentence that set
    them up.
  - Shape stops a generated sentence from wandering mid-generation from
    e.g. a triggered ability into an unrelated activated ability's "cost:"
    clause. Transitions are modeled separately per shape, so a chain that
    starts as one construct only ever continues as that construct.
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
    """Word-level Markov chain over tokenized sentences (e.g. oracle text).

    Every sentence is tagged with a "shape" at training time (callers that
    don't care about shape-splitting -- e.g. name generation -- can just tag
    everything with the same constant). Transitions are modeled *separately
    per shape*, not just started separately, so mid-generation the chain can
    never wander from one shape into another -- that cross-shape wandering is
    what produces nonsense like a triggered ability's text fusing into an
    unrelated activated ability's "cost:" clause.
    """

    def __init__(self, order: int = 2) -> None:
        self.order = order
        # Transitions are modeled separately per (shape, position), not just
        # per shape: sharing transitions across positions within a shape was
        # a bug (see train()'s comment) that let a continuation clause like
        # "When you do, ..." get drawn as a first line, because after a
        # legitimate position-0 opening word ("When"), the *next* word was
        # still being pulled from a table pooling every position's "When
        # ___" continuations together.
        self._models: dict[tuple[str, int], dict[tuple[str, ...], Counter]] = defaultdict(
            lambda: defaultdict(Counter)
        )
        # (shape, position) -> real first words seen opening a sentence
        # there (one entry per occurrence, so rng.choice naturally weights
        # by real frequency). position 0 = opener, 1 = second sentence, etc.
        self._starts: dict[tuple[str, int], list[str]] = defaultdict(list)
        # shape -> number of sentences trained, used to weight shape choice
        # at generation time by how common that construct actually is.
        self._shape_counts: Counter = Counter()

    def train(self, sentences: list[tuple[str, int, str]]) -> None:
        for sentence, position, shape in sentences:
            words = sentence.split()
            if len(words) < self.order + 1:
                continue
            padded = [START] * self.order + words + [END]
            self._starts[(shape, position)].append(words[0])
            self._shape_counts[shape] += 1
            # Scoped by (shape, position), not just shape -- see __init__.
            # Sharing this table across positions would let e.g. a real
            # position-0 opener's second word be drawn from what actually
            # followed that same word on a position-2 continuation clause
            # elsewhere, defeating the point of tracking position at all.
            model = self._models[(shape, position)]
            for i in range(len(padded) - self.order):
                key = tuple(padded[i : i + self.order])
                nxt = padded[i + self.order]
                model[key][nxt] += 1

    def choose_shape(self, rng: random.Random | None = None) -> str | None:
        """Pick a shape weighted by how often it was actually trained on.
        None means nothing was ever trained. Exposed so callers can generate
        multiple lines from the *same* shape (see momir/text.py) rather than
        e.g. an activated-ability line followed by an unrelated static one."""
        if not self._shape_counts:
            return None
        rng = rng or random
        shapes = list(self._shape_counts.keys())
        weights = [self._shape_counts[s] for s in shapes]
        return rng.choices(shapes, weights=weights)[0]

    def generate(
        self,
        max_words: int = 25,
        rng: random.Random | None = None,
        position: int = 0,
        shape: str | None = None,
    ) -> str:
        rng = rng or random
        shape = shape if shape is not None else self.choose_shape(rng)
        if shape is None:
            return ""
        # Fall back to the opener pool (always the best-populated) if this
        # position wasn't seen often enough at this mana value to have its
        # own starts -- e.g. a sparse mana value borrowing neighbor sentences.
        # The model lookup below follows the same fallback (not the
        # originally requested position) so a word's continuation always
        # comes from the same position bucket its first word did.
        first_words = self._starts.get((shape, position))
        if not first_words:
            first_words = self._starts.get((shape, 0))
            position = 0
        if not first_words:
            return ""
        model = self._models[(shape, position)]
        first_word = rng.choice(first_words)
        out: list[str] = [first_word]
        # The key a normal walk would be in right after emitting the real
        # first word: order-1 START placeholders followed by that word.
        key = (START,) * (self.order - 1) + (first_word,)
        for _ in range(max_words - 1):
            choices = model.get(key)
            if not choices:
                break
            nxt = rng.choices(list(choices.keys()), weights=list(choices.values()))[0]
            if nxt == END:
                break
            if nxt != START:
                out.append(nxt)
            key = (key + (nxt,))[-self.order :]
        return " ".join(out).strip()
