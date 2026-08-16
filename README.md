# Momir Vibe

A vibe-coded Magic: The Gathering creature card generator, built for Momir-style play. Give it a mana value, get back a randomly-generated, fully-statted creature card that doesn't exist.

## Details

### Card names

Card **names** are generated one of two ways: 

1. matching how real creatures are named: ordinary creatures get species/role names ("Grizzly Bears", "Goblin Piker") assembled from real name words by a word-level Markov chain, so the result is always real English words in a new combination, never invented syllables; and
2. legendary creatures get personal character names ("Jace", "Chandra, Fire Artisan") from a character-level Markov chain, which *is* free to invent new syllables since that's how invented character names are supposed to sound. 

How often a generated card gets a character name mirrors how often real creatures are legendary.

### Rules text

**Rules text** is a mix of real keyword abilities (sampled by how often they appear at that mana value, restricted to keywords we found real printed text for and pruned of one-off card-specific ability
words (see corpus.py's MIN_KEYWORD_OCCURRENCES) and given a real observed value/cost when the keyword takes one, e.g. "Ward" -> "Ward {2}", so a keyword never shows up incomplete). Text is trained *only* on oracle text from real creatures at that same mana value (widening to nearby mana values only if there isn't enough data), so a 1-drop's generated text is never built from words that only ever show up on eight-mana bombs. 

**Sentences** are also kept separate by construct ("shape" -- triggered/activated/static ability) during training and generation, so a generated line can't wander mid-sentence from one construct into another. Line-level templating that only makes sense inside its own card frame (Saga chapters, Class level headers, Case/d20-roll threshold rows, Choose-one's bullet options) is filtered out of training entirely rather than left for the chain to mangle. 

### Other features

**Mana cost, type line, and power/toughness** are likewise sampled from the actual distribution of real creatures at that mana value, so generated cards feel "on curve" even though nothing about them is real.


## Runs locally

The only network access anywhere in this project is the one-time data fetch from the [Scryfall API](https://scryfall.com/docs/api) — the server/generator itself runs fully offline.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# One-time (or occasional) fetch of real creature card data to train on.
# Writes data/cards_cache.json (~6-7 MB), gitignored.
python -m data.fetch_cards
```

## Run the API

```bash
python -m momir.main
```

Serves at `http://127.0.0.1:8000` — open it in a browser for a small card
mockup page (mana value input + Generate button, rendered into a CSS card
frame). Interactive API docs at `/docs`.

### Endpoints

- `GET /` — the card mockup web page (`static/`).
- `GET /cards/generate?mana_value=4` — one generated creature card at that mana value (0-16).
- `GET /cards/decode?code=...` — reconstruct a card from its `share_code` (see "Saving and sharing cards" below).
- `POST /cards/save` — body `{"share_code": "..."}`; persists it server-side and returns `{"id": "..."}`.
- `GET /c/{id}` — the card saved under that short id.
- `GET /health` — liveness check + how many cards are in the training corpus (overall and per format).

Both generation endpoints also take an optional `format` param (`standard`, `pioneer`, or `modern`) to restrict training data to cards legal in that format, so generated cards feel like they belong to that format's card pool rather than Magic's full 30-year history. Omit it for the full, unrestricted pool. (Legacy/Vintage aren't offered as filters because creatures are almost never banned there, so it'd barely narrow the pool at all -- ~99% of all creatures are legal in both) Each format's corpus and Markov chains are trained lazily on first request and cached from then on, same as the unrestricted pool trained eagerly at startup.

Note that a Standard-scoped corpus is only as fresh as the last `data/fetch_cards.py` run -- Standard rotates sets out over time, and this project never re-fetches on its own, so re-run the fetch occasionally if you're using `format=standard`. Modern/Pioneer don't have this problem asbans are rare and don't expire.

Example:

```bash
curl "http://127.0.0.1:8000/cards/generate?mana_value=3"
```

```json
{
  "name":"Treetop Freedom Fighters",
  "mana_cost":"{3}",
  "mana_value":3,
  "colors":[],
  "color_identity":[],
  "type_line":"Creature — Human Druid",
  "power":2,
  "toughness":3,
  "keywords":["Indestructible"],
  "rules_text":["When this creature enters, put a +1/+1 counter on target creature."],
  ...
  }
```

### Saving and sharing cards

Every generated `Card` carries a `share_code` — a compact string (version tag + zlib-compressed, base64url-encoded JSON of the card's fields) that fully encodes the card. `GET /cards/decode?code=...` turns one back into the exact same `Card`, with no regeneration and no dependency on the corpus or Markov chains being unchanged since the card was made (see `momir/codec.py`).

That one primitive backs two things in the web page:

- **Save** — the ★ button keeps a card's `share_code` (plus its already-generated fields, so the list renders without a round trip) in the browser's `localStorage`. Nothing server-side is involved, so this costs nothing to run and never expires.
- **Share** — the "Copy link" button first tries `POST /cards/save`, which persists the `share_code` in a local SQLite DB (`data/saved_cards.db`, gitignored, created automatically) keyed by a short content-addressed id (`sha256(share_code)[:10]`, so saving the same card twice is a no-op), giving a short `?id=...` link. If that request fails for any reason, it falls back to the long but still fully self-contained `?card=<share_code>` link, which needs no server-side state at all.

The SQLite DB is a lookup cache, not a source of truth — every row is just `id -> share_code`, and any `share_code` you still have decodes correctly even if the DB were deleted. There's currently no eviction of old rows.

## Project layout

```
data/fetch_cards.py   one-time Scryfall fetch -> data/cards_cache.json
momir/corpus.py        loads the cache, builds training indices
momir/markov.py        the generic Markov chain implementations
momir/names.py         name generation (common: word-level Markov, character: char-level Markov)
momir/text.py           rules text + keyword generation
momir/colors.py         mana cost synthesis
momir/stats.py          power/toughness sampling
momir/types.py          creature type line generation
momir/card_builder.py   ties it all together into a Card
momir/codec.py           encode/decode a Card to/from its share_code
momir/store.py           SQLite-backed short-id lookup for shared cards
momir/models.py         pydantic Card schema + request/response shapes
momir/api.py            FastAPI app + routes
momir/main.py           uvicorn entrypoint
static/                 card mockup web page (vanilla HTML/CSS/JS, no build step)
```

## Notes / limitations

- Generated rules text is flavorful, not mechanically enforced — a generated "Whenever this attacks, draw a card" won't actually do anything in any digital sense. This is a card *generator*, not a game engine.
- Mana value range is capped at 0-16 (matches the real creature card population closely enough to generate from).
- Re-running `python -m data.fetch_cards` refreshes the corpus with whatever's newest on Scryfall; delete `data/cards_cache.json` first if you want a completely clean pull.
