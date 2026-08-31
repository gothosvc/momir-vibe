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

# Formats build_corpus can restrict training data to (see its `legal_in`
# param). Keep in sync with data/fetch_cards.py's TRACKED_FORMATS -- that's
# what actually populates each cached card's "legal_formats" list; a format
# named here that wasn't tracked at fetch time would just silently produce
# an empty corpus.
SUPPORTED_FORMATS = ("standard", "pioneer", "modern")

# Mirrors colors.py's own _COLOR_ORDER -- kept as a separate copy rather
# than imported, since colors.py already imports Corpus from here and a
# reverse import would be circular.
_COLOR_ORDER = "WUBRG"

_REMINDER_TEXT_RE = re.compile(r"\([^)]*\)")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# Sentence position within a card's oracle text, capped so we get a handful of
# well-populated buckets (opener / 2nd sentence / later) instead of a long
# tail of buckets with one or two samples each. Position matters because real
# continuation clauses -- "If a spell is countered this way, exile it instead"
# -- only make sense following the sentence that set them up; letting one get
# picked as an opening line is what produces nonsensical-sounding generated
# text. See momir/text.py's SentencePool.
MAX_SENTENCE_POSITION = 2

# Sentence "shape" -- the construct it belongs to -- kept separate from
# position (see above) because mixing sentences across *these* is what
# produces nonsense that reads as outright broken rather than just
# flavorfully odd, e.g. pairing a triggered ability's condition with an
# unrelated activated ability's "cost:" effect. Bucketing by shape keeps a
# generated line's pieces confined to the same construct. See
# momir/text.py's SentencePool.
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


# A keyword name only ever appears on a handful of cards below this count --
# in practice that's reliably a card-unique ability-word title Scryfall filed
# under "keywords" alongside real reusable keywords ("Nitro-9", "For Auld
# Lang Syne"), not a real keyword ability. Verified against the live corpus:
# 389 of 641 distinct keyword names occur on 1-2 cards each, and are
# overwhelmingly one-off titles like these; real (if rare) mechanics start
# showing up at 3+.
MIN_KEYWORD_OCCURRENCES = 3


def _keyword_occurrence(oracle_text: str, keyword: str, name: str = "") -> str | None:
    """The value/cost suffix actually printed after `keyword` on this card
    ("" if it's bare), or None if `keyword` never literally appears in the
    text at all.

    The None case matters: Scryfall's keywords list includes umbrella family
    tags ("Landwalk", "Typecycling") alongside the specific variant actually
    printed ("Swampwalk", "Forestcycling") -- the umbrella tag itself is
    never real card text, so it shouldn't be offered up as a standalone
    generated keyword either.

    Some keyword values reference their own card by name (a Heroic trigger's
    "Whenever you cast a spell that targets <Name>") -- normalized to "~"
    same as sentence training, so it doesn't leak into a differently-named
    generated card. See _normalize_self_references.

    Only the trailing side is stripped: real templating uses two different
    conventions after a keyword name -- an alt-cost dash fuses directly
    ("Ward—Pay 2 life"), an ability word gets a space on both sides
    ("Landfall — Whenever ..."). Keeping the leading whitespace (or lack of
    it) verbatim reproduces whichever one the source card actually used,
    rather than guessing.

    Searched line by line -- skipping template-fragment and quoted lines,
    the same screening _extract_sentences applies to sentence training, see
    _is_template_fragment -- rather than across the whole text at once, so a
    keyword that happens to also appear inside e.g. a Choose-one bullet or a
    granted-ability's quoted sub-text on this card doesn't have that
    fragment's mismatched context captured as its value."""
    for line in _REMINDER_TEXT_RE.sub("", oracle_text).split("\n"):
        line = line.strip()
        if not line or _is_template_fragment(line) or '"' in line:
            continue
        if name:
            line = _normalize_self_references(line, name)
        match = re.search(rf"\b{re.escape(keyword)}\b([^,.;]*)", line, re.IGNORECASE)
        if match:
            return match.group(1).rstrip()
    return None


