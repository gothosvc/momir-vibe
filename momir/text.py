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

import itertools
import random
from collections import Counter

from .corpus import Corpus, has_ungrounded_x, mana_value_weight
from .markov import WordMarkovChain

KEYWORD_COUNT_WEIGHTS = [0, 0, 1, 1, 1, 2]  # skewed toward 0-1 keywords, occasionally more
# ~97% of real creatures print some oracle text; 0.55 was making generated
# cards noticeably blanker than that. Raised to sit closer to reality while
# still leaving room for the occasional vanilla creature.
EXTRA_TEXT_CHANCE = 0.85
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
# _is_complete_sentence) rather than ever showing one.
SENTENCE_ATTEMPTS = 8

# Word cap per generated line. Real individual sentences run up to ~31 words
# at the 99th percentile, so this is generous headroom -- a chain still
# going this long is more likely mid-splice than genuinely still inside one
# real sentence. See _is_complete_sentence for what happens when it's hit.
MAX_SENTENCE_WORDS = 32

# Minimum sentence pool a mana value's text chain wants before we trust it to
# generate coherent output. Sparse mana values (very low or very high mv have
# few real creatures) borrow sentences from progressively wider neighboring
# mana values -- never from the corpus at large -- to stay in the right
# power-level neighborhood while still having enough to train on.
#
# 60 was too low. At order 3 (see TEXT_MARKOV_ORDER), a few hundred
# sentences still isn't enough distinct overlap for the chain to branch
# anywhere -- most 3-word keys have exactly one real continuation, so
# generation just plays a source sentence back verbatim. Measured duplicate-
# line rate across repeated generations at unchanged mana value, at the old
# threshold: cmc 9 83%, cmc 10 85%, cmc 12 87% -- often enough to read as
# "samey". Raised to 1000 so the same borrowing mechanism kicks in earlier:
# every mana value's duplicate rate now lands in the same ~3-27% band native
# bulk mana values (2-6) already sit in, cmc 9-16 included.
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


def build_mayhem_text_chain(corpus: Corpus) -> WordMarkovChain:
    """A single chain trained on sentences from every mana value, for
    mayhem=text/full -- unlike build_text_chains this doesn't vary by mana
    value, so it's built once rather than per mana value."""
    chain = WordMarkovChain(order=TEXT_MARKOV_ORDER)
    chain.train(list(itertools.chain.from_iterable(corpus.sentences_by_cmc.values())))
    return chain


# Verbs that always open a directive clause needing a completed object
# ("return TARGET CREATURE to its owner's hand", "tap target creature",
# "put a -1/-1 counter ON IT") -- see _is_complete_sentence's dangling-object
# check. Deliberately excludes ambiguous words like "counter" (usually the
# noun in "+1/+1 counter", only occasionally the verb "counter target
# spell") where the false-positive rate from misreading the noun would
# outweigh the real catches.
_DANGLING_OBJECT_VERBS = {
    "return", "exile", "destroy", "tap", "untap", "sacrifice", "bounce", "regenerate", "put",
    "fight", "fights", "goad", "detain",
}
# Words that resolve a dangling verb's object phrase -- once one of these
# shows up, the clause has somewhere for its object to land. "and"/"or" are
# in here for "put"'s sake: real oracle text routinely resolves its object
# by coordinating a whole new clause onto it rather than a preposition
# ("put a +1/+1 counter on this creature AND you gain 1 life", "...counter
# on this creature OR this creature gains flying") -- without treating that
# as a resolver too, those real, common sentences would misfire the same
# check meant to catch the splice.
_OBJECT_RESOLVERS = {"to", "from", "under", "instead", "and", "or"}
# Verbs that only make sense introducing a *new* clause's own subject
# ("...creature you control GETS +2/+2", "...you control DIES", "...you
# control BECOME 3/3 artifact creatures", "...creatures you control HAVE
# flying"). Real oracle text pairs these only with a subject that was never
# anyone else's object, so one showing up while a verb's object is still
# unresolved is a splice. Sized against the actual frequency of "you control
# <verb>" in the training corpus, not just the one reported case, since any
# omitted high-frequency verb reopens the identical splice on a different
# verb pair.
_CLAUSE_SUBJECT_VERBS = {
    "gets", "get", "gains", "gain", "dies", "die", "attacks", "attack",
    "enters", "enter", "deals", "deal", "becomes", "become", "have", "has",
    "is", "are", "leaves", "counts", "explores",
}


