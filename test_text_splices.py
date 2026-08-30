"""Regression check for _is_complete_sentence's dangling-object splice guard.

Run directly: python test_text_splices.py
"""
from momir.text import _is_complete_sentence


def main() -> None:
    # Real splice: "it fights target creature ..." never resolves its object
    # before an unrelated sentence's "attacks" hijacks the clause via the
    # shared phrase "creature an opponent controls".
    spliced = (
        "When this creature enters, it fights target creature an opponent "
        "controls attacks one of your opponents, double its power until end of turn."
    )
    assert not _is_complete_sentence(spliced, "trigger"), "dangling-object splice should be rejected"

    # A real, complete trigger sentence using the same verb shouldn't be
    # rejected as a false positive.
    legit = "When this creature enters, it fights target creature you control."
    assert _is_complete_sentence(legit, "trigger"), "a real, resolved 'fights target creature' sentence must pass"

    # Same splice, entered via "up to N" cardinality rather than a bare verb
    # -- the "to" in "up to" isn't the object-resolving preposition.
    spliced_up_to = (
        "When this creature enters, return up to one target creature an opponent "
        "controls attacks one of your opponents, double its power until end of turn."
    )
    assert not _is_complete_sentence(spliced_up_to, "trigger"), "'up to N' splice should be rejected"

    # A real "return up to N ... to" sentence must still pass once its
    # object actually resolves.
    legit_up_to = "Return up to one target creature you control to its owner's hand."
    assert _is_complete_sentence(legit_up_to, None), "a real, resolved 'return up to N ... to' sentence must pass"

    print("ok")


if __name__ == "__main__":
    main()
