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
import re
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


# Numeric slots in generated rules text, classified by local context and
# rerolled to a different real value of the same kind mined from the same
# mana-value pool -- see _extract_number_spans. A swapped number can never
# break grammar (only the digits/words change, never the surrounding
# sentence), so unlike word-level generation this needs no completeness
# check on the result.
_NUMBER_WORDS = (
    "one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty"
).split()
_COUNT_RE = r"(?:\d+|" + "|".join(_NUMBER_WORDS) + r")"
# "a"/"an" is the most common way oracle text expresses count = 1 ("draw a
# card"), but only counter_count/card_count swap it in -- "card" and
# "counter" are always regularly pluralized (card/cards, counter/counters),
# so a whole "a card" -> "three cards" phrase swap can't produce a bad
# plural the way a bare article swapped in front of an arbitrary noun could
# ("a Wizard" -> "three Wizard", missing the "s").
_ARTICLE_OR_COUNT_RE = r"(?:a|an|\d+|" + "|".join(_NUMBER_WORDS) + r")"
_QUALIFIER_RE = r"(?: or (?:more|less|greater|fewer))?"

# "create two 1/1 white Soldier creature tokens" -- the count and its plural
# noun ("tokens") are separated by a P/T-and-type description, so unlike
# counter_count/card_count they're not adjacent, and correctly rewriting
# both ends without touching the type words in between (deliberately out of
# scope) is a harder problem than the rest of this module solves. Matched
# and masked out before any real kind can claim it, rather than left to
# fall into the generic bucket, where an independent reroll could turn "a
# token" into "three token" (singular noun, no agreement rewrite).
# ponytail: token counts pass through unmodified; add real support if this
# turns out to matter (would need count+noun treated as one long-distance
# slot, not a local match).
_TOKEN_COUNT_GUARD_RE = re.compile(
    r"\bcreates? (?:up to )?" + _ARTICLE_OR_COUNT_RE + r"\b(?=[^.!?,]*\btokens?\b)", re.IGNORECASE
)
# A bare (unsigned) N/N is a stat block (a token's own power/toughness,
# "create a 3/3 ..."), not a delta -- pt_delta only matches the signed form.
# Masked out same as the token count above rather than left for the generic
# bare-digit fallback to claim each side independently ("1/1" -> "1/one").
_BARE_PT_RE = re.compile(r"\b\d+/\d+\b")

# (?<![-+]) on both: without it, "of +1/+1 counters" (no article/count word
# of its own -- "of" isn't one) still lets the pattern latch onto just the
# *second* number of that unrelated +1/+1 delta as if it were opening a
# fresh count ("1 counters"), since a bare \d+ alone before " counters"
# already satisfies the rest of the pattern. The character immediately
# before that second number is always its sign (+/-), not the "/" a couple
# characters further back -- excluding a sign immediately before the opener
# rules out ever starting mid-delta like that.
_COUNTER_COUNT_RE = re.compile(
    r"(?<![-+])\b(?:up to )?" + _ARTICLE_OR_COUNT_RE + r"(?: [+-]\d+/[+-]\d+)? counters?\b", re.IGNORECASE
)
_CARD_COUNT_RE = re.compile(r"(?<![-+])\b(?:up to )?" + _ARTICLE_OR_COUNT_RE + r" cards?\b", re.IGNORECASE)
_PT_DELTA_RE = re.compile(r"[+-]\d+/[+-]\d+")
_DAMAGE_RE = re.compile(r"\b" + _COUNT_RE + _QUALIFIER_RE + r" damage\b", re.IGNORECASE)
_LIFE_RE = re.compile(r"\b" + _COUNT_RE + _QUALIFIER_RE + r" life\b", re.IGNORECASE)
# Bare digits/number-words not already claimed above. Never matches inside
# {...} mana symbols ({2}, {X}) -- those are v1's territory (the cost head
# of an activated ability is already a real, atomic, verbatim/recombined
# clause; see _split_sentence), not a slot this pass touches.
_GENERIC_NUMBER_RE = re.compile(r"(?<!\{)\b(?:\d+|" + "|".join(_NUMBER_WORDS) + r")\b(?!\})", re.IGNORECASE)

