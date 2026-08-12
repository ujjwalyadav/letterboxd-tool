import {
  $, $$, initShell, loadCatalog, escapeHtml, formatNumber, formatDate, showToast,
  csvEscape, mediaTypeLabel
} from "./core.js";

const STORAGE_KEY = "film-atlas-review-drafts";

const state = {
  filter: "all",
  q: "",
  browseAll: false,
  drafts: {},
};

let config;
let catalog;
let buildReport = { unresolved: [] };
let allFilms = [];
let reviewable = [];
let filmMap = new Map();

const statusLabels = {
  unresolved: "Unresolved",
  weak: "Weak match",
  unknown: "Unknown type",
  error: "Error",
  matched: "Matched",
  skipped: "Skipped",
  draft: "Correction saved",
};

function loadDrafts() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    state.drafts = parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    state.drafts = {};
  }
}

function saveDrafts() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state.drafts));
}

function catalogKey(film) {
  return film.key || film.letterboxd_uri || `${film.name}::${film.year || ""}`;
}

function reviewReason(film) {
  return film.match?.reason || film.reason || film.note || "Manual review";
}

function reviewStatus(film) {
  const key = catalogKey(film);
  if (state.drafts[key]) return "draft";
  if (film.match?.status) return film.match.status;
  return "unresolved";
}

function isReviewable(film) {
  const status = film.match?.status;
  return Boolean(
    state.drafts[catalogKey(film)] ||
    film.media_type === "unknown" ||
    ["unresolved", "weak", "error"].includes(status)
  );
}

function seededReviewItems() {
  const unresolved = (buildReport?.unresolved || []).map(item => {
    const key = item.letterboxd_uri || `${item.name}::${item.year || ""}`;
    return {
      key,
      name: item.name,
      year: item.year,
      letterboxd_uri: item.letterboxd_uri,
      media_type: "unknown",
      match: { status: "unresolved", confidence: null, reason: item.reason || "Needs manual review" },
      tmdb: null,
    };
  });
  return unresolved;
}

