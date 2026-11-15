// Game Finder page. Search view: query, mode and filters live in the URL; calls /search.
// Find view: posts a half-remembered description to /find and shows the clues it extracted.
const PAGE_SIZE = 20;
const MAX_PAGES = 1000 / PAGE_SIZE; // semantic and hybrid rank at most 1,000 games
const AUTOCOMPLETE_DELAY_MS = 200;

const $ = (id) => document.getElementById(id);
let state = readState();

function readState() {
  const p = new URLSearchParams(location.search);
  return {
    view: p.get("view") === "find" ? "find" : "search",
    q: p.get("q") || "",
    mode: p.get("mode") || "hybrid",
    genres: p.getAll("genres"),
    price_min: p.get("price_min") || "",
    price_max: p.get("price_max") || "",
    min_score: p.get("min_score") || "",
    year_from: p.get("year_from") || "",
    year_to: p.get("year_to") || "",
    free_only: p.get("free_only") === "true",
    sort: p.get("sort") || "relevance",
    page: Number(p.get("page")) || 1,
  };
}

function toParams(s) {
  const p = new URLSearchParams();
  for (const [key, value] of Object.entries(s)) {
    if (key === "genres") value.forEach((g) => p.append("genres", g));
    else if (key === "free_only") { if (value) p.set(key, "true"); }
    else if (key === "view" && value === "search") continue;
    else if (key === "mode" && value === "hybrid") continue;
    else if (key === "sort" && value === "relevance") continue;
    else if (key === "page" && value === 1) continue;
    else if (value !== "") p.set(key, value);
  }
  return p;
}

function update(changes, { resetPage = true } = {}) {
  state = { ...state, ...changes, ...(resetPage ? { page: 1 } : {}) };
  const qs = toParams(state).toString();
  history.pushState(null, "", qs ? `?${qs}` : location.pathname);
  show();
}

function show() {
  const find = state.view === "find";
  $("search-view").hidden = find;
  $("search-bar").hidden = find;
  $("find-view").hidden = !find;
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === state.view));
  if (!find) runSearch();
}

// ---------- Search view ----------

async function runSearch() {
  syncControls();
  const params = toParams(state);
  params.delete("view");
  params.set("size", PAGE_SIZE);
  $("summary").textContent = "Searching…";
  try {
    const data = await getJSON(`/search?${params}`);
    renderSearch(data);
  } catch (err) {
    $("summary").textContent = "";
    $("results").innerHTML = `<li class="error">Search failed: ${esc(err.message)}</li>`;
    $("pagination").innerHTML = "";
  }
}

function renderSearch(data) {
  const n = data.total.toLocaleString();
  const fellBack = state.q && data.mode !== state.mode ? ` (${data.mode} mode: ${state.mode} is unavailable)` : "";
  $("summary").textContent = `${n} result${data.total === 1 ? "" : "s"} in ${data.took_ms} ms${fellBack}`;
  $("results").innerHTML = data.results.length
    ? data.results.map((g) => gameCard(g)).join("")
    : `<li class="empty">No games found. Try fewer filters.</li>`;
  renderGenres(data.facets.genres);
  renderPrices(data.facets.price);
  renderYears(data.facets.years);
  renderPagination(data.total);
}

function renderGenres(buckets) {
  // Keep selected genres visible even if they fall out of the top 20.
  const shown = new Map(buckets.map((b) => [b.key, b.count]));
  state.genres.forEach((g) => { if (!shown.has(g)) shown.set(g, 0); });
  $("genre-facets").innerHTML = [...shown].map(([key, count]) => `
    <label><input type="checkbox" value="${esc(key)}" ${state.genres.includes(key) ? "checked" : ""}>
      ${esc(key)} <span class="count">${count.toLocaleString()}</span></label>`).join("");
}

function renderPrices(buckets) {
  const current = `${state.price_min}|${state.price_max}`;
  const options = [{ key: "Any price", min: null, max: null, count: null }, ...buckets];
  $("price-facets").innerHTML = options.map((b) => {
    const value = `${b.min ?? ""}|${b.max ?? ""}`;
    const count = b.count == null ? "" : b.count.toLocaleString();
    return `<label><input type="radio" name="price" value="${value}" ${value === current ? "checked" : ""}>
      ${esc(b.key)} <span class="count">${count}</span></label>`;
  }).join("");
}

function renderYears(buckets) {
  if (!buckets.length) return;
  $("year-from").placeholder = `From (${buckets[0].key})`;
  $("year-to").placeholder = `To (${buckets[buckets.length - 1].key})`;
}