# Most-specific-first: a counter-count phrase claims its embedded +N/+N
# delta before the standalone pt_delta pattern would otherwise grab it on
# its own, and so on down to the generic catch-all.
_NUMBER_KIND_PATTERNS = [
    (_COUNTER_COUNT_RE, "counter_count"),
    (_CARD_COUNT_RE, "card_count"),
    (_PT_DELTA_RE, "pt_delta"),
    (_DAMAGE_RE, "damage"),
    (_LIFE_RE, "life"),
    (_GENERIC_NUMBER_RE, "generic"),
]


# card_count and counter_count phrases are stored (and rerolled)
# separately by whether their own count is singular or plural ("a card" only
# ever swaps for another singular phrase, "two cards" only for another
# plural one) -- crossing that boundary breaks agreement the atomic-phrase
# swap doesn't otherwise cover: a later relative-clause verb or pronoun tied
# to the same count ("...or a card that HAS an Adventure" -> "...or ten
# cards that HAS..." if count and noun were allowed to cross classes).
def _count_class(value: str) -> str:
    rest = value[len("up to ") :] if value.lower().startswith("up to ") else value
    opener = rest.split(None, 1)[0].lower()
    return "singular" if opener in ("a", "an", "one", "1") else "plural"


def _extract_number_spans(text: str) -> list[tuple[int, int, str]]:
    """Numeric slots in `text` -- (start, end, kind) triples, non-
    overlapping, most-specific-first (see _NUMBER_KIND_PATTERNS). Token-
    creation counts and bare stat blocks are masked before anything else
    can claim them (see _TOKEN_COUNT_GUARD_RE / _BARE_PT_RE) so they're
    never returned as a slot at all. counter_count/card_count kinds carry a
    _singular/_plural suffix (see _count_class); generic never matches a
    bare "1"/"one" at all -- unlike counter_count/card_count it has no noun
    of its own to keep paired with the count, so swapping a bare 1 for a
    bare 2 (or back) would silently desync whatever noun follows it
    elsewhere in the line."""
    claimed = [(m.start(), m.end()) for m in _TOKEN_COUNT_GUARD_RE.finditer(text)]
    claimed += [(m.start(), m.end()) for m in _BARE_PT_RE.finditer(text)]
    spans: list[tuple[int, int, str]] = []
    for pattern, kind in _NUMBER_KIND_PATTERNS:
        for m in pattern.finditer(text):
            if kind == "generic" and m.group().lower() in ("1", "one"):
                continue
            if any(m.start() < end and start < m.end() for start, end in claimed):
                continue
            resolved_kind = f"{kind}_{_count_class(m.group())}" if kind in ("counter_count", "card_count") else kind
            spans.append((m.start(), m.end(), resolved_kind))
            claimed.append((m.start(), m.end()))
    return spans


def _mine_number_pools(sentences: list[tuple[str, int, str]]) -> dict[str, list[str]]:
    """Real observed values per numeric kind, mined from the same sentences
    a SentencePool already collected for its mana value -- see
    _extract_number_spans. Populated with the literal matched substrings
    (not parsed ints), same "sample the real text, never synthesize"
    approach as the rest of this file."""
    pools: dict[str, list[str]] = defaultdict(list)
    for sentence, _position, _shape in sentences:
        for start, end, kind in _extract_number_spans(sentence):
            pools[kind].append(sentence[start:end])
    return pools


@dataclass
class RerollVocab:
    """Precompiled detection regexes for keyword-name and creature-subtype
    references inside generated rules text -- built once per corpus, since
    the vocabulary itself doesn't vary by mana value. Which names actually
    become reroll candidates (and how often) is mana-value-scoped, mined
    separately into each SentencePool -- see _mine_reference_pools.

    keyword_re's match spans the qualifying word too ("has flying") --
    callers reroll only its captured group 1 (the name itself)."""

    keyword_re: re.Pattern | None
    subtype_re: re.Pattern | None


