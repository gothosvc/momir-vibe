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

# Sentence position within a card's oracle text, capped so we get a handful of
# well-populated buckets (opener / 2nd sentence / later) instead of a long
# tail of buckets with one or two samples each. Position matters because real
# continuation clauses -- "If a spell is countered this way, exile it instead"
# -- only make sense following the sentence that set them up; letting one get
# picked as an opening line is what produces nonsensical-sounding generated
# text. See momir/markov.py.
MAX_SENTENCE_POSITION = 2


@dataclass
class Corpus:
    raw_cards: list[dict]

    names: list[str] = field(default_factory=list)
    # cmc -> list of (sentence, position) pairs from creatures at that cmc,
    # where position is the sentence's index (0 = opener) within its source
    # card's oracle text, capped at MAX_SENTENCE_POSITION. Kept per-cmc
    # (rather than one flat pool) so a 1-drop's generated text is trained
    # only on what 1-drops actually say -- not phrases pulled in from
    # eight-mana bombs -- and kept per-position so generated text opens with
    # real openers rather than orphaned continuation clauses.
    sentences_by_cmc: dict[int, list[tuple[str, int]]] = field(default_factory=lambda: defaultdict(list))

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


def _self_reference_patterns(name: str) -> list[str]:
    """Strings within a card's own oracle text that refer back to itself, longest
    first so e.g. a full "Adeline, Resplendent Cathar" match wins out over the
    "Adeline" short form it contains. Split/adventure names ("Beanstalk Giant //
    Fertile Footsteps") contribute a pattern per face; legendary subtitles
    ("Aang, A Lot to Learn") contribute the pre-comma short name too, since
    real oracle text refers to itself that way (see corpus.py's cached
    "Aang has vigilance..." example)."""
    patterns: set[str] = set()
    for face in name.split(" // "):
        face = face.strip()
        if not face:
            continue
        patterns.add(face)
        short = face.split(",")[0].strip()
        if short:
            patterns.add(short)
    return sorted(patterns, key=len, reverse=True)


def _normalize_self_references(text: str, name: str) -> str:
    """Replace a card's self-references with the "~" placeholder real MTG
    templating uses, so sentences trained from e.g. Aang don't leak "Aang"
    into a generated card named something else -- text.py substitutes the
    generated card's own name back in at generation time."""
    for pattern in _self_reference_patterns(name):
        text = re.sub(rf"\b{re.escape(pattern)}\b", "~", text)
    return text


def _extract_sentences(oracle_text: str | None, name: str = "") -> list[str]:
    if not oracle_text:
        return []
    sentences = []
    for line in oracle_text.split("\n"):
        line = _REMINDER_TEXT_RE.sub("", line).strip()
        if not line:
            continue
        if name:
            line = _normalize_self_references(line, name)
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

        cmc = card.get("cmc")
        if cmc is None:
            continue
        cmc = int(cmc)

        for position, sentence in enumerate(_extract_sentences(card.get("oracle_text"), name or "")):
            corpus.sentences_by_cmc[cmc].append((sentence, min(position, MAX_SENTENCE_POSITION)))

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
