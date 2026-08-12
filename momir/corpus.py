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

# Sentence "shape" -- the construct it belongs to -- kept separate from
# position (see above) because splicing across *these* is what produces the
# nonsense that reads as outright broken rather than just flavorfully odd:
# a chain wandering from a triggered ability into an unrelated activated
# ability's "cost:" clause mid-sentence, e.g. "...for each oil counter from
# this artifact: Destroy target artifact you control another." Bucketing by
# shape keeps a generated sentence's transitions confined to sentences of
# the same construct. See momir/markov.py's WordMarkovChain.
_TRIGGER_PREFIXES = ("When ", "Whenever ", "At the beginning of ")

# Line-level templating that only makes sense inside its own card frame --
# Saga chapters, Class/Level-up level headers, Case/d20-roll threshold rows,
# and Choose-one's bullet options (plus the "choose one --" line introducing
# them, which dangles without its bullets). Isolated as a lone sentence on
# some other creature, these can't read as sensible in *any* context, so
# they're dropped from training entirely rather than left for the Markov
# chain to (never successfully) make sense of.
_SAGA_CHAPTER_RE = re.compile(r"^[IVX]+(,\s*[IVX]+)*\s*[—-]")
_LEVEL_HEADER_RE = re.compile(r"^LEVEL\b", re.IGNORECASE)
_BARE_PT_RE = re.compile(r"^\*?[\d.]+\s*/\s*\*?[\d.]+$")


def _is_template_fragment(line: str) -> bool:
    if line.startswith("•") or line.rstrip().endswith("—"):
        return True
    if "|" in line:  # d20-roll / Case threshold rows, e.g. "1—9 | Draw a card."
        return True
    if _SAGA_CHAPTER_RE.match(line) or _LEVEL_HEADER_RE.match(line) or _BARE_PT_RE.match(line):
        return True
    return False


def _sentence_shape(sentence: str) -> str:
    if sentence.startswith(_TRIGGER_PREFIXES):
        return "trigger"
    if ":" in sentence:
        return "activated"
    return "static"


@dataclass
class Corpus:
    raw_cards: list[dict]

    # Real card names, split by naming style so name generation can favor
    # ordinary English words ("Grizzly Bears", "Goblin Piker") and only
    # occasionally reach for invented-sounding character names ("Jace",
    # "Chandra"). A card counts as a "character" name if its type line is
    # Legendary -- in practice that's reliably a personal name (with an
    # optional epithet after a comma), vs. ordinary creatures which are
    # named after their species/role. See momir/names.py.
    character_names: list[str] = field(default_factory=list)
    common_names: list[str] = field(default_factory=list)
    # cmc -> list of (sentence, position, shape) triples from creatures at
    # that cmc, where position is the sentence's index (0 = opener) within
    # its source card's oracle text, capped at MAX_SENTENCE_POSITION, and
    # shape is its construct type (see _sentence_shape). Kept per-cmc
    # (rather than one flat pool) so a 1-drop's generated text is trained
    # only on what 1-drops actually say -- not phrases pulled in from
    # eight-mana bombs -- kept per-position so generated text opens with
    # real openers rather than orphaned continuation clauses, and kept
    # per-shape so a generated triggered ability doesn't splice mid-sentence
    # into an unrelated activated ability's cost:effect clause -- see
    # momir/markov.py's WordMarkovChain and momir/text.py.
    sentences_by_cmc: dict[int, list[tuple[str, int, str]]] = field(default_factory=lambda: defaultdict(list))

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


def _extract_sentences(oracle_text: str | None, name: str = "") -> list[tuple[str, str]]:
    """Returns (sentence, shape) pairs; see _sentence_shape."""
    if not oracle_text:
        return []
    sentences = []
    for line in oracle_text.split("\n"):
        line = _REMINDER_TEXT_RE.sub("", line).strip()
        if not line or _is_template_fragment(line):
            continue
        if name:
            line = _normalize_self_references(line, name)
        for sentence in _SENTENCE_SPLIT_RE.split(line):
            sentence = sentence.strip()
            if sentence:
                sentences.append((sentence, _sentence_shape(sentence)))
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
            # Split/adventure cards ("Beanstalk Giant // Fertile Footsteps")
            # carry their non-creature-face name in the same string -- train
            # name generation on the creature face only.
            primary_name = name.split(" // ")[0].strip()
            if "Legendary" in (card.get("type_line") or ""):
                corpus.character_names.append(primary_name)
            else:
                corpus.common_names.append(primary_name)

        cmc = card.get("cmc")
        if cmc is None:
            continue
        cmc = int(cmc)

        for position, (sentence, shape) in enumerate(_extract_sentences(card.get("oracle_text"), name or "")):
            corpus.sentences_by_cmc[cmc].append((sentence, min(position, MAX_SENTENCE_POSITION), shape))

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
