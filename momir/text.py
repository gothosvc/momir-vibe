"""
Rules text generation: a blend of two techniques.

- Keyword abilities (Flying, Trample, Deathtouch, ...) are sampled from the
  real frequency distribution of keywords seen on creatures at a similar
  mana value, so mechanically the card is meaningful and "on curve". Ones
  that print with a value ("Ward {2}", "Cycling {1}{U}") get a real observed
  value attached rather than showing up bare -- see corpus.py's
  keyword_values_by_cmc.
- Extra flavor rules text is sampled from real oracle sentences (whole, or
  recombined at a real grammatical seam -- see generate_rules_text), giving
  loose, evocative (but not mechanically binding) rules text. Momir-style
  play is honor-system anyway -- players read the card and interpret it,
  same as a home-brew card.
"""
from __future__ import annotations

import itertools
import random
from collections import Counter, defaultdict
from dataclasses import dataclass

from .corpus import Corpus, mana_value_weight

KEYWORD_COUNT_WEIGHTS = [0, 0, 1, 1, 1, 2]  # skewed toward 0-1 keywords, occasionally more
# ~97% of real creatures print some oracle text; 0.55 was making generated
# cards noticeably blanker than that. Raised to sit closer to reality while
# still leaving room for the occasional vanilla creature.
EXTRA_TEXT_CHANCE = 0.85
MAX_EXTRA_SENTENCES = 2

# Chance a trigger/activated line is assembled from a condition/cost half of
# one real sentence plus an effect half of a different same-shape sentence,
# rather than one real sentence verbatim. Only ever splits at the shape's own
# grammatical delimiter (see _split_sentence) so recombination can't produce
# the mid-sentence splices word-Markov generation used to.
RECOMBINE_CHANCE = 0.5

# Minimum sentence pool a mana value's generated text wants before we trust
# it to not read "samey". Sparse mana values (very low or very high mv have
# few real creatures) borrow sentences from progressively wider neighboring
# mana values -- never from the corpus at large -- to stay in the right
# power-level neighborhood while still having enough to pick from. A small
# pool means the same handful of (shape, position) sentences keep coming back
# verbatim across repeated generations at that mana value; a bigger pool
# means more distinct sentences per bucket, and more head/tail combinations
# for the recombination path (see RECOMBINE_CHANCE) to draw from.
MIN_TRAINING_SENTENCES = 1000
MAX_BORROW_RADIUS = 32


def _keyword_pool(corpus: Corpus, mana_value: int, mayhem: bool) -> tuple[Counter, dict[str, list[str]]]:
    """The (keyword name -> count, keyword name -> observed values) pair to
    draw from -- under mayhem, pooled across every mana value but weighted
    by distance from mana_value (see corpus.py's mana_value_weight), else
    the requested one if it has any, else the nearest one that does."""
    if mayhem:
        names: Counter = Counter()
        for cmc, counter in corpus.keywords_by_cmc.items():
            weight = mana_value_weight(cmc, mana_value)
            for name, count in counter.items():
                names[name] += count * weight
        values: dict[str, list[str]] = {}
        for per_cmc in corpus.keyword_values_by_cmc.values():
            for name, vals in per_cmc.items():
                values.setdefault(name, []).extend(vals)
        return names, values

    cmc = mana_value if corpus.keywords_by_cmc.get(mana_value) else None
    if cmc is None:
        available = [c for c, counter in corpus.keywords_by_cmc.items() if counter]
        if not available:
            return Counter(), {}
        cmc = min(available, key=lambda c: (abs(c - mana_value), c))
    return corpus.keywords_by_cmc[cmc], corpus.keyword_values_by_cmc.get(cmc, {})


def _keyword_text(values_pool: dict[str, list[str]], name: str, card_name: str, rng: random.Random) -> str:
    """The full printed keyword line, e.g. "Ward" -> "Ward {2}" -- sampled
    from real observed values so a keyword that always prints with a
    cost/parameter never shows up bare. Values carry their own leading
    whitespace (or lack of it) verbatim from the source card -- see
    corpus.py's _keyword_occurrence -- so a plain concatenation reproduces
    the real spacing either way. "~" substitution mirrors generate_rules_text."""
    values = values_pool.get(name)
    if not values:
        return name
    value = rng.choice(values)
    text = f"{name}{value}" if value else name
    return text.replace("~", card_name)


def generate_keywords(
    corpus: Corpus, mana_value: int, card_name: str, rng: random.Random | None = None, mayhem: bool = False
) -> list[str]:
    rng = rng or random
    pool, values_pool = _keyword_pool(corpus, mana_value, mayhem)
    if not pool:
        return []

    count = rng.choice(KEYWORD_COUNT_WEIGHTS)
    if count == 0:
        return []

    names = list(pool.keys())
    weights = list(pool.values())

    chosen: list[str] = []
    for _ in range(min(count, len(names))):
        pick = rng.choices(names, weights=weights)[0]
        if pick not in chosen:
            chosen.append(pick)
    return [_keyword_text(values_pool, name, card_name, rng) for name in chosen]


