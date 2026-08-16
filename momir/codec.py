"""Encode/decode a generated Card to and from a compact, self-contained
string (a "share code") -- no database involved. Every field needed to
redraw the card is embedded in the code itself, so decoding never re-runs
the Markov chains and never depends on the corpus or generator code being
unchanged since the card was made (unlike replaying a stored RNG seed would).

    code := "<version>." + base64url_nopad(zlib(canonical_json(card)))

The version prefix lets the payload shape change later without breaking
codes that are already saved/shared -- add a new branch in decode_card()
for old versions rather than repurposing this one.
"""
from __future__ import annotations

import base64
import binascii
import json
import zlib

import pydantic

from .models import Card

CODEC_VERSION = "1"


class CardCodeError(ValueError):
    """Raised when a share code is malformed, corrupt, or from an
    unsupported codec version -- callers turn this into a 422."""


def encode_card(card: Card) -> str:
    # share_code itself is excluded: it's derived from everything else, so
    # embedding it would make the payload depend on itself.
    payload = card.model_dump(mode="json", exclude={"share_code"})
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    compressed = zlib.compress(raw, level=9)
    b64 = base64.urlsafe_b64encode(compressed).rstrip(b"=").decode("ascii")
    return f"{CODEC_VERSION}.{b64}"


def decode_card(code: str) -> Card:
    version, _, b64 = code.partition(".")
    if not b64:
        raise CardCodeError("Malformed card code.")
    if version != CODEC_VERSION:
        raise CardCodeError(f"Unsupported card code version {version!r}.")

    padded = b64 + "=" * (-len(b64) % 4)
    try:
        compressed = base64.urlsafe_b64decode(padded)
        raw = zlib.decompress(compressed)
        payload = json.loads(raw)
        card = Card.model_validate(payload)
    except (binascii.Error, zlib.error, json.JSONDecodeError, pydantic.ValidationError) as exc:
        raise CardCodeError("Invalid or corrupt card code.") from exc

    card.share_code = code
    return card