function renderPagination(total) {
  const limit = state.q && state.mode !== "keyword" ? MAX_PAGES : 10000 / PAGE_SIZE;
  const pages = Math.min(Math.ceil(total / PAGE_SIZE), limit);
  if (pages <= 1) { $("pagination").innerHTML = ""; return; }
  $("pagination").innerHTML = `
    <button type="button" data-page="${state.page - 1}" ${state.page <= 1 ? "disabled" : ""}>Previous</button>
    <span>Page ${state.page} of ${pages.toLocaleString()}</span>
    <button type="button" data-page="${state.page + 1}" ${state.page >= pages ? "disabled" : ""}>Next</button>`;
}

function syncControls() {
  $("q").value = state.q;
  $("sort").value = state.sort;
  $("min-score").value = state.min_score;
  $("year-from").value = state.year_from;
  $("year-to").value = state.year_to;
  $("free-only").checked = state.free_only;
  document.querySelectorAll(".modes button").forEach((b) => b.classList.toggle("active", b.dataset.mode === state.mode));
}

// ---------- Shared result card and "more like this" ----------

function gameCard(g, rank) {
  const price = g.is_free || g.price === 0 ? "Free" : g.price != null ? `$${g.price.toFixed(2)}` : "";
  const score = g.review_score != null
    ? `<span class="score ${g.review_score >= 80 ? "good" : g.review_score >= 50 ? "mixed" : "bad"}">${g.review_score}% positive</span>`
    : "";
  const year = g.release_date ? `<span>${g.release_date.slice(0, 4)}</span>` : "";
  const img = g.header_image ? `<img src="${esc(g.header_image)}" alt="" loading="lazy">` : `<img alt="">`;
  const chips = g.genres.map((x) => `<span class="chip">${esc(x)}</span>`).join("");
  const rankLabel = rank ? `<span class="rank">${rank}.</span>` : "";
  return `<li class="game">
    ${img}
    <div>
      <h3>${rankLabel}${esc(g.name)}</h3>
      <div class="meta"><span class="price">${price}</span>${score}${year}</div>
      <p>${esc(g.short_description || "")}</p>
      <div class="chips">${chips}</div>
      <button type="button" class="link similar-toggle" data-similar="${esc(g.app_id)}">More like this</button>
    </div>
    <div class="similar" id="similar-${esc(g.app_id)}"></div>
  </li>`;
}

async function toggleSimilar(button) {
  const box = button.closest(".game").querySelector(".similar");
  if (box.innerHTML) { box.innerHTML = ""; button.textContent = "More like this"; return; }
  button.textContent = "Loading…";
  try {
    const games = await getJSON(`/games/${encodeURIComponent(button.dataset.similar)}/similar`);
    box.innerHTML = games.length
      ? games.map((g) => `<a href="?${new URLSearchParams({ q: g.name, mode: "keyword" })}">
          ${g.header_image ? `<img src="${esc(g.header_image)}" alt="" loading="lazy">` : `<img alt="">`}
          ${esc(g.name)}</a>`).join("")
      : `<span class="muted">No similar games found.</span>`;
    button.textContent = "Hide similar games";
  } catch (err) {
    box.innerHTML = `<span class="error">${esc(err.message)}</span>`;
    button.textContent = "More like this";
  }
}

// ---------- Autocomplete ----------

let suggestTimer;
let suggestRequest = 0;
let activeSuggestion = -1;

function onQueryInput() {
  clearTimeout(suggestTimer);
  const q = $("q").value.trim();
  if (q.length < 2) { hideSuggestions(); return; }
  suggestTimer = setTimeout(async () => {
    const id = ++suggestRequest;
    try {
      const names = await getJSON(`/autocomplete?${new URLSearchParams({ q })}`);
      if (id !== suggestRequest) return; // a newer keystroke won
      activeSuggestion = -1;
      $("suggestions").innerHTML = names.map((s, i) =>
        `<li role="option" id="suggestion-${i}" data-name="${esc(s.name)}">${esc(s.name)}</li>`).join("");
      $("suggestions").hidden = !names.length;
    } catch { hideSuggestions(); }
  }, AUTOCOMPLETE_DELAY_MS);
}

function hideSuggestions() {
  $("suggestions").hidden = true;
  $("suggestions").innerHTML = "";
  activeSuggestion = -1;
}

function onQueryKeydown(e) {
  const items = [...$("suggestions").children];
  if ($("suggestions").hidden || !items.length) return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    activeSuggestion = (activeSuggestion + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items.forEach((li, i) => li.setAttribute("aria-selected", i === activeSuggestion));
    $("q").value = items[activeSuggestion].dataset.name;
  } else if (e.key === "Escape") {
    hideSuggestions();
  }
}

