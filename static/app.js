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

function renderCard(card) {
  const el = document.getElementById("card");
  el.className = `card ${frameClass(card.colors)}`;

  document.getElementById("card-name").textContent = card.name;
  document.getElementById("card-mana").innerHTML = renderManaCost(card.mana_cost);
  document.getElementById("card-type").textContent = card.type_line;

  const textBox = document.getElementById("card-text");
  textBox.innerHTML = "";
  for (const keyword of card.keywords) {
    const p = document.createElement("p");
    p.className = "keyword-line";
    p.textContent = keyword;
    textBox.appendChild(p);
  }
  for (const line of card.rules_text) {
    const p = document.createElement("p");
    p.textContent = line;
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
    const res = await fetch(`/cards/generate?mana_value=${encodeURIComponent(manaValue)}`);
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

// Generate one card immediately so the page isn't blank on load.
generate(document.getElementById("mana-value").value);
