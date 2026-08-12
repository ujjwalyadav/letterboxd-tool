import {
  $, $$, initShell, loadCatalog, summaryStats, renderKpis, createFilmCard,
  bindCardOpen, filmSearchText, formatNumber, countryName, languageName,
  exportFilmsCsv, openFilmDialog, showToast
} from "./core.js";

const state = {
  q: "",
  collection: "all",
  yearFrom: "",
  yearTo: "",
  runtimeMin: "",
  runtimeMax: "",
  ratingMin: "",
  ratingMax: "",
  tmdbRatingMin: "",
  tmdbVotesMin: "",
  lbRatingMin: "",
  lbWatchesMin: "",
  rewatch: "all",
  addedAfter: "",
  watchedAfter: "",
  sort: "title-asc",
  view: localStorage.getItem("film-atlas-view") || "grid",
  facets: {
    genres: new Set(), countries: new Set(), languages: new Set(), directors: new Set(), writers: new Set(),
    cast: new Set(), companies: new Set(), certifications: new Set(), keywords: new Set(), tags: new Set(), providers: new Set(),
  },
};

let data;
let allFilms = [];
let filtered = [];
let visibleCount = 48;
let filmMap = new Map();
let config;

const facetLabels = {
  genres: "Genre", countries: "Country", languages: "Language", directors: "Director", writers: "Writer",
  cast: "Cast", companies: "Company", certifications: "Certification", keywords: "Keyword", tags: "Your tag", providers: "Provider",
};

function parseNumber(v) { return v === "" ? null : Number(v); }

function listNames(items) { return items?.map(x => x.name) || []; }
function providerNames(t) {
  const p = t?.watch_providers || {};
  return [...new Set([...(p.flatrate || []), ...(p.free || []), ...(p.ads || []), ...(p.rent || []), ...(p.buy || [])])];
}

function intersects(selected, values) {
  if (!selected.size) return true;
  const set = new Set(values || []);
  return [...selected].some(v => set.has(v));
}

function passes(film) {
  const u = film.user || {}, t = film.tmdb || {};
  if (state.collection === "watched" && !u.watched) return false;
  if (state.collection === "watchlist" && !u.watchlist) return false;
  if (state.collection === "diary" && !(u.diary_entries || []).length) return false;

  if (state.q && !film._search.includes(state.q.toLocaleLowerCase())) return false;

  const year = Number(film.year || String(t.release_date || "").slice(0, 4) || 0);
  const yf = parseNumber(state.yearFrom), yt = parseNumber(state.yearTo);
  if (yf != null && (!year || year < yf)) return false;
  if (yt != null && (!year || year > yt)) return false;

  const runtime = Number(t.runtime || 0);
  const rmin = parseNumber(state.runtimeMin), rmax = parseNumber(state.runtimeMax);
  if (rmin != null && (!runtime || runtime < rmin)) return false;
  if (rmax != null && (!runtime || runtime > rmax)) return false;

  const rating = u.rating == null ? null : Number(u.rating);
  const pmin = parseNumber(state.ratingMin), pmax = parseNumber(state.ratingMax);
  if (pmin != null && (rating == null || rating < pmin)) return false;
  if (pmax != null && (rating == null || rating > pmax)) return false;

  const trmin = parseNumber(state.tmdbRatingMin);
  if (trmin != null && (!t.vote_average || Number(t.vote_average) < trmin)) return false;
  const tvmin = parseNumber(state.tmdbVotesMin);
  if (tvmin != null && (!t.vote_count || Number(t.vote_count) < tvmin)) return false;
  const lb = film.letterboxd_community || {};
  const lbRatingMin = parseNumber(state.lbRatingMin);
  if (lbRatingMin != null && (lb.average_rating == null || Number(lb.average_rating) < lbRatingMin)) return false;
  const lbWatchesMin = parseNumber(state.lbWatchesMin);
  if (lbWatchesMin != null && (lb.watches == null || Number(lb.watches) < lbWatchesMin)) return false;

  if (state.rewatch === "yes" && !(u.rewatch_count > 0)) return false;
  if (state.rewatch === "no" && (!u.watched || u.rewatch_count > 0)) return false;
  if (state.addedAfter && (!u.watchlist_added_date || u.watchlist_added_date < state.addedAfter)) return false;
  if (state.watchedAfter && (!u.last_watched || u.last_watched < state.watchedAfter)) return false;

  if (!intersects(state.facets.genres, t.genres || [])) return false;
  if (!intersects(state.facets.countries, (t.production_countries || []).map(x => x.code))) return false;
  if (!intersects(state.facets.languages, [...(t.spoken_languages || []).map(x => x.code), t.original_language].filter(Boolean))) return false;
  if (!intersects(state.facets.directors, listNames(t.directors))) return false;
  if (!intersects(state.facets.writers, listNames(t.writers))) return false;
  if (!intersects(state.facets.cast, listNames(t.cast))) return false;
  if (!intersects(state.facets.companies, (t.production_companies || []).map(x => x.name))) return false;
  if (!intersects(state.facets.certifications, [t.certification].filter(Boolean))) return false;
  if (!intersects(state.facets.keywords, t.keywords || [])) return false;
  if (!intersects(state.facets.tags, u.tags || [])) return false;
  if (!intersects(state.facets.providers, providerNames(t))) return false;
  return true;
}

