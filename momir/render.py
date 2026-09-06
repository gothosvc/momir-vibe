"""Renders a Card as a black & white, card-shaped PNG for thermal-printed
proxies -- a "printer" that expects to pull a random full-card *image* (the
way it might pull one from Scryfall) can point at GET /cards/generate/image
instead of the JSON endpoint.

Drawn fresh with Pillow rather than reusing static/style.css: no headless
browser dependency, and the color-only parts of the web card (frame
gradient per color, rarity gem) have no B&W equivalent worth building.
Nothing here is ever written to disk -- render_card_png returns PNG bytes
from an in-memory buffer, so there's nothing to clean up after serving it.
"""
from __future__ import annotations

import functools
import io
import pathlib

import requests
from PIL import Image, ImageDraw, ImageFont

from . import colors
from .models import Card

FONTS_DIR = pathlib.Path(__file__).parent.parent / "data" / "fonts"

# 2.5in x 3.5in at 300dpi -- the common MTG proxy-printing convention, and
# close enough to real card dimensions for a thermal label printer (as
# opposed to a narrow receipt-strip printer, which this isn't targeting).
CARD_W = 750
CARD_H = 1050

MARGIN = 30
GAP = 14

HEADER_BOX = (MARGIN, MARGIN, CARD_W - MARGIN, MARGIN + 100)
ART_BOX = (MARGIN, HEADER_BOX[3] + GAP, CARD_W - MARGIN, HEADER_BOX[3] + GAP + 440)
TYPE_BOX = (MARGIN, ART_BOX[3] + GAP, CARD_W - MARGIN, ART_BOX[3] + GAP + 56)
RULES_BOX = (MARGIN, TYPE_BOX[3] + GAP, CARD_W - MARGIN, TYPE_BOX[3] + GAP + 310)
META_Y = RULES_BOX[3] + 16

PIP_RADIUS = 26
PT_BADGE_W, PT_BADGE_H = 110, 56

_FONT_REGULAR = FONTS_DIR / "PTSerif-Regular.ttf"
_FONT_BOLD = FONTS_DIR / "PTSerif-Bold.ttf"

font_name = ImageFont.truetype(str(_FONT_BOLD), 40)
font_type = ImageFont.truetype(str(_FONT_BOLD), 28)
font_body = ImageFont.truetype(str(_FONT_REGULAR), 26)
font_keyword = ImageFont.truetype(str(_FONT_BOLD), 26)
font_meta = ImageFont.truetype(str(_FONT_REGULAR), 18)
font_pt = ImageFont.truetype(str(_FONT_BOLD), 30)
font_pip = ImageFont.truetype(str(_FONT_BOLD), 20)
font_placeholder = ImageFont.truetype(str(_FONT_BOLD), 24)


# Scryfall's CDN rejects requests with a generic/default User-Agent (same
# as data/fetch_cards.py's own HEADERS, needed there for the same reason).
_HEADERS = {"User-Agent": "MomirVibeCardGenerator/0.1 (personal hobby project; https://github.com/gothosvc/momir-vibe)"}


