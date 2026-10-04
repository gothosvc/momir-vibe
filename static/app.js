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

// Fills in a card element (the live #card, or a cloned #stack-card-template
// instance) from a Card object -- both share the same inner markup (see
// index.html), scoped by class rather than id since a stack can hold
// several of these at once.
function populateCard(root, card, extraClass = "") {
  const isLegendary = card.type_line.includes("Legendary");
  root.className = ["card", frameClass(card.colors), extraClass, isLegendary ? "legendary" : ""]
    .filter(Boolean)
    .join(" ");

  root.querySelector(".card-name").textContent = card.name;
  root.querySelector(".card-mana").innerHTML = renderManaCost(card.mana_cost);
  root.querySelector(".card-type").textContent = card.type_line;

  // art_url is a real creature's art, picked server-side by color-identity
  // match -- unrelated to this card's name/text, just a plausible-looking
  // picture (see momir/art.py). Falls back to the plain label when the
  // corpus has no art data (see momir/models.py's Card.art_url docstring).
  const artBox = root.querySelector(".card-art");
  const artImage = root.querySelector(".card-art-image");
  const artLabel = root.querySelector(".card-art-label");
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

  const textBox = root.querySelector(".card-text");
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

  root.querySelector(".card-pt").textContent = `${card.power}/${card.toughness}`;
  root.querySelector(".card-meta").innerHTML =
    `${escapeHtml(card.set_name)} #${escapeHtml(card.collector_number)} • ${escapeHtml(card.artist)}`;
}

// Max tilt in degrees per mayhem level -- wilder settings land messier.
const TILT = { off: 3, text: 4, full: 6, unhinged: 9 };

function renderCard(card, { animateSettle = false } = {}) {
  const el = document.getElementById("card");
  populateCard(el, card);
  // Every card lands at a slight random tilt, like it was just slapped
  // onto the table -- animateSettle (only set by a fresh Generate or a
  // promoted stack card, see generate()/promoteFromStack() below)
  // additionally plays the settle-bounce animation.
  const tilt = TILT[card._genParams?.mayhem] ?? TILT.off;
  el.style.setProperty("--rot", `${((Math.random() * 2 - 1) * tilt).toFixed(1)}deg`);
  if (animateSettle) el.classList.add("settling");
}

// Cards generated earlier this session, most-recent-first, rendered as a
// fanned stack behind the live card (see style.css's .card-stack) -- purely
// in-memory, gone on reload, same ephemeral spirit as not persisting a card
// server-side at all. Capped to what the stack's CSS actually keeps visible
// (see .stack-card:nth-last-child), so nothing accumulates unseen.
const MAX_HISTORY = 4;
let history = [];
let currentCard = null;

function renderStack() {
  const stackEl = document.getElementById("card-stack");
  const template = document.getElementById("stack-card-template");
  stackEl.innerHTML = "";
  // Appended oldest-first, so the most recently displaced card ends up last
  // in the DOM -- .stack-card:nth-last-child(1) (closest to the live card)
  // always matches the most recent one, regardless of how many are stacked.
  for (let i = history.length - 1; i >= 0; i--) {
    const card = history[i];
    const node = template.content.firstElementChild.cloneNode(true);
    populateCard(node, card, "stack-card");
    node.setAttribute("role", "button");
    node.tabIndex = 0;
    node.setAttribute("aria-label", `Bring ${card.name} back to the front`);
    const promote = () => promoteFromStack(i);
    node.addEventListener("click", promote);
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        promote();
      }
    });
    stackEl.appendChild(node);
  }
}

// Makes `card` (freshly generated, or promoted from the stack) the live
// card, pushing whatever was live before it onto the stack in its place.
function showCard(card, { animateSettle = false } = {}) {
  if (currentCard) {
    history.unshift(currentCard);
    history = history.slice(0, MAX_HISTORY);
  }
  currentCard = card;
  renderCard(card, { animateSettle });
  renderStack();
  document.getElementById("card-scene").style.setProperty("--depth", history.length);
  document.getElementById("printable-btn").disabled = false;
}