function sortFilms(films) {
  const copy = [...films];
  const cmpText = (a, b) => String(a || "").localeCompare(String(b || ""), undefined, { sensitivity: "base" });
  const dateVal = x => x ? Date.parse(x) || 0 : 0;
  const sorters = {
    "title-asc": (a,b) => cmpText(a.name,b.name),
    "title-desc": (a,b) => cmpText(b.name,a.name),
    "year-desc": (a,b) => (b.year || 0) - (a.year || 0),
    "year-asc": (a,b) => (a.year || 9999) - (b.year || 9999),
    "rating-desc": (a,b) => (b.user?.rating ?? -1) - (a.user?.rating ?? -1),
    "tmdb-desc": (a,b) => (b.tmdb?.vote_average ?? -1) - (a.tmdb?.vote_average ?? -1),
    "votes-desc": (a,b) => (b.tmdb?.vote_count ?? -1) - (a.tmdb?.vote_count ?? -1),
    "popularity-desc": (a,b) => (b.tmdb?.popularity ?? -1) - (a.tmdb?.popularity ?? -1),
    "runtime-asc": (a,b) => (a.tmdb?.runtime ?? 99999) - (b.tmdb?.runtime ?? 99999),
    "runtime-desc": (a,b) => (b.tmdb?.runtime ?? -1) - (a.tmdb?.runtime ?? -1),
    "last-watched-desc": (a,b) => dateVal(b.user?.last_watched) - dateVal(a.user?.last_watched),
    "watchlist-oldest": (a,b) => dateVal(a.user?.watchlist_added_date) - dateVal(b.user?.watchlist_added_date),
  };
  return copy.sort(sorters[state.sort] || sorters["title-asc"]);
}

function update() {
  filtered = sortFilms(allFilms.filter(passes));
  visibleCount = Math.max(config.itemsPerPage || 48, Math.min(visibleCount, filtered.length));
  renderCatalog();
  renderActiveFilters();
  $("#filtered-count").textContent = formatNumber(filtered.length);
  $("#filtered-context").textContent = filtered.length === allFilms.length ? "of your full library" : `of ${formatNumber(allFilms.length)} films`;
}

function renderCatalog() {
  const container = $("#movie-results");
  container.className = state.view === "list" ? "movie-list" : "movie-grid";
  if (!filtered.length) {
    container.innerHTML = `<div class="empty-state" style="grid-column:1/-1"><h3>No films match this combination.</h3><p>Try removing a filter or lowering a threshold.</p><button class="button" id="empty-reset">Reset filters</button></div>`;
    $("#empty-reset")?.addEventListener("click", resetFilters);
    $("#load-more-wrap").style.display = "none";
    return;
  }
  container.innerHTML = filtered.slice(0, visibleCount).map(f => createFilmCard(f, state.view)).join("");
  $("#load-more-wrap").style.display = visibleCount < filtered.length ? "flex" : "none";
}

function displayFacetValue(facet, value) {
  if (facet === "countries") return countryName(value);
  if (facet === "languages") return languageName(value);
  return value;
}

function renderFacet(facet, query = "") {
  const panel = $(`[data-facet-panel="${facet}"]`);
  if (!panel) return;
  const q = query.toLocaleLowerCase();
  const source = data.facets?.[facet] || [];
  const selected = state.facets[facet];
  let rows = source.filter(x => String(displayFacetValue(facet, x.value)).toLocaleLowerCase().includes(q));
  const selectedRows = rows.filter(x => selected.has(x.value));
  const rest = rows.filter(x => !selected.has(x.value));
  rows = [...selectedRows, ...rest].slice(0, q ? 80 : 36);
  panel.innerHTML = rows.map(x => `<button class="filter-chip ${selected.has(x.value) ? "selected" : ""}" data-facet="${facet}" data-value="${encodeURIComponent(x.value)}" title="${x.count} films">${displayFacetValue(facet, x.value)} <span style="opacity:.65">${x.count}</span></button>`).join("") || `<div class="filter-summary">No matches</div>`;
}

function renderAllFacets() { Object.keys(state.facets).forEach(f => renderFacet(f)); }