def _prune_rare_keywords(corpus: Corpus) -> None:
    totals: Counter = Counter()
    for counter in corpus.keywords_by_cmc.values():
        totals.update(counter)
    rare = {kw for kw, n in totals.items() if n < MIN_KEYWORD_OCCURRENCES}
    if not rare:
        return
    for counter in corpus.keywords_by_cmc.values():
        for kw in rare:
            counter.pop(kw, None)
    for values in corpus.keyword_values_by_cmc.values():
        for kw in rare:
            values.pop(kw, None)


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
    # shape is its construct type (see _sentence_shape). Three separate axes
    # of bucketing, each guarding against a different failure mode:
    #   - per-cmc, so a 1-drop's generated text is sampled only from what
    #     1-drops actually say, not phrases pulled in from eight-mana bombs;
    #   - per-position, so generated text opens with real openers rather
    #     than orphaned continuation clauses;
    #   - per-shape, so a generated triggered ability's condition never pairs
    #     with an unrelated activated ability's cost:effect clause.
    # See momir/text.py's SentencePool.
    sentences_by_cmc: dict[int, list[tuple[str, int, str]]] = field(default_factory=lambda: defaultdict(list))

    # cmc -> list of raw mana_cost strings actually used at that cmc
    mana_costs_by_cmc: dict[int, list[str]] = field(default_factory=lambda: defaultdict(list))

    # cmc -> list of (power, toughness) float pairs (numeric only)
    pt_by_cmc: dict[int, list[tuple[float, float]]] = field(default_factory=lambda: defaultdict(list))

    # cmc -> Counter of individual creature subtypes seen at that cmc
    subtypes_by_cmc: dict[int, Counter] = field(default_factory=lambda: defaultdict(Counter))

    # cmc -> Counter of keyword ability names seen at that cmc, restricted to
    # keywords we actually found literal printed text for on their source
    # card (see _keyword_occurrence) -- Scryfall's keywords list also
    # includes umbrella family tags ("Landwalk" alongside the "Swampwalk"
    # actually printed) that are never themselves real card text, and those
    # are dropped rather than offered up standalone. Rare one-off ability
    # words unique to a single card ("Nitro-9") are pruned separately by
    # _prune_rare_keywords.
    keywords_by_cmc: dict[int, Counter] = field(default_factory=lambda: defaultdict(Counter))

    # cmc -> keyword name -> list of real observed value/cost suffixes for
    # that keyword at that cmc (one entry per occurrence, "" for a bare
    # keyword with no parameter) -- e.g. "Ward" -> ["{1}", "{2}", "{2}", ...].
    # Sampling from this (rather than printing the bare keyword name) is
    # what keeps a generated "Ward" from showing up without its cost. See
    # momir/text.py's generate_keywords.
    keyword_values_by_cmc: dict[int, dict[str, list[str]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(list))
    )

    rarities: list[str] = field(default_factory=list)

    # Color identity tuple (WUBRG order, e.g. ("U", "B"); () for colorless)
    # -> list of (art_crop_url, artist) pairs from real creatures with that
    # exact color combination -- picking real art for a generated card
    # stays thematically plausible by matching on color, see momir/art.py.
    # Empty for a cache fetched before data/fetch_cards.py started keeping
    # art_crop_url/artist, which momir/art.py treats as "no art available"
    # rather than an error.
    art_by_colors: dict[tuple[str, ...], list[tuple[str, str]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    # Every (art_crop_url, artist) pair seen, regardless of color -- fallback
    # pool for a color combination with no real creatures of its own (rare,
    # e.g. 4-5 color identities), see momir/art.py.
    all_art: list[tuple[str, str]] = field(default_factory=list)

    @property
    def available_cmcs(self) -> list[int]:
        return sorted(self.mana_costs_by_cmc.keys())


def mana_value_weight(cmc: int, mana_value: int) -> float:
    """How much a cmc bucket should contribute to a mayhem pool centered on
    mana_value -- 1.0 at distance 0, decaying but never reaching zero, so
    mayhem still favors nearby mana values without fully excluding any."""
    return 1.0 / (1 + abs(cmc - mana_value))


def subtype_pool(corpus: Corpus, mana_value: int, mayhem: bool = False) -> Counter:
    """The creature-subtype Counter to draw from -- under mayhem, pooled
    across every mana value but weighted by distance (see
    mana_value_weight), else the requested one if it has any, else the
    nearest one that does."""
    if mayhem:
        combined: Counter = Counter()
        for cmc, counter in corpus.subtypes_by_cmc.items():
            weight = mana_value_weight(cmc, mana_value)
            for name, count in counter.items():
                combined[name] += count * weight
        return combined

    pool = corpus.subtypes_by_cmc.get(mana_value)
    if pool:
        return pool

    available = [cmc for cmc, counter in corpus.subtypes_by_cmc.items() if counter]
    if not available:
        return Counter()
    nearest = min(available, key=lambda cmc: (abs(cmc - mana_value), cmc))
    return corpus.subtypes_by_cmc[nearest]


def _numeric(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_subtypes(type_line: str) -> list[str]:
    # Split/adventure cards ("Creature — Giant // Sorcery — Adventure") carry
    # both faces' type lines in one string -- without this, partitioning on
    # the first em dash pulls in the *second* face's " // Sorcery —
    # Adventure" tail too, and "//"/"Sorcery"/"Adventure" leak into the
    # subtype pool as if they were real creature subtypes.
    type_line = type_line.split(" // ")[0]
    if "—" not in type_line:
        return []
    _, _, subtypes = type_line.partition("—")
    return subtypes.split()


_THE_EPITHET_RE = re.compile(r"^([A-Za-z][\w'-]*) the ")


def _self_reference_patterns(name: str) -> list[str]:
    """Strings within a card's own oracle text that refer back to itself, longest
    first so e.g. a full "Adeline, Resplendent Cathar" match wins out over the
    "Adeline" short form it contains. Split/adventure names ("Beanstalk Giant //
    Fertile Footsteps") contribute a pattern per face; legendary subtitles
    contribute a short "call name" too, since real oracle text refers to
    itself that way -- both the comma convention ("Aang, A Lot to Learn"
    refers to itself as "Aang") and the "the" epithet convention ("Eron the
    Relentless" refers to itself as "Eron"; "Regenerate Eron." is real
    printed text). The epithet short form is restricted to a single leading
    word so it doesn't misfire on an ordinary multi-word name that merely
    contains " the " deeper in a longer type of subtitle."""
    patterns: set[str] = set()
    for face in name.split(" // "):
        face = face.strip()
        if not face:
            continue
        patterns.add(face)
        comma_short = face.split(",")[0].strip()
        if comma_short:
            patterns.add(comma_short)
        epithet_match = _THE_EPITHET_RE.match(face)
        if epithet_match:
            patterns.add(epithet_match.group(1))
    return sorted(patterns, key=len, reverse=True)


def _normalize_self_references(text: str, name: str) -> str:
    """Replace a card's self-references with the "~" placeholder real MTG
    templating uses, so sentences trained from e.g. Aang don't leak "Aang"
    into a generated card named something else -- text.py substitutes the
    generated card's own name back in at generation time."""
    for pattern in _self_reference_patterns(name):
        text = re.sub(rf"\b{re.escape(pattern)}\b", "~", text)
    return text


# A bare "X" pronoun -- its value set elsewhere on the card, by an {X} spell
# cost, a "choose a number" cost, or a sibling sentence like "This creature
# enters with X +1/+1 counters on it" -- reads as nonsense once picked as an
# isolated training sentence divorced from whatever set it up: no generated
# card ever prints an {X} cost (see colors.py's synthesize_mana_cost) or
# carries a sibling sentence along with it, so "reveal the top X cards of
# your library" ends up with no X to resolve at all.
#
# Real oracle text does have sentences that ground X entirely within
# themselves, though, and those are fine to keep: a "where X is ..." clause
# spells it out explicitly, and an activated ability whose own cost names X
# ("Sacrifice X lands: Put X +1/+1 counters on this creature.") defines it
# by the act of paying that cost. Everything else referencing a bare X is
# presumed to lean on context this generator can never supply.
_BARE_X_RE = re.compile(r"\bX\b")
_WHERE_X_RE = re.compile(r"\bwhere X (is|was)\b", re.IGNORECASE)


def has_ungrounded_x(sentence: str, shape: str) -> bool:
    """True if `sentence` uses a bare X that isn't defined anywhere within
    itself -- see the comment above for why that's unresolvable here. A
    sentence failing this check is excluded from sentences_by_cmc entirely,
    so generation (momir/text.py) never sees one."""
    if not _BARE_X_RE.search(sentence):
        return False
    if "{X}" in sentence or _WHERE_X_RE.search(sentence):
        return False
    if shape == "activated" and ":" in sentence:
        cost, _, effect = sentence.partition(":")
        if _BARE_X_RE.search(cost) and _BARE_X_RE.search(effect):
            return False
    return True


def _extract_sentences(oracle_text: str | None, name: str = "") -> list[tuple[str, int, str]]:
    """Returns (sentence, position, shape) triples; see _sentence_shape.

    `position` counts every real candidate sentence in printed order --
    including ones dropped from training below (template fragments, quoted
    sub-abilities) -- not just the ones that made it through filtering. A
    sentence that presupposes an earlier real clause ("When you do, ...")
    needs to keep the position that reflects that, even when we chose not
    to train on the clause it presupposes; numbering based only on what
    survived filtering would let it collapse down to position 0 and get
    offered up as if it were a real opener. (Real case that surfaced this:
    Dáin Ironfoot's first sentence quotes a granted ability and gets
    dropped, and without this, "When you do, attach it to target creature
    you control." would shift into position 0.)"""
    if not oracle_text:
        return []
    sentences = []
    position = 0
    for line in oracle_text.split("\n"):
        line = _REMINDER_TEXT_RE.sub("", line).strip()
        if not line:
            continue
        if _is_template_fragment(line):
            position += 1
            continue
        if name:
            line = _normalize_self_references(line, name)
        for sentence in _SENTENCE_SPLIT_RE.split(line):
            sentence = sentence.strip()
            if not sentence:
                continue
            # Self-quoted sub-abilities ('...has "Whenever this creature
            # attacks, ..."') pair an opening and closing quote that can
            # land in different sentences (or get sentence-split apart
            # entirely) -- word-splicing has no way to keep them balanced,
            # so quoted sentences are dropped rather than left to produce
            # stray dangling quote marks. Sentences with an ungrounded bare
            # "X" are dropped for the same reason -- see has_ungrounded_x.
            shape = _sentence_shape(sentence)
            if '"' not in sentence and not has_ungrounded_x(sentence, shape):
                sentences.append((sentence, position, shape))
            position += 1
    return sentences


def _load_raw() -> list[dict]:
    if not CACHE_PATH.exists():
        raise FileNotFoundError(
            f"No card cache found at {CACHE_PATH}. Run `python -m data.fetch_cards` first."
        )
    return json.loads(CACHE_PATH.read_text())


def build_corpus(raw_cards: list[dict] | None = None, legal_in: str | None = None) -> Corpus:
    """`legal_in`, if given, restricts training data to cards legal in that
    format (see SUPPORTED_FORMATS) -- e.g. "modern" trains only on cards
    legal in Modern, so generated cards feel like they belong to that
    format's card pool rather than Magic's full 30-year history."""
    raw_cards = raw_cards if raw_cards is not None else _load_raw()
    if legal_in is not None:
        if legal_in not in SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported format {legal_in!r}; choose from {SUPPORTED_FORMATS}")
        raw_cards = [card for card in raw_cards if legal_in in (card.get("legal_formats") or [])]
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

        art_crop_url, artist = card.get("art_crop_url"), card.get("artist")
        if art_crop_url and artist:
            # Doesn't depend on cmc, unlike everything below -- indexed
            # ahead of the `cmc is None` check so a card missing cmc (if
            # one somehow slipped through fetch_cards.py's filtering) still
            # contributes its art.
            raw_colors = card.get("colors") or []
            color_key = tuple(c for c in _COLOR_ORDER if c in raw_colors)
            entry = (art_crop_url, artist)
            corpus.art_by_colors[color_key].append(entry)
            corpus.all_art.append(entry)

        cmc = card.get("cmc")
        if cmc is None:
            continue
        cmc = int(cmc)
        oracle_text = card.get("oracle_text") or ""

        for sentence, position, shape in _extract_sentences(oracle_text, name or ""):
            corpus.sentences_by_cmc[cmc].append((sentence, min(position, MAX_SENTENCE_POSITION), shape))

        mana_cost = card.get("mana_cost")
        if mana_cost:
            # Split/adventure cards ("{5}{B} // {1}{B}") carry both faces'
            # costs in one string, same as their name and type_line -- use
            # the creature face's cost only.
            corpus.mana_costs_by_cmc[cmc].append(mana_cost.split(" // ")[0].strip())

        power, toughness = _numeric(card.get("power")), _numeric(card.get("toughness"))
        if power is not None and toughness is not None:
            corpus.pt_by_cmc[cmc].append((power, toughness))

        for subtype in _extract_subtypes(card.get("type_line", "")):
            corpus.subtypes_by_cmc[cmc][subtype] += 1

        for keyword in card.get("keywords") or []:
            value = _keyword_occurrence(oracle_text, keyword, name or "")
            if value is None:
                continue
            corpus.keywords_by_cmc[cmc][keyword] += 1
            corpus.keyword_values_by_cmc[cmc][keyword].append(value)

        rarity = card.get("rarity")
        if rarity:
            corpus.rarities.append(rarity)

    _prune_rare_keywords(corpus)
    return corpus


@functools.lru_cache(maxsize=1)
def _get_raw_cards() -> list[dict]:
    """The full cached card list, read from disk once regardless of how many
    different `legal_in` corpora end up getting built from it."""
    return _load_raw()


@functools.lru_cache(maxsize=None)
def get_corpus(legal_in: str | None = None) -> Corpus:
    """One process-wide singleton per `legal_in` value, so training for a
    given format only happens once per server run -- see build_corpus."""
    return build_corpus(_get_raw_cards(), legal_in=legal_in)
