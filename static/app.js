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

// The currently-displayed card, kept around so the save/share buttons don't
// need to re-fetch or re-derive anything -- see renderCard().
let currentCard = null;

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

  currentCard = card;
  document.getElementById("card-actions").hidden = false;
  updateSaveButton();
}

// --- Save (localStorage) / share (URL) -------------------------------
//
// A generated Card is fully self-contained -- its share_code (see
// momir/codec.py) encodes everything needed to redraw it, no server
// lookup required. So "saving" a card users care about is just keeping
// that code (plus the card itself, so the list can render without a
// round trip) in localStorage; "sharing" it is putting the same code in
// a URL that GET /cards/decode can turn back into the exact same card.

const SAVE_KEY = "momir-saved-cards";

function loadSavedCards() {
  try {
    return JSON.parse(localStorage.getItem(SAVE_KEY)) || [];
  } catch {
    return [];
  }
}

function writeSavedCards(cards) {
  localStorage.setItem(SAVE_KEY, JSON.stringify(cards));
  renderSavedList();
}

function isSaved(shareCode) {
  return loadSavedCards().some((c) => c.share_code === shareCode);
}

function updateSaveButton() {
  const btn = document.getElementById("save-btn");
  const saved = currentCard && isSaved(currentCard.share_code);
  btn.textContent = saved ? "★ Saved" : "☆ Save";
  btn.classList.toggle("saved", !!saved);
}

function toggleSaveCurrentCard() {
  if (!currentCard) return;
  const saved = loadSavedCards();
  const already = saved.findIndex((c) => c.share_code === currentCard.share_code);
  if (already === -1) {
    saved.unshift(currentCard);
  } else {
    saved.splice(already, 1);
  }
  writeSavedCards(saved);
  updateSaveButton();
}

function renderSavedList() {
  const saved = loadSavedCards();
  const section = document.getElementById("saved-section");
  const list = document.getElementById("saved-list");
  section.hidden = saved.length === 0;
  list.innerHTML = "";

  for (const card of saved) {
    const li = document.createElement("li");

    const openBtn = document.createElement("button");
    openBtn.type = "button";
    openBtn.className = "saved-item";
    openBtn.textContent = `${card.name} (MV ${card.mana_value})`;
    openBtn.addEventListener("click", () => {
      renderCard(card);
      setUrlCode(card.share_code);
    });

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "saved-item-remove";
    removeBtn.textContent = "✕";
    removeBtn.setAttribute("aria-label", `Remove ${card.name} from saved cards`);
    removeBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      writeSavedCards(loadSavedCards().filter((c) => c.share_code !== card.share_code));
      updateSaveButton();
    });

    li.append(openBtn, removeBtn);
    list.appendChild(li);
  }
}

function setUrlCode(shareCode) {
  const url = new URL(location.href);
  url.searchParams.delete("id");
  if (shareCode) {
    url.searchParams.set("card", shareCode);
  } else {
    url.searchParams.delete("card");
  }
  history.replaceState(null, "", url);
}

function setUrlId(cardId) {
  const url = new URL(location.href);
  url.searchParams.delete("card");
  url.searchParams.set("id", cardId);
  history.replaceState(null, "", url);
}

function flashStatus(message) {
  const el = document.getElementById("status");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(flashStatus._timer);
  flashStatus._timer = setTimeout(() => {
    el.hidden = true;
  }, 2000);
}

async function copyShareLink() {
  if (!currentCard) return;

  // Prefer a short /c/<id> link (POST /cards/save persists the share_code
  // server-side under a short id -- see momir/store.py); if that request
  // fails for any reason, fall back to the long but fully self-contained
  // ?card=<share_code> link, which needs no server-side state at all.
  try {
    const res = await fetch("/cards/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ share_code: currentCard.share_code }),
    });
    if (!res.ok) throw new Error("save failed");
    const { id } = await res.json();
    setUrlId(id);
  } catch {
    setUrlCode(currentCard.share_code);
  }

  const link = location.href;
  try {
    await navigator.clipboard.writeText(link);
    flashStatus("Link copied to clipboard.");
  } catch {
    // Clipboard access can be denied (permissions, non-HTTPS context,
    // etc.) -- fall back to just showing the link so it's still usable.
    flashStatus(link);
  }
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
    // A freshly-rolled card wasn't asked for by URL, so don't leave a
    // stale ?card= in the address bar pointing at a different card.
    setUrlCode(null);
  } catch (err) {
    errorEl.textContent = err.message || "Something went wrong.";
    errorEl.hidden = false;
  } finally {
    button.disabled = false;
  }
}

// Shared logic for the two "load a specific card" entry points below: hit
// the given URL, render what comes back, or fall back to generating a
// fresh card if the link turns out to be bad (expired short id, corrupt
// share_code, etc).
async function loadFrom(url, invalidMessage) {
  const errorEl = document.getElementById("error");
  errorEl.hidden = true;
  try {
    const res = await fetch(url);
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(errorMessage(body, res.status));
    }
    renderCard(await res.json());
  } catch (err) {
    errorEl.textContent = err.message || invalidMessage;
    errorEl.hidden = false;
    generate(document.getElementById("mana-value").value);
  }
}

// Reconstruct a card from a share_code, e.g. from a ?card= link -- GET
// /cards/decode does the actual decode (see momir/codec.py), purely
// client-side data, no server-stored state involved.
function loadFromCode(shareCode) {
  return loadFrom(`/cards/decode?code=${encodeURIComponent(shareCode)}`, "That card link looks invalid.");
}

// Reconstruct a card from a short id, e.g. from a /cards/save-produced
// ?id= link -- GET /c/<id> looks the share_code up server-side (see
// momir/store.py) and decodes it the same way.
function loadFromId(cardId) {
  return loadFrom(`/c/${encodeURIComponent(cardId)}`, "That card link has expired or doesn't exist.");
}

document.getElementById("controls").addEventListener("submit", (event) => {
  event.preventDefault();
  const manaValue = document.getElementById("mana-value").value;
  generate(manaValue);
});

document.getElementById("save-btn").addEventListener("click", toggleSaveCurrentCard);
document.getElementById("share-btn").addEventListener("click", copyShareLink);

renderSavedList();

// On load, a ?id=<short id> or ?card=<share_code> link (both produced by
// Copy link, or a saved-card click) reconstructs that exact card;
// otherwise generate a fresh one so the page isn't blank.
const urlParams = new URL(location.href).searchParams;
const urlCardId = urlParams.get("id");
const urlShareCode = urlParams.get("card");
if (urlCardId) {
  loadFromId(urlCardId);
} else if (urlShareCode) {
  loadFromCode(urlShareCode);
} else {
  generate(document.getElementById("mana-value").value);
}