function renderActiveFilters() {
  const root = $("#active-filters");
  const chips = [];
  Object.entries(state.facets).forEach(([facet, set]) => {
    [...set].forEach(value => chips.push(`<span class="active-filter">${facetLabels[facet]}: ${displayFacetValue(facet, value)}<button data-remove-facet="${facet}" data-value="${encodeURIComponent(value)}" aria-label="Remove">×</button></span>`));
  });
  const scalarLabels = [
    ["q", "Search"], ["yearFrom", "Year ≥"], ["yearTo", "Year ≤"], ["runtimeMin", "Runtime ≥"], ["runtimeMax", "Runtime ≤"],
    ["ratingMin", "Your rating ≥"], ["ratingMax", "Your rating ≤"], ["tmdbRatingMin", "TMDB ≥"], ["tmdbVotesMin", "TMDB votes ≥"],
    ["lbRatingMin", "Letterboxd community ≥"], ["lbWatchesMin", "Letterboxd watches ≥"],
    ["addedAfter", "Watchlist added after"], ["watchedAfter", "Watched after"],
  ];
  scalarLabels.forEach(([key, label]) => { if (state[key]) chips.push(`<span class="active-filter">${label}: ${state[key]}<button data-clear-key="${key}" aria-label="Remove">×</button></span>`); });
  if (state.collection !== "all") chips.push(`<span class="active-filter">Collection: ${state.collection}<button data-clear-key="collection" aria-label="Remove">×</button></span>`);
  if (state.rewatch !== "all") chips.push(`<span class="active-filter">Rewatch: ${state.rewatch}<button data-clear-key="rewatch" aria-label="Remove">×</button></span>`);
  root.innerHTML = chips.join("");
}

function resetFilters() {
  state.q = ""; state.collection = "all"; state.yearFrom = ""; state.yearTo = ""; state.runtimeMin = ""; state.runtimeMax = "";
  state.ratingMin = ""; state.ratingMax = ""; state.tmdbRatingMin = ""; state.tmdbVotesMin = ""; state.lbRatingMin = ""; state.lbWatchesMin = ""; state.rewatch = "all";
  state.addedAfter = ""; state.watchedAfter = "";
  Object.values(state.facets).forEach(s => s.clear());
  syncControls(); renderAllFacets(); visibleCount = config.itemsPerPage || 48; update();
}

function syncControls() {
  const map = {
    "search": "q", "collection": "collection", "year-from": "yearFrom", "year-to": "yearTo", "runtime-min": "runtimeMin", "runtime-max": "runtimeMax",
    "rating-min": "ratingMin", "rating-max": "ratingMax", "tmdb-rating-min": "tmdbRatingMin", "tmdb-votes-min": "tmdbVotesMin", "lb-rating-min": "lbRatingMin", "lb-watches-min": "lbWatchesMin",
    "rewatch": "rewatch", "added-after": "addedAfter", "watched-after": "watchedAfter", "sort": "sort",
  };
  Object.entries(map).forEach(([id, key]) => { const el = $(`#${id}`); if (el) el.value = state[key]; });
  $("#grid-view")?.classList.toggle("primary", state.view === "grid");
  $("#list-view")?.classList.toggle("primary", state.view === "list");
}

