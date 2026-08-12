"""
Loads the cached Scryfall creature data (see data/fetch_cards.py) and turns
it into the indices/corpora the generators train on.

Everything here is pure in-memory processing of already-fetched data -- no
network access happens at runtime.
"""
from __future__ import annotations

import functools
import json
import pathlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

CACHE_PATH = pathlib.Path(__file__).parent.parent / "data" / "cards_cache.json"

_REMINDER_TEXT_RE = re.compile(r"\([^)]*\)")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Corpus:
    raw_cards: list[dict]

    names: list[str] = field(default_factory=list)
    sentences: list[str] = field(default_factory=list)

    # cmc -> list of raw mana_cost strings actually used at that cmc
    mana_costs_by_cmc: dict[int, list[str]] = field(default_factory=lambda: defaultdict(list))

    # cmc -> list of (power, toughness) float pairs (numeric only)
    pt_by_cmc: dict[int, list[tuple[float, float]]] = field(default_factory=lambda: defaultdict(list))

    # cmc -> Counter of individual creature subtypes seen at that cmc
    subtypes_by_cmc: dict[int, Counter] = field(default_factory=lambda: defaultdict(Counter))

    # cmc -> Counter of keyword abilities seen at that cmc
    keywords_by_cmc: dict[int, Counter] = field(default_factory=lambda: defaultdict(Counter))

    rarities: list[str] = field(default_factory=list)

    @property
    def available_cmcs(self) -> list[int]:
        return sorted(self.mana_costs_by_cmc.keys())


def _numeric(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_subtypes(type_line: str) -> list[str]:
    if "—" not in type_line:
        return []
    _, _, subtypes = type_line.partition("—")
    return subtypes.split()


def _extract_sentences(oracle_text: str | None) -> list[str]:
    if not oracle_text:
        return []
    sentences = []
    for line in oracle_text.split("\n"):
        line = _REMINDER_TEXT_RE.sub("", line).strip()
        if not line:
            continue
        for sentence in _SENTENCE_SPLIT_RE.split(line):
            sentence = sentence.strip()
            if sentence:
                sentences.append(sentence)
    return sentences


def _load_raw() -> list[dict]:
    if not CACHE_PATH.exists():
        raise FileNotFoundError(
            f"No card cache found at {CACHE_PATH}. Run `python -m data.fetch_cards` first."
        )
    return json.loads(CACHE_PATH.read_text())


def build_corpus(raw_cards: list[dict] | None = None) -> Corpus:
    raw_cards = raw_cards if raw_cards is not None else _load_raw()
    corpus = Corpus(raw_cards=raw_cards)

    for card in raw_cards:
        name = card.get("name")
        if name:
            corpus.names.append(name)

        corpus.sentences.extend(_extract_sentences(card.get("oracle_text")))

        cmc = card.get("cmc")
        if cmc is None:
            continue
        cmc = int(cmc)

        mana_cost = card.get("mana_cost")
        if mana_cost:
            corpus.mana_costs_by_cmc[cmc].append(mana_cost)

        power, toughness = _numeric(card.get("power")), _numeric(card.get("toughness"))
        if power is not None and toughness is not None:
            corpus.pt_by_cmc[cmc].append((power, toughness))

        for subtype in _extract_subtypes(card.get("type_line", "")):
            corpus.subtypes_by_cmc[cmc][subtype] += 1

        for keyword in card.get("keywords") or []:
            corpus.keywords_by_cmc[cmc][keyword] += 1

        rarity = card.get("rarity")
        if rarity:
            corpus.rarities.append(rarity)

    return corpus


@functools.lru_cache(maxsize=1)
def get_corpus() -> Corpus:
    """Process-wide singleton so training only happens once per server run."""
    return build_corpus()
