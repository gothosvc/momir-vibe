# Momir Vibe

A fake Magic: The Gathering creature card generator, built for Momir-style
play: give it a mana value, get back a fully statted creature card that
doesn't exist.

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

Serves at `http://127.0.0.1:8000`. Interactive docs at `/docs`.

### Endpoints

- `GET /cards/generate?mana_value=4` — one generated creature card at that mana value (0-16).
- `GET /momir/match?mana_value=4` — two cards generated off the same mana value, one per player.
- `GET /health` — liveness check + how many cards are in the training corpus (overall and per format).

Both generation endpoints also take an optional `format` param --
`standard`, `pioneer`, or `modern` -- to restrict training data to cards
legal in that format, so generated cards feel like they belong to that
format's card pool rather than Magic's full 30-year history. Omit it for the
full, unrestricted pool. (Legacy/Vintage aren't offered as filters: creatures
are almost never banned there, so it'd barely narrow the pool at all --
~99% of all creatures are legal in both.) Each format's corpus and Markov
chains are trained lazily on first request and cached from then on, same as
the unrestricted pool trained eagerly at startup.

Note that a Standard-scoped corpus is only as fresh as the last
`data/fetch_cards.py` run -- Standard rotates sets out over time, and this
project never re-fetches on its own, so re-run the fetch occasionally if
you're using `format=standard`. Modern/Pioneer don't have this problem
(bans are rare and don't expire).

Example:

```bash
curl "http://127.0.0.1:8000/cards/generate?mana_value=3"
```

```json
{
  "name": "Bird Avenger",
  "mana_cost": "{2}{W}",
  "mana_value": 3,
  "colors": ["W"],
  "type_line": "Creature — Bird",
  "power": 3,
  "toughness": 2,
  "keywords": [],
  "rules_text": ["This creature enters tapped."],
  "rarity": "common",
  "set_name": "Momir Vibe",
  "collector_number": "4",
  ...
}
```

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
momir/models.py         pydantic Card / MatchPair schemas
momir/api.py            FastAPI app + routes
momir/main.py           uvicorn entrypoint
```

## Notes / limitations

- Generated rules text is flavorful, not mechanically enforced — a generated "Whenever this attacks, draw a card" won't actually do anything in any digital sense. This is a card *generator*, not a game engine.
- Mana value range is capped at 0-16 (matches the real creature card population closely enough to generate from).
- Re-running `python -m data.fetch_cards` refreshes the corpus with whatever's newest on Scryfall; delete `data/cards_cache.json` first if you want a completely clean pull.
