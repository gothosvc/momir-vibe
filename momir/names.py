"""Card name generation via a character-level Markov chain."""
from __future__ import annotations

from .corpus import Corpus
from .markov import CharMarkovChain

NAME_MARKOV_ORDER = 3


def build_name_chain(corpus: Corpus) -> CharMarkovChain:
    chain = CharMarkovChain(order=NAME_MARKOV_ORDER)
    chain.train(corpus.names)
    return chain
