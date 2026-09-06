"""Regression check for render.py: a Card always renders to a valid,
correctly-sized, B&W PNG, whether its art fetches successfully or not.

Run directly: python -m momir.test_render
"""
from __future__ import annotations

import io

from PIL import Image

from .models import Card
from .render import CARD_H, CARD_W, render_card_png

_BASE_KWARGS = dict(
    name="Test Creature",
    mana_cost="{2}{G}{G}",
    mana_value=4,
    colors=["G"],
    color_identity=["G"],
    type_line="Legendary Creature — Human Wizard",
    power=3,
    toughness="*",
    keywords=["Flying", "Ward {2}"],
    rules_text=[
        "Whenever this creature attacks, put a +1/+1 counter on it.",
        "This creature's toughness is equal to the number of creatures you control.",
    ],
    rarity="mythic",
    set_name="Momir Vibe",
    set_code="MMV",
    collector_number="1",
    artist="Test Artist",
)


def _assert_valid_png(png: bytes) -> None:
    assert png, "render_card_png returned no bytes"
    img = Image.open(io.BytesIO(png))
    assert img.size == (CARD_W, CARD_H), f"unexpected size: {img.size}"
    assert img.mode == "1", f"expected dithered B&W ('1') mode, got {img.mode}"


def main() -> None:
    # No art_url at all -- corpus predates art data, same case the web UI
    # already handles via its own placeholder.
    card = Card(**_BASE_KWARGS, art_url=None)
    _assert_valid_png(render_card_png(card))

    # An unreachable art_url -- exercises the actual except branch in
    # _fetch_art (a real failed request, not a mock of one), same as a
    # timeout or a dead link would.
    card = Card(**_BASE_KWARGS, art_url="http://127.0.0.1:1/no-such-server.jpg")
    _assert_valid_png(render_card_png(card))

    # A very long name/type line and a long list of rules text shouldn't
    # crash (truncation/wrapping/overflow handling) even though it's well
    # beyond what the generator actually produces.
    card = Card(
        **{
            **_BASE_KWARGS,
            "name": "A Very Long Legendary Creature Name That Should Get Truncated",
            "type_line": "Legendary Creature — Human Wizard Advisor Warrior Cleric Rogue",
            "rules_text": [
                "This is the first long sentence of rules text, long enough to wrap onto more than one line.",
                "This is a second long sentence of rules text, also long enough to wrap onto more than one line.",
                "A third sentence that should be silently dropped once the rules-text box runs out of room.",
            ],
        },
        art_url=None,
    )
    _assert_valid_png(render_card_png(card))

    print("ok")


if __name__ == "__main__":
    main()