def _is_complete_sentence(sentence: str, shape: str | None) -> bool:
    """Backstop against generations that never actually reached a genuine
    sentence end. Catches four distinct ways a chain can go wrong:

    1. **Cut off by the word cap.** Every trained sentence carries its own
       real terminal punctuation (that's how corpus.py split sentences out
       of oracle text in the first place), so a chain that reaches a real
       ending should too. One that doesn't got cut off mid-clause instead,
       which otherwise reads as a dangling fragment with a period stapled
       onto whatever word it happened to stop on (".../, and.", "...play
       that."). Also catches the same double-colon/bullet/pipe tells as
       before -- most commonly two same-shape activated-ability clauses
       fusing into a double "cost: effect: effect".

    2. **A dangling-object splice that still ends cleanly.** A sentence
       opens a directive verb's object ("return a black creature you
       control") but, before that object ever resolves (a preposition, a
       comma, a new clause), gets hijacked mid-object by an unrelated
       clause's verb ("...gets +2/+0 until end of turn"). This happens
       because two real sentences -- "Return another creature you control
       to its owner's hand." and "Another creature you control gets +2/+2
       until end of turn." -- share the long common run "creature you
       control", which is enough real match at any Markov order to walk
       from one straight into the other: the chain has no memory that
       "return" back at the start of the clause is still waiting on an
       object once it's a few words further along. See momir/markov.py.

    3. **A freshly-spliced, unresolvable "X".** Training already excludes
       real sentences where X's value isn't defined within that same
       sentence (see corpus.py's has_ungrounded_x) -- e.g. "This creature
       enters with X +1/+1 counters on it." never made it in, since nothing
       about a generated card can supply what X is. But the chain can still
       *produce* that same failure fresh: it opens on a real X sentence's
       start, then, before ever reaching its grounding "where X is ..."
       clause, wanders off into a same-shape continuation that doesn't have
       one. Same splice mechanism as case 2, just with a defining clause
       going missing instead of a whole object. Run through the identical
       check used at training time so a freshly-spliced ungrounded X is
       caught the same way a pre-existing one would have been.

    4. **Missing structural punctuation.** Every real trigger sentence is a
       condition clause, a comma, then an effect ("Whenever X, Y."), and
       every real activated-ability sentence is a cost, a colon, then an
       effect ("Cost: Y." -- see corpus.py's _sentence_shape). A chain that
       walks straight from the condition/cost into a same-tail effect
       clause without ever emitting that separator -- the same splice
       mechanism again -- produces a sentence that's actually just the
       condition or cost alone with an effect's tail bolted on, e.g.
       "Whenever an equipped creature you control gets +3/+0 until end of
       turn." (missing the triggering event and its comma) or "{2}, Exile a
       creature card from your graveyard to the battlefield." (missing the
       colon and the ability that cost was for). Checked directly against
       `shape` rather than re-deriving it from the text, since the caller
       already knows which one was requested."""
    if not sentence.endswith((".", "!", "?")):
        return False
    if sentence.count(":") >= 2:
        return False
    if "•" in sentence or "|" in sentence:
        return False
    if shape == "trigger" and "," not in sentence:
        return False
    if shape == "activated" and ":" not in sentence:
        return False
    if has_ungrounded_x(sentence, shape or ""):
        return False
    dangling = False
    prev_core = ""
    for word in sentence.split():
        core = word.strip(".,!?:;").lower()
        if dangling and core in _CLAUSE_SUBJECT_VERBS:
            return False
        if core in _DANGLING_OBJECT_VERBS:
            dangling = True
        # "to" in "up to N target creatures" is cardinality, not the
        # object-resolving "return X TO Y" -- the object hasn't even been
        # named yet at that "to", so it doesn't get to resolve anything.
        elif core in _OBJECT_RESOLVERS and not (core == "to" and prev_core == "up"):
            dangling = False
        if any(punct in word for punct in ",:.!?"):
            dangling = False
        prev_core = core
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
            candidate = chain.generate(max_words=MAX_SENTENCE_WORDS, rng=rng, position=position, shape=shape)
            if candidate and _is_complete_sentence(candidate, shape):
                sentence = candidate
                break
        if not sentence:
            continue
        lines.append(sentence.replace("~", card_name))
    return lines
