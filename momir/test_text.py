"""Regression check for generate_rules_text: every generated line must be
built entirely from real pieces -- a whole real sentence or a real
condition/cost half concatenated with a real effect half of the same shape,
with only its numeric/keyword/subtype slots (if any) rerolled to a
different real value of the same kind (see momir/text.py's SentencePool and
RerollVocab).

Run directly: python -m momir.test_text
"""
from __future__ import annotations

import random
from collections import Counter

from .corpus import Corpus
from .text import RerollVocab, _extract_number_spans, build_reroll_vocab, build_sentence_pools, generate_rules_text

CARD_NAME = "Test Creature"
MANA_VALUE = 3

TRIGGER_SENTENCES = [
    "Whenever this creature attacks, it gets +2/+2 until end of turn.",
    "Whenever this creature blocks, it gets +5/+5 until end of turn.",
    "Whenever ~ enters, you gain 2 life.",
    "Whenever ~ dies, you lose 3 life.",
]
ACTIVATED_SENTENCES = [
    "{1}, Sacrifice this creature: Draw a card.",
    "{2}, Sacrifice another creature: Draw two cards.",
    "{7}: Regenerate this creature.",  # distinctive cost -- must never change
    "{T}: Add one mana of any color.",
]
STATIC_SENTENCES = [
    "~ can't be blocked except by creatures with flying.",
    "Other Human creatures you control get +1/+0.",
    "Put a +1/+1 counter on this creature.",
    "Put three -1/-1 counters on target creature.",
    "Create two 1/1 white Soldier creature tokens.",  # count must never change
]


def _build_corpus() -> Corpus:
    corpus = Corpus(raw_cards=[])
    corpus.sentences_by_cmc[MANA_VALUE] = (
        [(s, 0, "trigger") for s in TRIGGER_SENTENCES]
        + [(s, 0, "activated") for s in ACTIVATED_SENTENCES]
        + [(s, 0, "static") for s in STATIC_SENTENCES]
    )
    # "regenerate" is a real tracked keyword that never appears in a safe
    # "has/have/gains X" context in this fixture corpus (only as the verb in
    # ACTIVATED_SENTENCES' "Regenerate this creature.") -- it must never be
    # drawn as a keyword_ref reroll candidate, only ever appear verbatim.
    corpus.keywords_by_cmc[MANA_VALUE] = Counter({"flying": 5, "trample": 3, "regenerate": 2})
    corpus.subtypes_by_cmc[MANA_VALUE] = Counter({"Human": 4, "Wizard": 2})
    return corpus


def _skeleton(text: str, vocab: RerollVocab) -> str:
    """`text` with every reroll-able span replaced by a placeholder naming
    its kind -- two lines that only differ by which real value fills a slot
    reduce to the same skeleton, so comparing skeletons checks structure
    independently of which real value was drawn."""
    spans = [(start, end, f"<{kind}>") for start, end, kind in _extract_number_spans(text)]
    if vocab.keyword_re:
        spans += [(m.start(1), m.end(1), "<keyword>") for m in vocab.keyword_re.finditer(text)]
    if vocab.subtype_re:
        spans += [(m.start(), m.end(), "<subtype>") for m in vocab.subtype_re.finditer(text)]
    for start, end, placeholder in sorted(spans, key=lambda s: s[0], reverse=True):
        text = text[:start] + placeholder + text[end:]
    return text


def main() -> None:
    corpus = _build_corpus()
    vocab = build_reroll_vocab(corpus)
    pool = build_sentence_pools(corpus, range(MANA_VALUE, MANA_VALUE + 1), vocab)[MANA_VALUE]

    assert not pool.heads.get(("static", 0)), "static sentences must never be split"
    assert not pool.tails.get(("static", 0)), "static sentences must never be split"
    assert "regenerate" not in {v.lower() for v in pool.keyword_refs}, (
        "a keyword only ever seen as a verb/header must never become a reroll candidate"
    )

    known_texts = set(TRIGGER_SENTENCES + ACTIVATED_SENTENCES + STATIC_SENTENCES) | {
        head + tail
        for shape in ("trigger", "activated")
        for head in pool.heads[(shape, 0)]
        for tail in pool.tails[(shape, 0)]
    }
    known_skeletons = {_skeleton(t.replace("~", CARD_NAME), vocab) for t in known_texts}

    seen_values: dict[str, set[str]] = {}
    for seed in range(1000):
        rng = random.Random(seed)
        lines = generate_rules_text(pool, CARD_NAME, rng=rng, vocab=vocab)
        assert len(lines) <= 2, f"got {len(lines)} lines, expected at most MAX_EXTRA_SENTENCES"
        for line in lines:
            assert _skeleton(line, vocab) in known_skeletons, f"line doesn't match any known skeleton: {line!r}"
            assert "regenerate" not in line.lower() or line.endswith("Regenerate this creature."), (
                f"keyword-only-as-verb leaked into a reroll: {line!r}"
            )
            if "{7}" in line:
                # the head can still land on a recombined tail (v1
                # behavior, unrelated to this feature) -- what must never
                # happen is the cost digit itself changing.
                assert line.startswith("{7}: "), f"cost digits changed: {line!r}"
            if "Soldier" in line:
                assert line == "Create two 1/1 white Soldier creature tokens.", f"token count changed: {line!r}"
            for start, end, kind in _extract_number_spans(line):
                value = line[start:end]
                assert value in pool.number_pools[kind], f"{kind} value not from real pool: {value!r}"
                seen_values.setdefault(kind, set()).add(value)
            if vocab.keyword_re:
                for m in vocab.keyword_re.finditer(line):
                    value = m.group(1)
                    assert value in pool.keyword_refs, f"keyword value not from real pool: {value!r}"
                    seen_values.setdefault("keyword", set()).add(value)
            if vocab.subtype_re:
                for m in vocab.subtype_re.finditer(line):
                    value = m.group(0)
                    assert value in pool.subtype_refs, f"subtype value not from real pool: {value!r}"
                    seen_values.setdefault("subtype", set()).add(value)

    for kind, real_values in {**pool.number_pools, "keyword": pool.keyword_refs, "subtype": pool.subtype_refs}.items():
        if len(set(real_values)) > 1:
            assert len(seen_values.get(kind, ())) > 1, f"{kind} never visibly rerolled across 1000 seeds"

    empty_corpus = Corpus(raw_cards=[])
    empty_vocab = build_reroll_vocab(empty_corpus)
    empty_pool = build_sentence_pools(empty_corpus, range(0, 1), empty_vocab)[0]
    assert generate_rules_text(empty_pool, CARD_NAME, rng=random.Random(0)) == []

    print("ok")


if __name__ == "__main__":
    main()