# A tracked "keyword" name shows up in real oracle text in several
# grammatical roles that a flat "match the word anywhere" regex can't tell
# apart: a standalone keyword line ("Flying"), a value-suffixed one
# ("Bushido 2"), an ability-word header ("Adamant -- If at least..."), or an
# imperative keyword-action verb ("Regenerate this creature.", "Scry 1.").
# Rerolling the name in any of those roles either breaks the pairing (a
# value/header no longer matching the swapped-in name) or the grammar
# outright (a verb swapped for an adjective-only ability name). The one role
# that's unambiguous and reliably common is the classic "creatures WITH
# flying", "target creature GAINS trample", "Equipped creature HAS
# hexproof" reference -- so keyword rerolling is restricted to exactly that,
# via a required qualifying word immediately before the name. Creature
# subtypes don't have this problem (always a plain noun/modifier -- "target
# Human creature", "Sacrifice a Goblin", never a verb or a value-suffixed
# line), so subtype_re stays unrestricted.
_KEYWORD_REFERENCE_QUALIFIERS = ("with", "has", "have", "gains", "gain", "gained")

# Even inside a qualified "has X"/"gains X" reference, some names carry a
# trailing modifier that belongs to that specific name, not to the sentence
# generally: a printed value ("has ward {2}"), an ability-word continuation
# ("Adamant --"), or a qualifier clause ("gains protection FROM red").
# Rerolling just the name and leaving that modifier in place produces
# exactly the "Haste {2}" class of nonsense this whole reroll pass exists to
# avoid. Requiring the name be followed by a clause boundary or a small set
# of safe continuations rules those cases out -- the match simply doesn't
# happen there at all, so the original text (value and all) passes through
# untouched, same as any other unmatched span.
_KEYWORD_SAFE_FOLLOWER_RE = r"(?=[.,;:!?]|\s+(?:and|or|until|as|while)\b|\s*$)"


def _keyword_reference_regex(names: set[str]) -> re.Pattern | None:
    if not names:
        return None
    ordered = sorted(names, key=len, reverse=True)  # longest first, so a multi-word name wins over a short name it contains
    qualifiers = "|".join(_KEYWORD_REFERENCE_QUALIFIERS)
    names_alt = "|".join(re.escape(n) for n in ordered)
    pattern = r"\b(?:" + qualifiers + r")\s+(" + names_alt + r")\b" + _KEYWORD_SAFE_FOLLOWER_RE
    return re.compile(pattern, re.IGNORECASE)


# "Champion a Faerie" vs "Champion an Elemental" -- an indefinite article
# immediately before the subtype has to agree with *that* subtype's leading
# sound, which a plain name-for-name swap can't preserve ("Champion an
# Cat"). Excluded via two fixed-width lookbehinds (re requires each to be
# fixed-width individually; "a " and "an " differ in length so they can't
# share one) rather than attempted -- the far more common unarticled
# reference ("target Human creature", "each Wizard you control") is
# unaffected.
def _subtype_reference_regex(names: set[str]) -> re.Pattern | None:
    if not names:
        return None
    ordered = sorted(names, key=len, reverse=True)
    names_alt = "|".join(re.escape(n) for n in ordered)
    return re.compile(r"(?<!\b(?i:a)\s)(?<!\b(?i:an)\s)\b(?:" + names_alt + r")\b")


def build_reroll_vocab(corpus: Corpus) -> RerollVocab:
    keyword_names = {name for counter in corpus.keywords_by_cmc.values() for name in counter}
    subtype_names = {name for counter in corpus.subtypes_by_cmc.values() for name in counter}
    return RerollVocab(
        keyword_re=_keyword_reference_regex(keyword_names),
        subtype_re=_subtype_reference_regex(subtype_names),
    )


def _mine_reference_pools(sentences: list[tuple[str, int, str]], vocab: RerollVocab) -> tuple[list[str], list[str]]:
    """Real observed keyword-name and creature-subtype values, mined from
    the same sentences a SentencePool already collected for its mana value
    -- restricted to `vocab`'s own regexes, so only names actually seen in
    the safe qualified context (keyword_re) or as a plain subtype word
    (subtype_re) ever become reroll candidates. This is why a keyword name
    that's only ever a header/verb/value-suffixed line in this corpus
    (e.g. "Domain", "Regenerate") never gets drawn as a replacement here,
    even though it's a perfectly real name overall -- _keyword_pool (the
    card's *own* Keywords line) draws from the full corpus distribution
    instead, a different, unrestricted pool for a different purpose."""
    keyword_refs: list[str] = []
    subtype_refs: list[str] = []
    for sentence, _position, _shape in sentences:
        if vocab.keyword_re is not None:
            keyword_refs += [m.group(1) for m in vocab.keyword_re.finditer(sentence)]
        if vocab.subtype_re is not None:
            subtype_refs += [m.group(0) for m in vocab.subtype_re.finditer(sentence)]
    return keyword_refs, subtype_refs


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
    number_pools: dict[str, list[str]]
    keyword_refs: list[str]
    subtype_refs: list[str]


