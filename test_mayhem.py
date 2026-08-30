"""Self-check for mayhem=True/full pool-flattening across colors/stats/types/text.

Run directly: python test_mayhem.py
"""
from collections import Counter

from momir.colors import synthesize_mana_cost
from momir.corpus import Corpus
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

    pt_pool = _nearest_pt_pool(corpus, mana_value=1, mayhem=True)
    assert (1.0, 1.0) in pt_pool and (9.0, 9.0) in pt_pool, "mayhem P/T pool should span every cmc"

    subtype_pool = _subtype_pool(corpus, mana_value=1, mayhem=True)
    assert subtype_pool["Human"] == 5 and subtype_pool["Dragon"] == 5, "mayhem subtype pool should merge every cmc"

    names, values = _keyword_pool(corpus, mana_value=1, mayhem=True)
    assert names["Flying"] == 5 and names["Trample"] == 5, "mayhem keyword pool should merge every cmc"
    assert values["Trample"] == [""], "mayhem keyword values should carry over from other cmcs"

    costs = {synthesize_mana_cost(corpus, mana_value=1, mayhem=True) for _ in range(50)}
    assert "{7}{U}{U}" in costs, "mayhem mana cost pool should reach a cost from another cmc"

    # non-mayhem stays scoped to the requested mana value
    scoped_pt = _nearest_pt_pool(corpus, mana_value=1, mayhem=False)
    assert scoped_pt == [(1.0, 1.0)], "mayhem=False must stay scoped to the requested mana value"

    print("ok")


if __name__ == "__main__":
    main()