def _sentences_for_mana_value(corpus: Corpus, mana_value: int) -> list[tuple[str, int, str]]:
    """Sentences from creatures at this exact mana value, widened to
    progressively further neighbors only if there isn't enough to pick from."""
    collected = list(corpus.sentences_by_cmc.get(mana_value, []))

    radius = 1
    while len(collected) < MIN_TRAINING_SENTENCES and radius <= MAX_BORROW_RADIUS:
        for neighbor in (mana_value - radius, mana_value + radius):
            collected.extend(corpus.sentences_by_cmc.get(neighbor, []))
        radius += 1

    return collected


def _split_sentence(sentence: str, shape: str) -> tuple[str, str] | None:
    """Split a sentence into (head, tail) at its shape's own grammatical
    seam -- the comma separating a trigger's condition from its effect
    ("Whenever X, Y."), or the colon separating an activated ability's cost
    from its effect ("Cost: Y."). None if the shape has no such seam
    (static), or the expected delimiter isn't present. `head` keeps the
    delimiter and trailing space, so `head + tail` reproduces valid spacing
    -- see generate_rules_text's recombination path."""
    delimiter = {"trigger": ",", "activated": ":"}.get(shape)
    if delimiter is None:
        return None
    head, sep, tail = sentence.partition(delimiter)
    if not sep:
        return None
    return head + sep + " ", tail.strip()


def _bucket_sentences(
    sentences: list[tuple[str, int, str]],
) -> tuple[Counter, dict[tuple[str, int], list[str]], dict[tuple[str, int], list[str]], dict[tuple[str, int], list[str]]]:
    """Group real sentences by (shape, position) -- both whole (for
    verbatim sampling) and split into head/tail (for the seam-recombination
    path, where a split exists) -- plus a shape -> count tally for weighting
    which shape a generated block uses."""
    shape_counts: Counter = Counter()
    whole: dict[tuple[str, int], list[str]] = defaultdict(list)
    heads: dict[tuple[str, int], list[str]] = defaultdict(list)
    tails: dict[tuple[str, int], list[str]] = defaultdict(list)
    for sentence, position, shape in sentences:
        shape_counts[shape] += 1
        whole[(shape, position)].append(sentence)
        split = _split_sentence(sentence, shape)
        if split:
            head, tail = split
            heads[(shape, position)].append(head)
            tails[(shape, position)].append(tail)
    return shape_counts, whole, heads, tails


@dataclass
class SentencePool:
    """Real sentences bucketed and ready to sample from -- see
    _bucket_sentences. Replaces a trained Markov chain: generation is
    picking real pieces from here rather than walking a model."""

    shape_counts: Counter
    sentences: dict[tuple[str, int], list[str]]
    heads: dict[tuple[str, int], list[str]]
    tails: dict[tuple[str, int], list[str]]


def build_sentence_pools(corpus: Corpus, mana_values: range) -> dict[int, SentencePool]:
    """One sentence pool per mana value, each drawn only from sentences of
    creatures at (or, if sparse, near) that mana value."""
    return {
        mana_value: SentencePool(*_bucket_sentences(_sentences_for_mana_value(corpus, mana_value)))
        for mana_value in mana_values
    }


def build_mayhem_sentence_pool(corpus: Corpus) -> SentencePool:
    """A single pool drawn from sentences at every mana value, for
    mayhem=text/full -- unlike build_sentence_pools this doesn't vary by
    mana value, so it's built once rather than per mana value."""
    all_sentences = list(itertools.chain.from_iterable(corpus.sentences_by_cmc.values()))
    return SentencePool(*_bucket_sentences(all_sentences))


def _pick(bucket: dict[tuple[str, int], list[str]], shape: str, position: int) -> list[str]:
    """The (shape, position) bucket, falling back to (shape, 0) -- the
    best-populated position -- if this position wasn't seen often enough at
    this mana value to have its own entries (e.g. a sparse mana value
    borrowing neighbor sentences)."""
    return bucket.get((shape, position)) or bucket.get((shape, 0)) or []


def generate_rules_text(pool: SentencePool, card_name: str, rng: random.Random | None = None) -> list[str]:
    rng = rng or random
    if rng.random() >= EXTRA_TEXT_CHANCE or not pool.shape_counts:
        return []

    # Pick one shape (trigger / activated / static -- see corpus.py's
    # _sentence_shape) for the whole block, so a second line reads as more
    # of the same construct rather than an unrelated one.
    shapes = list(pool.shape_counts.keys())
    weights = list(pool.shape_counts.values())
    shape = rng.choices(shapes, weights=weights)[0]

    lines: list[str] = []
    for position in range(rng.randint(1, MAX_EXTRA_SENTENCES)):
        # position 0 draws from real opening sentences, position 1+ from real
        # follow-up sentences -- so a second line reads like a natural
        # continuation clause instead of an unrelated second opener.
        heads = _pick(pool.heads, shape, position)
        tails = _pick(pool.tails, shape, position)
        if heads and tails and rng.random() < RECOMBINE_CHANCE:
            sentence = rng.choice(heads) + rng.choice(tails)
        else:
            sentences = _pick(pool.sentences, shape, position)
            if not sentences:
                continue
            sentence = rng.choice(sentences)
        lines.append(sentence.replace("~", card_name))
    return lines