function bindControls() {
  const bindings = {
    "search": "q", "collection": "collection", "year-from": "yearFrom", "year-to": "yearTo", "runtime-min": "runtimeMin", "runtime-max": "runtimeMax",
    "rating-min": "ratingMin", "rating-max": "ratingMax", "tmdb-rating-min": "tmdbRatingMin", "tmdb-votes-min": "tmdbVotesMin", "lb-rating-min": "lbRatingMin", "lb-watches-min": "lbWatchesMin",
    "rewatch": "rewatch", "added-after": "addedAfter", "watched-after": "watchedAfter", "sort": "sort",
  };
  Object.entries(bindings).forEach(([id, key]) => {
    const el = $(`#${id}`); if (!el) return;
    const evt = id === "search" ? "input" : "change";
    el.addEventListener(evt, () => { state[key] = el.value.trim(); visibleCount = config.itemsPerPage || 48; update(); });
  });

  $("#facet-root").addEventListener("click", e => {
    const btn = e.target.closest("[data-facet][data-value]");
    if (!btn) return;
    const facet = btn.dataset.facet, value = decodeURIComponent(btn.dataset.value);
    const set = state.facets[facet];
    set.has(value) ? set.delete(value) : set.add(value);
    renderFacet(facet, $(`[data-facet-search="${facet}"]`)?.value || "");
    visibleCount = config.itemsPerPage || 48; update();
  });
  $$('[data-facet-search]').forEach(input => input.addEventListener("input", () => renderFacet(input.dataset.facetSearch, input.value)));

  $("#active-filters").addEventListener("click", e => {
    const f = e.target.closest("[data-remove-facet]");
    if (f) { state.facets[f.dataset.removeFacet].delete(decodeURIComponent(f.dataset.value)); renderFacet(f.dataset.removeFacet); update(); return; }
    const k = e.target.closest("[data-clear-key]");
    if (k) { const key = k.dataset.clearKey; state[key] = key === "collection" || key === "rewatch" ? "all" : ""; syncControls(); update(); }
  });

  $("#reset-filters").addEventListener("click", resetFilters);
  $("#load-more").addEventListener("click", () => { visibleCount += config.itemsPerPage || 48; renderCatalog(); });
  $("#grid-view").addEventListener("click", () => setView("grid"));
  $("#list-view").addEventListener("click", () => setView("list"));
  $("#export-filtered").addEventListener("click", () => { exportFilmsCsv(filtered); showToast(`Exported ${filtered.length} films`); });
  $("#surprise-me").addEventListener("click", () => {
    if (!filtered.length) return showToast("No films match your current filters.");
    const watchPool = filtered.filter(f => f.user?.watchlist);
    const pool = watchPool.length ? watchPool : filtered;
    const film = pool[Math.floor(Math.random() * pool.length)];
    openFilmDialog(film);
  });
  $("#mobile-filter-button").addEventListener("click", () => $("#filters").classList.toggle("open"));
  $("#close-mobile-filters").addEventListener("click", () => $("#filters").classList.remove("open"));
  $("#save-view").addEventListener("click", saveView);
  $("#saved-views").addEventListener("change", e => { if (e.target.value) loadSavedView(e.target.value); });
}

function setView(view) {
  state.view = view;
  localStorage.setItem("film-atlas-view", view);
  syncControls(); renderCatalog();
}

function serializableState() {
  return { ...state, facets: Object.fromEntries(Object.entries(state.facets).map(([k,v]) => [k, [...v]])) };
}

function saveView() {
  const name = prompt("Name this filter view:");
  if (!name?.trim()) return;
  const views = JSON.parse(localStorage.getItem("film-atlas-saved-views") || "{}");
  views[name.trim()] = serializableState();
  localStorage.setItem("film-atlas-saved-views", JSON.stringify(views));
  populateSavedViews();
  $("#saved-views").value = name.trim();
  showToast(`Saved view “${name.trim()}”`);
}

function populateSavedViews() {
  const select = $("#saved-views");
  const views = JSON.parse(localStorage.getItem("film-atlas-saved-views") || "{}");
  select.innerHTML = '<option value="">Saved views…</option>' + Object.keys(views).sort().map(name => `<option value="${name.replaceAll('"','&quot;')}">${name}</option>`).join("");
}

function loadSavedView(name) {
  const views = JSON.parse(localStorage.getItem("film-atlas-saved-views") || "{}");
  const saved = views[name]; if (!saved) return;
  Object.keys(state).forEach(key => { if (key !== "facets" && saved[key] !== undefined) state[key] = saved[key]; });
  Object.entries(state.facets).forEach(([facet, set]) => { set.clear(); (saved.facets?.[facet] || []).forEach(v => set.add(v)); });
  syncControls(); renderAllFacets(); visibleCount = config.itemsPerPage || 48; update();
}

function applyQueryParams() {
  const params = new URLSearchParams(location.search);
  if (params.get("country")) state.facets.countries.add(params.get("country").toUpperCase());
  if (params.get("collection")) state.collection = params.get("collection");
  if (params.get("q")) state.q = params.get("q");
}

async function main() {
  config = await initShell();
  data = await loadCatalog();
  allFilms = (data.films || []).map(f => ({ ...f, _search: filmSearchText(f) }));
  filmMap = new Map(allFilms.map(f => [f.key, f]));
  visibleCount = config.itemsPerPage || 48;
  renderKpis($("#kpi-strip"), summaryStats(allFilms));
  applyQueryParams();
  populateSavedViews();
  renderAllFacets();
  syncControls();
  bindControls();
  bindCardOpen($("#movie-results"), filmMap);
  update();

  const generated = data.meta?.generated_at ? new Date(data.meta.generated_at).toLocaleString() : "unknown";
  $("#data-status").textContent = `${formatNumber(data.meta?.film_count || 0)} films · metadata build ${generated}`;
  if (!data.meta?.tmdb_enabled && allFilms.length) showToast("TMDB enrichment is not enabled yet. See About → Setup.", 5000);
}

main().catch(err => {
  console.error(err);
  $("#movie-results").innerHTML = `<div class="empty-state" style="grid-column:1/-1"><h3>Could not load your catalog.</h3><p>${err.message}</p><p>Run <code>python scripts/enrich.py</code> or let the GitHub Action build it.</p></div>`;
});
