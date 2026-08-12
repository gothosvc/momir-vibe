"""
Rules text generation: a blend of two techniques.

- Keyword abilities (Flying, Trample, Deathtouch, ...) are sampled from the
  real frequency distribution of keywords seen on creatures at a similar
  mana value, so mechanically the card is meaningful and "on curve". Ones
  that print with a value ("Ward {2}", "Cycling {1}{U}") get a real observed
  value attached rather than showing up bare -- see corpus.py's
  keyword_values_by_cmc.
- Extra flavor rules text is generated with a word-level Markov chain
  trained on real oracle text, giving loose, evocative (but not mechanically
  binding) rules text. Momir-style play is honor-system anyway -- players
  read the card and interpret it, same as a home-brew card.
"""
from __future__ import annotations

import random

from .corpus import Corpus
from .markov import WordMarkovChain

KEYWORD_COUNT_WEIGHTS = [0, 0, 1, 1, 1, 2]  # skewed toward 0-1 keywords, occasionally more
EXTRA_TEXT_CHANCE = 0.55
MAX_EXTRA_SENTENCES = 2

# Word-chain order for rules text. Higher than the default (2) deliberately:
# order 2 was splicing sentences at nearly any shared 2-word junction, which
# (even with per-shape modeling -- see corpus.py's _sentence_shape) could
# still fuse two same-shape clauses into nonsense like a double "cost:
# effect: effect". Order 3 requires a longer real match before it'll
# diverge, trading a bit of novelty for a lot fewer broken splices.
TEXT_MARKOV_ORDER = 3

# Retries for a single generated line before giving up on it, discarding
# generations that slipped past shape-bucketing with an obvious tell (see
# _is_clean_sentence) rather than ever showing one.
SENTENCE_ATTEMPTS = 8

# Minimum sentence pool a mana value's text chain wants before we trust it to
# generate coherent output. Sparse mana values (very low or very high mv have
# few real creatures) borrow sentences from progressively wider neighboring
# mana values -- never from the corpus at large -- to stay in the right
# power-level neighborhood while still having enough to train on.
MIN_TRAINING_SENTENCES = 60
MAX_BORROW_RADIUS = 32


def _keyword_cmc(corpus: Corpus, mana_value: int) -> int | None:
    """The mana value to actually pull keyword data (names and values) from
    -- the requested one if it has any, else the nearest one that does."""
    if corpus.keywords_by_cmc.get(mana_value):
        return mana_value
    available = [cmc for cmc, counter in corpus.keywords_by_cmc.items() if counter]
    if not available:
        return None
    return min(available, key=lambda cmc: (abs(cmc - mana_value), cmc))


def _keyword_text(corpus: Corpus, cmc: int, name: str, card_name: str, rng: random.Random) -> str:
    """The full printed keyword line, e.g. "Ward" -> "Ward {2}" -- sampled
    from real observed values at this mana value so a keyword that always
    prints with a cost/parameter never shows up bare. Values carry their own
    leading whitespace (or lack of it) verbatim from the source card -- see
    corpus.py's _keyword_occurrence -- so a plain concatenation reproduces
    the real spacing either way. "~" substitution mirrors generate_rules_text."""
    values = corpus.keyword_values_by_cmc.get(cmc, {}).get(name)
    if not values:
        return name
    value = rng.choice(values)
    text = f"{name}{value}" if value else name
    return text.replace("~", card_name)


def generate_keywords(
    corpus: Corpus, mana_value: int, card_name: str, rng: random.Random | None = None
) -> list[str]:
    rng = rng or random
    cmc = _keyword_cmc(corpus, mana_value)
    if cmc is None:
        return []

    count = rng.choice(KEYWORD_COUNT_WEIGHTS)
    if count == 0:
        return []

    pool = corpus.keywords_by_cmc[cmc]
    names = list(pool.keys())
    weights = list(pool.values())

    chosen: list[str] = []
    for _ in range(min(count, len(names))):
        pick = rng.choices(names, weights=weights)[0]
        if pick not in chosen:
            chosen.append(pick)
    return [_keyword_text(corpus, cmc, name, card_name, rng) for name in chosen]


def _sentences_for_mana_value(corpus: Corpus, mana_value: int) -> list[tuple[str, int, str]]:
    """Sentences from creatures at this exact mana value, widened to
    progressively further neighbors only if there isn't enough to train on."""
    collected = list(corpus.sentences_by_cmc.get(mana_value, []))

    radius = 1
    while len(collected) < MIN_TRAINING_SENTENCES and radius <= MAX_BORROW_RADIUS:
        for neighbor in (mana_value - radius, mana_value + radius):
            collected.extend(corpus.sentences_by_cmc.get(neighbor, []))
        radius += 1

    return collected


def build_text_chains(corpus: Corpus, mana_values: range) -> dict[int, WordMarkovChain]:
    """One word-Markov chain per mana value, each trained only on sentences
    from creatures at (or, if sparse, near) that mana value."""
    chains: dict[int, WordMarkovChain] = {}
    for mana_value in mana_values:
        chain = WordMarkovChain(order=TEXT_MARKOV_ORDER)
        chain.train(_sentences_for_mana_value(corpus, mana_value))
        chains[mana_value] = chain
    return chains


def _is_clean_sentence(sentence: str) -> bool:
    """Backstop against generations that slipped past corpus filtering with
    an obvious tell -- most commonly two same-shape activated-ability
    clauses fusing into a double "cost: effect: effect"."""
    if sentence.count(":") >= 2:
        return False
    if "•" in sentence or "|" in sentence:
        return False
    return True


def generate_rules_text(
    chain: WordMarkovChain, card_name: str, rng: random.Random | None = None
) -> list[str]:
    rng = rng or random
    if rng.random() >= EXTRA_TEXT_CHANCE:
        return []

    # Pick one shape (trigger / activated / static -- see corpus.py's
    # _sentence_shape) for the whole block, so a second line reads as more
    # of the same construct rather than an unrelated one.
    shape = chain.choose_shape(rng)

    lines: list[str] = []
    for position in range(rng.randint(1, MAX_EXTRA_SENTENCES)):
        # position 0 draws from real opening sentences, position 1+ from real
        # follow-up sentences -- so a second line reads like a natural
        # continuation clause instead of an unrelated second opener.
        sentence = ""
        for _ in range(SENTENCE_ATTEMPTS):
            candidate = chain.generate(max_words=22, rng=rng, position=position, shape=shape)
            if candidate and _is_clean_sentence(candidate):
                sentence = candidate
                break
        if not sentence:
            continue
        sentence = sentence.replace("~", card_name)
        if not sentence.endswith((".", "!", "?")):
            sentence += "."
        lines.append(sentence)
    return lines