@functools.lru_cache(maxsize=128)
def _fetch_art(url: str) -> Image.Image | None:
    """The art crop at `url`, in grayscale, or None on any fetch/decode
    failure -- callers fall back to the plain placeholder box, same as the
    web UI does when the corpus has no art data at all. Cached by URL since
    the same real-card art gets borrowed by many generated cards."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=5)
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content)).convert("L")
    except Exception:
        return None


def _fit_cover(img: Image.Image, box_w: int, box_h: int) -> Image.Image:
    """Resize + center-crop `img` to exactly fill a box_w x box_h box --
    the standard fixed-art-box behavior real printed cards use (simpler
    than the web UI's per-image aspect-ratio matching, which only exists to
    avoid ugly cropping in a browser)."""
    scale = max(box_w / img.width, box_h / img.height)
    resized = img.resize((round(img.width * scale), round(img.height * scale)))
    left = (resized.width - box_w) // 2
    top = (resized.height - box_h) // 2
    return resized.crop((left, top, left + box_w, top + box_h))


def _truncate(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    if font.getlength(text) <= max_width:
        return text
    ellipsis = "…"
    while text and font.getlength(text + ellipsis) > max_width:
        text = text[:-1]
    return text + ellipsis


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if not current or font.getlength(trial) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_text_centered(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, font, fill=0) -> None:
    bbox = font.getbbox(text)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = box[0] + (box[2] - box[0] - w) / 2 - bbox[0]
    y = box[1] + (box[3] - box[1] - h) / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=fill)


def _draw_pip(draw: ImageDraw.ImageDraw, cx: float, cy: float, symbol: str) -> None:
    r = PIP_RADIUS
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=0, width=2, fill=255)
    _draw_text_centered(draw, (cx - r, cy - r, cx + r, cy + r), symbol, font_pip)


def _draw_mana_cost(draw: ImageDraw.ImageDraw, mana_cost: str, right_edge: int, cy: float) -> int:
    """Draws right-aligned mana pips ending at right_edge, returns the x
    position where they start (so the name can be truncated to the space
    actually left of them)."""
    symbols = colors.parse_symbols(mana_cost)
    if not symbols:
        return right_edge
    step = PIP_RADIUS * 2 + 8
    start_x = right_edge - len(symbols) * step + PIP_RADIUS + 8
    for i, symbol in enumerate(symbols):
        _draw_pip(draw, start_x + i * step, cy, symbol)
    return start_x - PIP_RADIUS - 8


def render_card_png(card: Card) -> bytes:
    img = Image.new("L", (CARD_W, CARD_H), 255)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([MARGIN, MARGIN, CARD_W - MARGIN, CARD_H - MARGIN], radius=28, outline=0, width=4)

    # Header: name (truncated to whatever space the mana cost pips leave)
    # and the mana cost as circle-and-letter pips -- pips are already
    # letter/number-coded on a real card, so B&W loses no information here.
    draw.rectangle(HEADER_BOX, outline=0, width=2)
    header_cy = (HEADER_BOX[1] + HEADER_BOX[3]) / 2
    pips_start_x = _draw_mana_cost(draw, card.mana_cost, HEADER_BOX[2] - 16, header_cy)
    name = _truncate(card.name, font_name, pips_start_x - HEADER_BOX[0] - 24)
    name_bbox = font_name.getbbox(name)
    draw.text((HEADER_BOX[0] + 16, header_cy - (name_bbox[3] - name_bbox[1]) / 2 - name_bbox[1]), name, font=font_name, fill=0)

    # Art, or the plain placeholder box if there's no art_url, the fetch
    # failed, or it timed out.
    draw.rectangle(ART_BOX, outline=0, width=2)
    box_w, box_h = ART_BOX[2] - ART_BOX[0], ART_BOX[3] - ART_BOX[1]
    art = _fetch_art(card.art_url) if card.art_url else None
    if art:
        img.paste(_fit_cover(art, box_w, box_h), (ART_BOX[0], ART_BOX[1]))
    else:
        _draw_text_centered(draw, ART_BOX, "MOMIR VIBE", font_placeholder)

    # Type line
    draw.rectangle(TYPE_BOX, outline=0, width=2)
    type_line = _truncate(card.type_line, font_type, TYPE_BOX[2] - TYPE_BOX[0] - 32)
    type_bbox = font_type.getbbox(type_line)
    draw.text(
        (TYPE_BOX[0] + 16, (TYPE_BOX[1] + TYPE_BOX[3]) / 2 - (type_bbox[3] - type_bbox[1]) / 2 - type_bbox[1]),
        type_line,
        font=font_type,
        fill=0,
    )

    # Rules text: keyword lines bold, then rules_text lines regular, each
    # word-wrapped to the box width. Inline {N}/{T}/{W}-style symbols are
    # left as literal bracketed text rather than drawn as pips -- standard,
    # unambiguous MTG notation, and far simpler than laying out mixed
    # text-and-glyph runs across a wrap.
    draw.rectangle(RULES_BOX, outline=0, width=2)
    text_max_width = RULES_BOX[2] - RULES_BOX[0] - 32
    y = RULES_BOX[1] + 16
    line_height = 34
    for keyword in card.keywords:
        for line in _wrap(keyword, font_keyword, text_max_width):
            if y + line_height > RULES_BOX[3] - 16:
                break
            draw.text((RULES_BOX[0] + 16, y), line, font=font_keyword, fill=0)
            y += line_height
    for line_text in card.rules_text:
        for line in _wrap(line_text, font_body, text_max_width):
            if y + line_height > RULES_BOX[3] - 16:
                break
            draw.text((RULES_BOX[0] + 16, y), line, font=font_body, fill=0)
            y += line_height
        y += 8  # small gap between rules-text lines, matching the web card's <p> spacing

    # Power/toughness badge, bottom-right corner of the rules box.
    pt_box = (RULES_BOX[2] - PT_BADGE_W, RULES_BOX[3] - PT_BADGE_H // 2, RULES_BOX[2] + 10, RULES_BOX[3] + PT_BADGE_H // 2)
    draw.rectangle(pt_box, outline=0, width=3, fill=255)
    _draw_text_centered(draw, pt_box, f"{card.power}/{card.toughness}", font_pt)

    # Meta line: rarity, set, collector number, artist.
    meta = f"{card.rarity} • {card.set_name} #{card.collector_number} • {card.artist}"
    meta = _truncate(meta, font_meta, CARD_W - 2 * MARGIN - 32)
    _draw_text_centered(draw, (MARGIN, META_Y, CARD_W - MARGIN, META_Y + 24), meta, font_meta)

    buf = io.BytesIO()
    img.convert("1").save(buf, format="PNG")
    return buf.getvalue()
