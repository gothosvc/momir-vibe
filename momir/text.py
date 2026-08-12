"""
Rules text generation: a blend of two techniques.

- Keyword abilities (Flying, Trample, Deathtouch, ...) are sampled from the
  real frequency distribution of keywords seen on creatures at a similar
  mana value, so mechanically the card is meaningful and "on curve".
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


def _keyword_pool(corpus: Corpus, mana_value: int):
    pool = corpus.keywords_by_cmc.get(mana_value)
    if pool:
        return pool
    available = [cmc for cmc, counter in corpus.keywords_by_cmc.items() if counter]
    if not available:
        return None
    nearest = min(available, key=lambda cmc: (abs(cmc - mana_value), cmc))
    return corpus.keywords_by_cmc[nearest]


def generate_keywords(corpus: Corpus, mana_value: int, rng: random.Random | None = None) -> list[str]:
    rng = rng or random
    pool = _keyword_pool(corpus, mana_value)
    if not pool:
        return []

    count = rng.choice(KEYWORD_COUNT_WEIGHTS)
    if count == 0:
        return []

    keywords = list(pool.keys())
    weights = list(pool.values())

    chosen: list[str] = []
    for _ in range(min(count, len(keywords))):
        pick = rng.choices(keywords, weights=weights)[0]
        if pick not in chosen:
            chosen.append(pick)
    return chosen


def build_text_chain(corpus: Corpus) -> WordMarkovChain:
    chain = WordMarkovChain(order=2)
    chain.train(corpus.sentences)
    return chain


def generate_rules_text(
    chain: WordMarkovChain, card_name: str, rng: random.Random | None = None
) -> list[str]:
    rng = rng or random
    if rng.random() >= EXTRA_TEXT_CHANCE:
        return []

    lines: list[str] = []
    for _ in range(rng.randint(1, MAX_EXTRA_SENTENCES)):
        sentence = chain.generate(max_words=22, rng=rng)
        if not sentence:
            continue
        sentence = sentence.replace("~", card_name)
        if not sentence.endswith((".", "!", "?")):
            sentence += "."
        lines.append(sentence)
    return lines