def _build_pool(sentences: list[tuple[str, int, str]], vocab: RerollVocab) -> SentencePool:
    keyword_refs, subtype_refs = _mine_reference_pools(sentences, vocab)
    return SentencePool(*_bucket_sentences(sentences), _mine_number_pools(sentences), keyword_refs, subtype_refs)


def build_sentence_pools(corpus: Corpus, mana_values: range, vocab: RerollVocab) -> dict[int, SentencePool]:
    """One sentence pool per mana value, each drawn only from sentences of
    creatures at (or, if sparse, near) that mana value."""
    return {
        mana_value: _build_pool(_sentences_for_mana_value(corpus, mana_value), vocab) for mana_value in mana_values
    }


def build_mayhem_sentence_pool(corpus: Corpus, vocab: RerollVocab) -> SentencePool:
    """A single pool drawn from sentences at every mana value, for
    mayhem=text/full -- unlike build_sentence_pools this doesn't vary by
    mana value, so it's built once rather than per mana value."""
    all_sentences = list(itertools.chain.from_iterable(corpus.sentences_by_cmc.values()))
    return _build_pool(all_sentences, vocab)


def _pick(bucket: dict[tuple[str, int], list[str]], shape: str, position: int) -> list[str]:
    """The (shape, position) bucket, falling back to (shape, 0) -- the
    best-populated position -- if this position wasn't seen often enough at
    this mana value to have its own entries (e.g. a sparse mana value
    borrowing neighbor sentences)."""
    return bucket.get((shape, position)) or bucket.get((shape, 0)) or []


def _reroll_line(text: str, pool: SentencePool, vocab: RerollVocab | None, rng: random.Random) -> str:
    """Independently reroll every detected numeric/keyword/subtype slot in
    `text` for a different real value drawn from the matching pool -- a
    no-op for a slot whose pool is empty, or one that happens to redraw
    what was already there. Numeric, keyword, and subtype vocabularies
    never overlap in practice, so spans are only ever checked for overlap
    within their own category (_extract_number_spans already does this for
    numbers; re.finditer already returns non-overlapping matches within one
    regex), not across categories."""
    spans: list[tuple[int, int, list[str]]] = [
        (start, end, pool.number_pools.get(kind, [])) for start, end, kind in _extract_number_spans(text)
    ]
    if vocab is not None:
        if vocab.keyword_re is not None:
            # group 1 is the name alone -- the match also spans the
            # qualifying word before it ("has flying"), which stays put.
            spans += [(m.start(1), m.end(1), pool.keyword_refs) for m in vocab.keyword_re.finditer(text)]
        if vocab.subtype_re is not None:
            spans += [(m.start(), m.end(), pool.subtype_refs) for m in vocab.subtype_re.finditer(text)]

    for start, end, candidates in sorted(spans, key=lambda s: s[0], reverse=True):
        if candidates:
            text = text[:start] + rng.choice(candidates) + text[end:]
    return text


def generate_rules_text(
    pool: SentencePool,
    card_name: str,
    rng: random.Random | None = None,
    vocab: RerollVocab | None = None,
    force: bool = False,
) -> list[str]:
    rng = rng or random
    if not pool.shape_counts:
        return []
    if not force and rng.random() >= EXTRA_TEXT_CHANCE:
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
        sentence = _reroll_line(sentence, pool, vocab, rng)
        lines.append(sentence.replace("~", card_name))

    # position 0/1 can both come up empty if the chosen shape happens to have
    # no sentences at that exact position (e.g. it only ever appears as a
    # card's 2nd+ sentence) -- under force, fall back to any real sentence in
    # the pool rather than leave the card with no rules text at all.
    if force and not lines:
        fallback = [sentence for bucket in pool.sentences.values() for sentence in bucket]
        if fallback:
            sentence = _reroll_line(rng.choice(fallback), pool, vocab, rng)
            lines.append(sentence.replace("~", card_name))
    return lines
