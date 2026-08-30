"""Self-check for mayhem=True/full distance-weighted pooling across colors/stats/types/text.

Run directly: python test_mayhem.py
"""
import random
from collections import Counter

from momir.colors import _is_generic, parse_symbols, synthesize_mana_cost
from momir.corpus import Corpus, mana_value_weight
from momir.stats import _nearest_pt_pool
from momir.text import _keyword_pool
from momir.types import _subtype_pool


def _corpus() -> Corpus:
    corpus = Corpus(raw_cards=[])
    corpus.mana_costs_by_cmc[1] = ["{W}"]
    corpus.mana_costs_by_cmc[9] = ["{7}{U}{U}"]
    corpus.pt_by_cmc[1] = [(1.0, 1.0)]
    corpus.pt_by_cmc[9] = [(9.0, 9.0)]
    corpus.subtypes_by_cmc[1] = Counter({"Human": 5})
    corpus.subtypes_by_cmc[9] = Counter({"Dragon": 5})
    corpus.keywords_by_cmc[1] = Counter({"Flying": 5})
    corpus.keywords_by_cmc[9] = Counter({"Trample": 5})
    corpus.keyword_values_by_cmc[9]["Trample"] = [""]
    return corpus


def main() -> None:
    corpus = _corpus()

    assert mana_value_weight(1, 1) > mana_value_weight(9, 1), "nearer cmc must weigh more than a farther one"

    pool, weights = _nearest_pt_pool(corpus, mana_value=1, mayhem=True)
    by_pt = dict(zip(pool, weights))
    assert by_pt[(1.0, 1.0)] > by_pt[(9.0, 9.0)], "mayhem P/T pool should favor the requested mana value"

    subtype_pool = _subtype_pool(corpus, mana_value=1, mayhem=True)
    assert subtype_pool["Human"] > subtype_pool["Dragon"] > 0, "mayhem subtypes: reachable but distance-weighted"

    names, values = _keyword_pool(corpus, mana_value=1, mayhem=True)
    assert names["Flying"] > names["Trample"] > 0, "mayhem keywords: reachable but distance-weighted"
    assert values["Trample"] == [""], "mayhem keyword values should carry over from other cmcs"

    # Regression: a template borrowed from a far cmc (here, cmc 9's
    # "{7}{U}{U}") must be rebuilt to the requested mana value, not returned
    # as-is -- a mayhem=full card whose printed cost doesn't sum to its own
    # mana_value looks broken, not chaotic.
    rng = random.Random(0)
    for _ in range(200):
        cost = synthesize_mana_cost(corpus, mana_value=4, rng=rng, mayhem=True)
        symbols = parse_symbols(cost)
        total = sum(int(s) if _is_generic(s) else 1 for s in symbols)
        assert total == 4, f"mayhem mana cost {cost} sums to {total}, not the requested mana value"

    # non-mayhem stays scoped to the requested mana value
    scoped_pool, scoped_weights = _nearest_pt_pool(corpus, mana_value=1, mayhem=False)
    assert scoped_pool == [(1.0, 1.0)] and scoped_weights is None, "mayhem=False must stay scoped, unweighted"

    print("ok")


if __name__ == "__main__":
    main()