function buildReviewableList() {
  const seeded = seededReviewItems();
  const catalogItems = allFilms.filter(isReviewable);
  const combined = state.browseAll || (!seeded.length && !catalogItems.length) ? allFilms : [...seeded, ...catalogItems];
  const seen = new Set();
  return combined.filter(film => {
    const key = catalogKey(film);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function matchesFilter(film) {
  const status = reviewStatus(film);
  if (state.filter !== "all" && status !== state.filter) {
    if (!(state.filter === "corrected" && status === "draft")) return false;
  }
  if (!state.q) return true;
  const haystack = [
    film.name, film.year, film.letterboxd_uri, reviewReason(film),
    film.media_type, film.tmdb?.id, film.tmdb?.title
  ].filter(Boolean).join(" ").toLowerCase();
  return haystack.includes(state.q.toLowerCase());
}

function displayCounts() {
  const stats = reviewable.reduce((acc, film) => {
    const status = reviewStatus(film);
    acc.total += 1;
    if (status === "draft") acc.corrected += 1;
    if (status === "unresolved") acc.unresolved += 1;
    if (status === "weak") acc.weak += 1;
    if (status === "unknown" || film.media_type === "unknown") acc.unknown += 1;
    return acc;
  }, { total: 0, corrected: 0, unresolved: 0, weak: 0, unknown: 0 });

  $("#review-summary").innerHTML = `
    <div class="review-stat"><span>Total</span><strong>${formatNumber(stats.total)}</strong></div>
    <div class="review-stat"><span>Unresolved</span><strong>${formatNumber(stats.unresolved)}</strong></div>
    <div class="review-stat"><span>Weak</span><strong>${formatNumber(stats.weak)}</strong></div>
    <div class="review-stat"><span>Unknown</span><strong>${formatNumber(stats.unknown)}</strong></div>
    <div class="review-stat"><span>Saved drafts</span><strong>${formatNumber(stats.corrected)}</strong></div>
  `;
}

function draftFor(film) {
  return state.drafts[catalogKey(film)] || {
    tmdb_id: film.tmdb?.id || "",
    tmdb_media_type: film.tmdb?.media_kind || "",
    media_type: film.media_type || "",
    reason: reviewReason(film),
  };
}

function saveFilmDraft(film, form) {
  const key = catalogKey(film);
  const draft = {
    letterboxd_uri: film.letterboxd_uri || "",
    name: film.name || "",
    year: film.year || "",
    tmdb_id: form.tmdb_id.trim(),
    tmdb_media_type: form.tmdb_media_type.trim(),
    media_type: form.media_type.trim(),
    reason: form.reason.trim(),
    updated_at: new Date().toISOString(),
  };
  if (!draft.tmdb_id && !draft.tmdb_media_type && !draft.media_type && !draft.reason) {
    delete state.drafts[key];
  } else {
    state.drafts[key] = draft;
  }
  saveDrafts();
  render();
  showToast(`${film.name}: saved locally`);
}

function clearFilmDraft(film) {
  delete state.drafts[catalogKey(film)];
  saveDrafts();
  render();
  showToast(`${film.name}: draft cleared`);
}

function csvFromRows(rows, headers) {
  return [headers, ...rows].map(row => row.map(csvEscape).join(",")).join("\n");
}

function downloadCsv(filename, rows, headers) {
  const blob = new Blob([csvFromRows(rows, headers)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function downloadTmdbOverrides() {
  const rows = Object.values(state.drafts)
    .filter(d => d.tmdb_id)
    .map(d => [d.letterboxd_uri || "", d.name || "", d.year || "", d.tmdb_id, d.tmdb_media_type || "", d.media_type || ""]);
  downloadCsv("tmdb_overrides.csv", rows, ["Letterboxd URI", "Name", "Year", "TMDB ID", "TMDB Media Type", "Media Type"]);
}

function downloadMediaOverrides() {
  const rows = Object.values(state.drafts)
    .filter(d => d.media_type)
    .map(d => [d.letterboxd_uri || "", d.name || "", d.year || "", d.media_type, d.tmdb_media_type || "", d.tmdb_id || "", "", "", ""]);
  downloadCsv("media_type_overrides.csv", rows, ["Letterboxd URI", "Name", "Year", "Media Type", "TMDB Media Type", "TMDB ID", "Series ID", "Season", "Episode"]);
}

function renderFilters() {
  $$("[data-kind]").forEach(btn => btn.classList.toggle("active", btn.dataset.kind === state.filter));
  $("#review-search").value = state.q;
}

function renderList() {
  const list = $("#review-list");
  const rows = reviewable.filter(matchesFilter);
  $("#review-empty").hidden = rows.length !== 0;
  list.innerHTML = rows.map(film => {
    const draft = draftFor(film);
    const status = reviewStatus(film);
    return `
      <article class="review-card ${status === "draft" ? "saved" : ""}">
        <div class="review-card-head">
          <div>
            <div class="review-kicker">${escapeHtml(statusLabels[status] || status || "Review")}</div>
            <h2>${escapeHtml(film.name)}${film.year ? ` <span>${escapeHtml(film.year)}</span>` : ""}</h2>
            <p>${escapeHtml(reviewReason(film))}</p>
          </div>
          <div class="review-meta">
            <span class="review-pill">${escapeHtml(mediaTypeLabel(film.media_type || "unknown"))}</span>
            <span class="review-pill">${film.letterboxd_uri ? `<a href="${escapeHtml(film.letterboxd_uri)}" target="_blank" rel="noreferrer">Letterboxd</a>` : "No URI"}</span>
            <span class="review-pill">${film.tmdb?.id ? `TMDB ${escapeHtml(String(film.tmdb.id))}` : "No TMDB match"}</span>
          </div>
        </div>
        <form class="review-form" data-review-form="${escapeHtml(catalogKey(film))}">
          <label>
            <span>TMDB ID</span>
            <input class="field" name="tmdb_id" inputmode="numeric" placeholder="123456" value="${escapeHtml(draft.tmdb_id || "")}">
          </label>
          <label>
            <span>TMDB media type</span>
            <input class="field" name="tmdb_media_type" placeholder="movie or tv" value="${escapeHtml(draft.tmdb_media_type || "")}">
          </label>
          <label>
            <span>Media type</span>
            <input class="field" name="media_type" placeholder="feature_film, short_film, tv_series..." value="${escapeHtml(draft.media_type || "")}">
          </label>
          <label class="review-wide">
            <span>Reason / note</span>
            <textarea class="field review-textarea" name="reason" rows="3" placeholder="What needs to be corrected?">${escapeHtml(draft.reason || reviewReason(film) || "")}</textarea>
          </label>
          <div class="review-actions-row review-wide">
            <button class="button primary" type="submit">Save correction</button>
            <button class="button" type="button" data-clear="${escapeHtml(catalogKey(film))}">Clear</button>
            <a class="button ghost" href="https://www.themoviedb.org/search" target="_blank" rel="noreferrer">Search TMDB</a>
          </div>
          <div class="review-footer review-wide">
            <span>Updated ${draft.updated_at ? formatDate(draft.updated_at.slice(0, 10)) : "locally only"}</span>
            <span>${film.match?.confidence != null ? `Confidence ${Number(film.match.confidence).toFixed(2)}` : "No confidence score"}</span>
          </div>
        </form>
      </article>
    `;
  }).join("");

  if (!rows.length) {
    list.innerHTML = "";
  }
}

function bindEvents() {
  $$("[data-kind]").forEach(btn => {
    btn.addEventListener("click", () => {
      state.filter = btn.dataset.kind;
      render();
    });
  });
  $("#review-search").addEventListener("input", e => {
    state.q = e.target.value;
    renderList();
  });
  $("#download-tmdb-overrides").addEventListener("click", downloadTmdbOverrides);
  $("#download-media-overrides").addEventListener("click", downloadMediaOverrides);
  $("#clear-review-drafts").addEventListener("click", () => {
    localStorage.removeItem(STORAGE_KEY);
    loadDrafts();
    render();
    showToast("Local corrections cleared");
  });
  $("#browse-all-titles").addEventListener("click", () => {
    state.browseAll = !state.browseAll;
    reviewable = buildReviewableList();
    render();
    showToast(state.browseAll ? "Browsing the full catalog" : "Showing only flagged items");
  });
  $("#review-list").addEventListener("submit", e => {
    const form = e.target.closest("form[data-review-form]");
    if (!form) return;
    e.preventDefault();
    const film = filmMap.get(form.dataset.reviewForm);
    if (!film) return;
    const fd = new FormData(form);
    saveFilmDraft(film, {
      tmdb_id: String(fd.get("tmdb_id") || ""),
      tmdb_media_type: String(fd.get("tmdb_media_type") || ""),
      media_type: String(fd.get("media_type") || ""),
      reason: String(fd.get("reason") || ""),
    });
  });
  $("#review-list").addEventListener("click", e => {
    const btn = e.target.closest("[data-clear]");
    if (!btn) return;
    const film = filmMap.get(btn.dataset.clear);
    if (film) clearFilmDraft(film);
  });
}

function render() {
  $("#browse-all-titles").textContent = state.browseAll ? "Show flagged only" : "Browse all titles";
  displayCounts();
  renderFilters();
  renderList();
}

async function main() {
  config = await initShell();
  const [catalogData, reportData] = await Promise.all([
    loadCatalog(),
    fetch("assets/data/build-report.json", { cache: "no-store" }).then(r => {
      if (!r.ok) throw new Error(`Build report failed (${r.status})`);
      return r.json();
    }),
  ]);
  catalog = catalogData;
  buildReport = reportData;
  allFilms = catalog.films || [];
  filmMap = new Map(allFilms.map(f => [catalogKey(f), f]));
  loadDrafts();
  reviewable = buildReviewableList();
  bindEvents();
  render();
  $("#review-summary").setAttribute("aria-label", `Review summary for ${formatNumber(reviewable.length)} items`);
}

main().catch(err => {
  console.error(err);
  $("#review-list").innerHTML = `<div class="empty-state"><h2>Could not load review data.</h2><p>${escapeHtml(err.message)}</p></div>`;
});