function pickSuggestion(name) {
  hideSuggestions();
  update({ q: name });
}

// ---------- Find view ----------

async function runFind(e) {
  e.preventDefault();
  const memory = $("memory").value.trim();
  if (memory.length < 10) return;
  const button = $("find-form").querySelector("button");
  button.disabled = true;
  $("find-summary").textContent = $("use-ai").checked ? "Reading your description and searching…" : "Searching…";
  $("clues").innerHTML = "";
  $("find-results").innerHTML = "";
  try {
    const data = await getJSON("/find", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ memory, use_ai: $("use-ai").checked }),
    });
    renderClues(data);
    $("find-results").innerHTML = data.results.length
      ? data.results.map((g, i) => gameCard(g, i + 1)).join("")
      : `<li class="empty">No games found. Try adding a detail you remember.</li>`;
  } catch (err) {
    $("find-summary").textContent = "";
    $("find-results").innerHTML = `<li class="error">${esc(err.message)}</li>`;
  } finally {
    button.disabled = false;
  }
}

function renderClues(data) {
  const c = data.clues;
  const chips = [];
  if (c.year_from || c.year_to) {
    const years = c.year_from === c.year_to || !c.year_to || !c.year_from
      ? `around ${c.year_from || c.year_to}` : `${c.year_from}–${c.year_to}`;
    chips.push(["Year", years]);
  }
  c.genres.forEach((g) => chips.push(["Genre", g]));
  c.keywords.forEach((k) => chips.push(["Clue", k]));
  c.platform_hints.forEach((p) => chips.push(["Platform", p]));
  $("clues").innerHTML = chips.map(([label, value]) =>
    `<span class="clue"><b>${esc(label)}</b> ${esc(value)}</span>`).join("");
  const how = data.used_ai ? "AI-read description" : "your exact words";
  const semantic = data.semantic ? "" : " Semantic search is unavailable, so only keywords were used.";
  $("find-summary").textContent = `Searched for “${data.query}” (${how}) in ${data.took_ms} ms.${semantic}`;
}

// ---------- Helpers and events ----------

async function getJSON(url, options) {
  const resp = await fetch(url, options);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
  return data;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

$("search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  hideSuggestions();
  update({ q: $("q").value.trim() });
});
$("q").addEventListener("input", onQueryInput);
$("q").addEventListener("keydown", onQueryKeydown);
$("q").addEventListener("blur", () => setTimeout(hideSuggestions, 150));
$("suggestions").addEventListener("mousedown", (e) => {
  const li = e.target.closest("li");
  if (li) pickSuggestion(li.dataset.name);
});
$("genre-facets").addEventListener("change", (e) => {
  const g = e.target.value;
  update({ genres: e.target.checked ? [...state.genres, g] : state.genres.filter((x) => x !== g) });
});
$("price-facets").addEventListener("change", (e) => {
  const [price_min, price_max] = e.target.value.split("|");
  update({ price_min, price_max });
});
$("free-only").addEventListener("change", (e) => update({ free_only: e.target.checked }));
$("min-score").addEventListener("change", (e) => update({ min_score: e.target.value }));
$("year-from").addEventListener("change", (e) => update({ year_from: e.target.value }));
$("year-to").addEventListener("change", (e) => update({ year_to: e.target.value }));
$("sort").addEventListener("change", (e) => update({ sort: e.target.value }));
$("clear-filters").addEventListener("click", () => update({
  genres: [], price_min: "", price_max: "", min_score: "", year_from: "", year_to: "", free_only: false,
}));
$("pagination").addEventListener("click", (e) => {
  const page = Number(e.target.dataset.page);
  if (!page) return;
  update({ page }, { resetPage: false });
  window.scrollTo({ top: 0 });
});
document.querySelector(".modes").addEventListener("click", (e) => {
  const mode = e.target.dataset.mode;
  if (mode) update({ mode });
});
document.querySelector(".tabs").addEventListener("click", (e) => {
  const view = e.target.dataset.view;
  if (view && view !== state.view) update({ view }, { resetPage: false });
});
document.addEventListener("click", (e) => {
  const button = e.target.closest("[data-similar]");
  if (button) toggleSimilar(button);
});
$("find-form").addEventListener("submit", runFind);
$("memory").addEventListener("input", () => { $("memory-count").textContent = `${$("memory").value.length} / 1000`; });
window.addEventListener("popstate", () => { state = readState(); show(); });

show();