function promoteFromStack(index) {
  const [card] = history.splice(index, 1);
  showCard(card, { animateSettle: true });
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

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Fed to style.css as --storm-ms: keeping the storm on screen for at least
// this long even when the request comes back instantly is what makes it
// read as a moment rather than a glitch.
const STORM_MS = { off: 460, text: 460, full: 540, unhinged: 760 };
const MAYHEM_AMP = { off: 1, text: 1.1, full: 1.25, unhinged: 1.6 };
const MAX_MANA_VALUE = 16;
const QUAKE_MIN_MANA_VALUE = 10;
const FACE_DOWN_MIN_MS = 600;
const SLOW_MS = 1200;

let lastManaValue = null;

async function generate(manaValue) {
  const errorEl = document.getElementById("error");
  const button = document.getElementById("generate-btn");
  const cardEl = document.getElementById("card");
  const chipsEl = document.getElementById("mv-chips");
  const stackEl = document.getElementById("card-stack");
  const sceneEl = document.getElementById("card-scene");
  const statusEl = document.getElementById("status");
  if (button.disabled) return;
  lastManaValue = manaValue;
  errorEl.hidden = true;
  // Locked for the duration of the request -- a chip click or stack-card
  // promote mid-fetch would race the response that's already in flight and
  // land in an inconsistent state (which card is "current" first?).
  button.disabled = true;
  chipsEl.classList.add("busy");
  stackEl.classList.add("busy");

  const { params, format, mayhem } = buildParams(manaValue);
  const amp = (0.4 + 1.2 * (Number(manaValue) / MAX_MANA_VALUE)) * MAYHEM_AMP[mayhem];
  cardEl.style.setProperty("--amp", amp.toFixed(2));
  cardEl.style.setProperty("--storm-ms", `${STORM_MS[mayhem]}ms`);

  // The first card of the session flips over from a face-down back; every
  // later one storms in place of the card already on the table.
  const faceDown = cardEl.classList.contains("face-down");
  if (faceDown) {
    cardEl.querySelector(".card-back-caption").textContent = "Shuffling the library…";
  } else {
    cardEl.classList.remove("settling");
    cardEl.classList.toggle("unhinged", mayhem === "unhinged");
    cardEl.classList.add("storming");
  }
  const slowTimer = faceDown ? null : setTimeout(() => (statusEl.textContent = "Shuffling the library…"), SLOW_MS);

  try {
    const fetchPromise = fetch(`/cards/generate?${params}`);
    const [res] = await Promise.all([fetchPromise, sleep(faceDown ? FACE_DOWN_MIN_MS : STORM_MS[mayhem])]);
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(errorMessage(body, res.status));
    }
    const card = await res.json();
    // The seed the server used for this card -- stashed so "Printable
    // image" can later request this *exact* card as an image instead of
    // yet another random one (see /cards/generate/image's seed param).
    card._genParams = { mana_value: manaValue, format, mayhem, seed: res.headers.get("X-Momir-Seed") };
    if (faceDown) {
      cardEl.classList.add("flip-out");
      await sleep(180);
      showCard(card);
      cardEl.classList.add("flip-in");
    } else {
      cardEl.classList.remove("storming");
      showCard(card, { animateSettle: true });
      if (Number(manaValue) >= QUAKE_MIN_MANA_VALUE) {
        sceneEl.classList.remove("quake");
        void sceneEl.offsetWidth;
        sceneEl.classList.add("quake");
      }
    }
  } catch (err) {
    cardEl.classList.remove("storming");
    if (faceDown) cardEl.querySelector(".card-back-caption").textContent = "The spell fizzled.";
    document.getElementById("error-text").textContent = `The spell fizzled: ${err.message || "something went wrong."}`;
    errorEl.hidden = false;
  } finally {
    clearTimeout(slowTimer);
    statusEl.textContent = "";
    button.disabled = false;
    chipsEl.classList.remove("busy");
    stackEl.classList.remove("busy");
  }
}

function selectedManaValue() {
  return document.querySelector(".mv-chip.selected")?.dataset.value ?? "3";
}

