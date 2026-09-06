// Fetches a generated card from the local API and renders it into the CSS
// card frame in index.html. Same-origin (this page is served by the same
// FastAPI app -- see momir/api.py's static mount), so no CORS setup needed.

const COLOR_TO_FRAME_CLASS = { W: "white", U: "blue", B: "black", R: "red", G: "green" };

function frameClass(colors) {
  if (!colors || colors.length === 0) return "colorless";
  if (colors.length > 1) return "gold";
  return COLOR_TO_FRAME_CLASS[colors[0]] || "colorless";
}

function pipClass(symbol) {
  if (/^[WUBRG]$/.test(symbol)) return `pip-${symbol.toLowerCase()}`;
  if (/^(\d+|X)$/.test(symbol)) return "pip-generic";
  return "pip-hybrid"; // hybrid (W/U) or Phyrexian (B/P) mana
}

function renderManaCost(manaCost) {
  const symbols = [...manaCost.matchAll(/\{([^}]+)\}/g)].map((m) => m[1]);
  return symbols
    .map((sym) => `<span class="pip ${pipClass(sym)}">${sym}</span>`)
    .join("");
}

const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;" };
function escapeHtml(str) {
  return str.replace(/[&<>]/g, (c) => HTML_ESCAPES[c]);
}

// Renders prose that may contain inline {W}/{2}/{T}-style symbols (keyword
// costs, activated-ability costs) as the same pip spans used in the mana
// cost line, so e.g. "Ward {2}" matches the card's cost styling.
function renderTextWithMana(text) {
  return text
    .split(/(\{[^}]+\})/g)
    .map((part) => {
      const m = part.match(/^\{([^}]+)\}$/);
      return m
        ? `<span class="pip pip-inline ${pipClass(m[1])}">${m[1]}</span>`
        : escapeHtml(part);
    })
    .join("");
}

function renderCard(card) {
  const el = document.getElementById("card");
  el.className = `card ${frameClass(card.colors)}`;

  document.getElementById("card-name").textContent = card.name;
  document.getElementById("card-mana").innerHTML = renderManaCost(card.mana_cost);
  document.getElementById("card-type").textContent = card.type_line;

  // art_url is a real creature's art, picked server-side by color-identity
  // match -- unrelated to this card's name/text, just a plausible-looking
  // picture (see momir/art.py). Falls back to the plain label when the
  // corpus has no art data (see momir/models.py's Card.art_url docstring).
  const artBox = document.getElementById("card-art");
  const artImage = document.getElementById("card-art-image");
  const artLabel = document.getElementById("card-art-label");
  if (card.art_url) {
    // Scryfall art_crop images vary in aspect ratio per card, so the box's
    // ratio is set from the image's actual dimensions once it loads --
    // that keeps object-fit: cover from ever having to crop real content.
    artImage.onload = () => {
      artBox.style.aspectRatio = `${artImage.naturalWidth} / ${artImage.naturalHeight}`;
    };
    artImage.src = card.art_url;
    artImage.hidden = false;
    artLabel.hidden = true;
  } else {
    artImage.hidden = true;
    artImage.removeAttribute("src");
    artImage.onload = null;
    artBox.style.aspectRatio = "";
    artLabel.hidden = false;
  }

  const textBox = document.getElementById("card-text");
  textBox.innerHTML = "";
  for (const keyword of card.keywords) {
    const p = document.createElement("p");
    p.className = "keyword-line";
    p.innerHTML = renderTextWithMana(keyword);
    textBox.appendChild(p);
  }
  for (const line of card.rules_text) {
    const p = document.createElement("p");
    p.innerHTML = renderTextWithMana(line);
    textBox.appendChild(p);
  }

  document.getElementById("card-pt").textContent = `${card.power}/${card.toughness}`;
  document.getElementById(
    "card-meta"
  ).textContent = `${card.rarity} • ${card.set_name} #${card.collector_number} • ${card.artist}`;

  el.hidden = false;
}

// FastAPI's `detail` field isn't a consistent shape: a route that raises
// HTTPException(detail=str(...)) (see api.py's ValueError handling) sends a
// plain string, but FastAPI's own built-in query-param validation (e.g. the
// mana_value ge/le constraints) sends a *list* of {msg, loc, ...} objects
// instead -- passing that straight to `new Error()` stringifies the array
// as "[object Object]" rather than anything readable.
function errorMessage(body, status) {
  const detail = body?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((e) => e.msg || JSON.stringify(e)).join("; ");
  return `Request failed (${status})`;
}

async function generate(manaValue) {
  const errorEl = document.getElementById("error");
  const button = document.getElementById("generate-btn");
  errorEl.hidden = true;
  button.disabled = true;
  try {
    const mayhem = document.getElementById("mayhem").value;
    const res = await fetch(
      `/cards/generate?mana_value=${encodeURIComponent(manaValue)}&mayhem=${encodeURIComponent(mayhem)}`
    );
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(errorMessage(body, res.status));
    }
    renderCard(await res.json());
  } catch (err) {
    errorEl.textContent = err.message || "Something went wrong.";
    errorEl.hidden = false;
  } finally {
    button.disabled = false;
  }
}

document.getElementById("controls").addEventListener("submit", (event) => {
  event.preventDefault();
  const manaValue = document.getElementById("mana-value").value;
  generate(manaValue);
});

generate(document.getElementById("mana-value").value);