function buildParams(manaValue) {
  const mayhem = document.getElementById("mayhem").value;
  const format = document.getElementById("format").value;
  const params = new URLSearchParams({ mana_value: manaValue, mayhem });
  if (format) params.set("format", format);
  return { params, format, mayhem };
}

function selectManaValue(value) {
  for (const chip of document.querySelectorAll(".mv-chip")) {
    const selected = chip.dataset.value === value;
    chip.classList.toggle("selected", selected);
    chip.setAttribute("aria-pressed", String(selected));
    if (selected) chip.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  document.getElementById("cost-pip").textContent = value;
  document.getElementById("generate-btn").setAttribute("aria-label", `Activate with X = ${value}`);
}

function pickManaValue(value) {
  if (document.getElementById("generate-btn").disabled) return;
  selectManaValue(value);
  generate(value);
}

// Picking a mana value reveals a card at it immediately -- picking X and
// seeing what it does is one motion at the table, not two.
document.getElementById("mv-chips").addEventListener("click", (event) => {
  const chip = event.target.closest(".mv-chip");
  if (!chip) return;
  pickManaValue(chip.dataset.value);
});

// The Generate button rerolls at whichever mana value is currently selected.
document.getElementById("controls").addEventListener("submit", (event) => {
  event.preventDefault();
  generate(selectedManaValue());
});

// Reprints the card currently on screen: replays its own stashed seed (see
// generate() above) rather than generating a new random one, so this is
// the *same* card, not just another one at the same mana value. Nothing is
// persisted server-side to make this work -- the seed is the only state,
// held here in the tab, same as the rest of the session.
document.getElementById("printable-btn").addEventListener("click", () => {
  if (!currentCard?._genParams) return;
  const { mana_value, format, mayhem, seed } = currentCard._genParams;
  const params = new URLSearchParams({ mana_value, mayhem });
  if (format) params.set("format", format);
  if (seed) params.set("seed", seed);
  window.open(`/cards/generate/image?${params}`, "_blank");
});

document.getElementById("retry-btn").addEventListener("click", () => {
  if (lastManaValue !== null) generate(lastManaValue);
});

const MAYHEM_HINTS = {
  off: "",
  text: "Rules text is borrowed from creatures of any mana value.",
  full: "Cost, stats, types and text are borrowed from any mana value, favoring ones near X.",
  unhinged: "Anything from any mana value, with no pull toward X, and always some rules text.",
};
document.getElementById("mayhem").addEventListener("change", (event) => {
  const hint = document.getElementById("mayhem-hint");
  hint.textContent = MAYHEM_HINTS[event.target.value];
  hint.hidden = !hint.textContent;
});

// Keyboard play: digits pick X (a leading 1 waits briefly for a second
// digit, for 10-16), arrows step X, Space/R rerolls, P prints.
const DIGIT_WAIT_MS = 450;
let pendingDigit = "";
let digitTimer = null;

document.addEventListener("keydown", (event) => {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.target.closest("select, input, textarea")) return;
  const key = event.key;

  if (/^\d$/.test(key)) {
    clearTimeout(digitTimer);
    const combined = pendingDigit + key;
    pendingDigit = "";
    if (combined.length === 2 && Number(combined) <= MAX_MANA_VALUE) {
      pickManaValue(combined);
    } else if (key === "1") {
      pendingDigit = "1";
      digitTimer = setTimeout(() => {
        pendingDigit = "";
        pickManaValue("1");
      }, DIGIT_WAIT_MS);
    } else {
      pickManaValue(key);
    }
  } else if (key === "ArrowLeft" || key === "ArrowRight") {
    event.preventDefault();
    const step = key === "ArrowLeft" ? -1 : 1;
    const next = Math.min(MAX_MANA_VALUE, Math.max(0, Number(selectedManaValue()) + step));
    if (String(next) !== selectedManaValue()) pickManaValue(String(next));
  } else if (key === "r" || key === "R" || (key === " " && !event.target.closest("button, [role=button]"))) {
    event.preventDefault();
    generate(selectedManaValue());
  } else if (key === "p" || key === "P") {
    document.getElementById("printable-btn").click();
  }
});

generate(selectedManaValue());
