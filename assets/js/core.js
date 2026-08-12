const STATE = { dataPromise: null, configPromise: null };

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export async function loadConfig() {
  if (!STATE.configPromise) {
    STATE.configPromise = fetch("assets/data/config.json", { cache: "no-store" })
      .then(r => { if (!r.ok) throw new Error(`Config failed (${r.status})`); return r.json(); });
  }
  return STATE.configPromise;
}

export async function loadCatalog() {
  if (!STATE.dataPromise) {
    STATE.dataPromise = fetch("assets/data/catalog.json", { cache: "no-store" })
      .then(r => { if (!r.ok) throw new Error(`Catalog failed (${r.status})`); return r.json(); });
  }
  return STATE.dataPromise;
}

export function formatNumber(value, options = {}) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return new Intl.NumberFormat(undefined, options).format(Number(value));
}

export function formatDate(value, options = { year: "numeric", month: "short", day: "numeric" }) {
  if (!value) return "—";
  const d = new Date(`${value}T12:00:00`);
  return Number.isNaN(d.getTime()) ? value : new Intl.DateTimeFormat(undefined, options).format(d);
}

export function formatRuntime(minutes) {
  if (!minutes) return "—";
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h ? `${h}h ${m}m` : `${m}m`;
}

export function countryName(code) {
  if (!code) return "Unknown";
  try { return new Intl.DisplayNames(undefined, { type: "region" }).of(code) || code; }
  catch { return code; }
}

export function languageName(code) {
  if (!code) return "Unknown";
  try { return new Intl.DisplayNames(undefined, { type: "language" }).of(code) || code; }
  catch { return code; }
}

export function posterFor(film) {
  return film?.tmdb?.poster || "assets/img/poster-placeholder.svg";
}

export function average(values) {
  const nums = values.filter(v => v !== null && v !== undefined && !Number.isNaN(Number(v))).map(Number);
  return nums.length ? nums.reduce((a, b) => a + b, 0) / nums.length : null;
}

export function uniqueWatchedFilms(films) { return films.filter(f => f.user?.watched); }
export function watchlistFilms(films) { return films.filter(f => f.user?.watchlist); }
export function diaryEntries(films) { return films.flatMap(f => (f.user?.diary_entries || []).map(e => ({ ...e, film: f }))); }

export function summaryStats(films) {
  const watched = uniqueWatchedFilms(films);
  const diary = diaryEntries(films);
  const rated = watched.filter(f => f.user?.rating != null);
  const runtimes = diary
    .map(e => e.film?.tmdb?.runtime)
    .filter(Boolean);
  const totalMinutes = runtimes.reduce((a, b) => a + b, 0);
  const rewatchEntries = diary.filter(e => e.rewatch).length;
  return {
    watched: watched.length,
    diaryEntries: diary.length,
    watchlist: watchlistFilms(films).length,
    avgRating: average(rated.map(f => f.user.rating)),
    totalHours: totalMinutes / 60,
    rewatchRate: diary.length ? (rewatchEntries / diary.length) * 100 : 0,
  };
}

function icon(name) {
  const icons = {
    moon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 12.8A8.4 8.4 0 1 1 11.2 3 6.6 6.6 0 0 0 21 12.8Z"/></svg>',
    sun: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.66 6.34l1.41-1.41"/></svg>',
    filter: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 5h16M7 12h10M10 19h4"/></svg>',
    dice: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="4" y="4" width="16" height="16" rx="3"/><circle cx="9" cy="9" r="1" fill="currentColor"/><circle cx="15" cy="15" r="1" fill="currentColor"/><circle cx="15" cy="9" r="1" fill="currentColor"/><circle cx="9" cy="15" r="1" fill="currentColor"/></svg>',
  };
  return icons[name] || "";
}

export async function initShell() {
  const config = await loadConfig();
  const path = location.pathname.split("/").pop() || "index.html";
  const current = path === "" ? "index.html" : path;
  const header = $("#site-header");
  if (header) {
    header.className = "site-header";
    header.innerHTML = `
      <div class="header-inner">
        <a class="brand" href="index.html" aria-label="${escapeHtml(config.siteTitle)} home">
          <span class="brand-mark" aria-hidden="true"></span>
          <span>${escapeHtml(config.siteTitle)}</span>
        </a>
        <nav class="site-nav" aria-label="Primary navigation">
          <a href="index.html" class="${current === "index.html" ? "active" : ""}">Explore</a>
          <a href="statistics.html" class="${current === "statistics.html" ? "active" : ""}">Statistics</a>
          <a href="map.html" class="${current === "map.html" ? "active" : ""}">Map</a>
          <a href="review.html" class="${current === "review.html" ? "active" : ""}">Review</a>
          <a href="about.html" class="${current === "about.html" ? "active" : ""}">About</a>
        </nav>
        <div class="header-actions">
          <button class="icon-button" id="theme-toggle" aria-label="Toggle theme">${icon("moon")}</button>
        </div>
      </div>`;
  }
  const footer = $("#site-footer");
  if (footer) {
    footer.className = "site-footer";
    footer.innerHTML = `<div class="footer-inner"><span>${escapeHtml(config.siteTitle)} · Built from your Letterboxd export</span><span>Movie metadata by <a href="https://www.themoviedb.org/" target="_blank" rel="noreferrer">TMDB</a> · Provider data via JustWatch · Not endorsed or certified by TMDB</span></div>`;
  }

  const savedTheme = localStorage.getItem("film-atlas-theme");
  if (savedTheme === "light") document.body.classList.add("light");
  const themeBtn = $("#theme-toggle");
  if (themeBtn) {
    themeBtn.innerHTML = document.body.classList.contains("light") ? icon("sun") : icon("moon");
    themeBtn.addEventListener("click", () => {
      document.body.classList.toggle("light");
      const theme = document.body.classList.contains("light") ? "light" : "dark";
      localStorage.setItem("film-atlas-theme", theme);
      themeBtn.innerHTML = theme === "light" ? icon("sun") : icon("moon");
    });
  }

  document.title = `${document.body.dataset.pageTitle || ""}${document.body.dataset.pageTitle ? " · " : ""}${config.siteTitle}`;
  $$('[data-config-display-name]').forEach(el => { el.textContent = config.displayName || config.siteTitle; });
  $$('[data-config-tagline]').forEach(el => { el.textContent = config.tagline || ""; });
  return config;
}

export function renderKpis(container, stats) {
  if (!container) return;
  const kpis = [
    ["Watched", formatNumber(stats.watched), "unique titles"],
    ["Diary", formatNumber(stats.diaryEntries), "logged watches"],
    ["Watchlist", formatNumber(stats.watchlist), "waiting for you"],
    ["Average rating", stats.avgRating == null ? "—" : stats.avgRating.toFixed(2), "out of 5"],
    ["Screen time", formatNumber(Math.round(stats.totalHours)), "logged hours"],
  ];
  container.innerHTML = kpis.map(([label, value, note]) => `<div class="kpi"><div class="kpi-label">${label}</div><div class="kpi-value">${value}</div><div class="kpi-note">${note}</div></div>`).join("");
}

export function filmSearchText(film) {
  const t = film.tmdb || {};
  return [
    film.name, film.year, mediaTypeLabel(film), film.media_type, t.title, t.original_title, t.series_name, t.overview, t.tagline,
    ...(t.genres || []), ...(t.keywords || []),
    ...(t.directors || []).map(x => x.name), ...(t.writers || []).map(x => x.name),
    ...(t.cast || []).map(x => x.name), ...(t.production_companies || []).map(x => x.name), t.certification,
    ...(t.production_countries || []).flatMap(x => [x.code, x.name]),
    ...(t.spoken_languages || []).flatMap(x => [x.code, x.name]),
    ...(film.user?.tags || []),
  ].filter(Boolean).join(" ").toLocaleLowerCase();
}

export function showToast(message, timeout = 2500) {
  let stack = $(".toast-stack");
  if (!stack) {
    stack = document.createElement("div");
    stack.className = "toast-stack";
    document.body.appendChild(stack);
  }
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = message;
  stack.appendChild(el);
  setTimeout(() => el.remove(), timeout);
}

export function ratingLabel(value) {
  return value == null ? "Unrated" : `★ ${Number(value).toFixed(1)}`;
}

export const MEDIA_TYPE_LABELS = {
  feature_film: "Feature film", short_film: "Short film", limited_series: "Limited series",
  tv_series: "TV series", tv_episode: "TV episode", unknown: "Unknown",
};
export function mediaTypeLabel(filmOrType) {
  const type = typeof filmOrType === "string" ? filmOrType : filmOrType?.media_type;
  return MEDIA_TYPE_LABELS[type] || "Unknown";
}
export function tmdbEntityUrl(t) {
  if (!t?.id) return null;
  if (t.media_kind === "tv_episode" && t.series_id != null && t.season_number != null && t.episode_number != null)
    return `https://www.themoviedb.org/tv/${Number(t.series_id)}/season/${Number(t.season_number)}/episode/${Number(t.episode_number)}`;
  if (t.media_kind === "tv") return `https://www.themoviedb.org/tv/${Number(t.id)}`;
  return `https://www.themoviedb.org/movie/${Number(t.id)}`;
}

export function createFilmCard(film, view = "grid") {
  const t = film.tmdb || {};
  const rating = film.user?.rating;
  const countries = (t.production_countries || []).slice(0, 2).map(c => c.code).join(" · ");
  const status = film.user?.watchlist && !film.user?.watched ? "Watchlist" : (film.user?.watched ? "Watched" : "Saved");
  const poster = posterFor(film);
  return `
    <article class="movie-card" tabindex="0" data-film-key="${escapeHtml(film.key)}" aria-label="Open ${escapeHtml(film.name)}">
      <div class="poster-wrap">
        <img class="poster" src="${escapeHtml(poster)}" alt="${escapeHtml(film.name)} poster" loading="lazy" onerror="this.src='assets/img/poster-placeholder.svg'">
        <div class="card-badges"><span class="badge ${status === "Watchlist" ? "accent" : ""}">${status}</span><span class="badge media-type-badge">${escapeHtml(mediaTypeLabel(film))}</span>${rating != null ? `<span class="badge">★ ${Number(rating).toFixed(1)}</span>` : ""}</div>
        <div class="card-actions"><button class="button small" data-open-card>Details</button></div>
      </div>
      <div class="movie-meta">
        <div class="movie-title" title="${escapeHtml(film.name)}">${escapeHtml(film.name)}</div>
        <div class="movie-subtitle"><span>${film.year || "—"}</span>${t.runtime ? `<span>${formatRuntime(t.runtime)}</span>` : ""}${countries ? `<span>${escapeHtml(countries)}</span>` : ""}</div>
      </div>
      <div class="list-extra">${escapeHtml(mediaTypeLabel(film))}${t.vote_average ? `<br>TMDB ${Number(t.vote_average).toFixed(1)}` : ""}${t.directors?.[0]?.name ? `<br>${escapeHtml(t.directors[0].name)}` : ""}</div>
    </article>`;
}

function personRows(people) {
  if (!people?.length) return '<span class="pill">No data</span>';
  return people.slice(0, 10).map(p => `
    <div class="person">
      <img class="person-avatar" src="${escapeHtml(p.profile || "assets/img/avatar-placeholder.svg")}" alt="" loading="lazy" onerror="this.src='assets/img/avatar-placeholder.svg'">
      <div style="min-width:0"><div class="person-name">${escapeHtml(p.name)}</div><div class="person-role">${escapeHtml(p.job || "")}</div></div>
    </div>`).join("");
}

export function ensureDialog() {
  let backdrop = $("#film-dialog-backdrop");
  if (backdrop) return backdrop;
  backdrop = document.createElement("div");
  backdrop.id = "film-dialog-backdrop";
  backdrop.className = "dialog-backdrop";
  backdrop.setAttribute("role", "presentation");
  backdrop.innerHTML = '<div class="movie-dialog" role="dialog" aria-modal="true" aria-label="Film details" id="film-dialog"></div>';
  document.body.appendChild(backdrop);
  backdrop.addEventListener("click", e => { if (e.target === backdrop) closeFilmDialog(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeFilmDialog(); });
  return backdrop;
}

export function openFilmDialog(film) {
  const backdrop = ensureDialog();
  const dialog = $("#film-dialog", backdrop);
  const t = film.tmdb || {};
  const u = film.user || {};
  const lb = film.letterboxd_community || {};
  const countries = (t.production_countries || []).map(c => `<span class="pill">${escapeHtml(c.name || countryName(c.code))}</span>`).join("") || '<span class="pill">Unknown</span>';
  const languages = (t.spoken_languages || []).map(l => `<span class="pill">${escapeHtml(l.name || languageName(l.code))}</span>`).join("") || '<span class="pill">Unknown</span>';
  const providers = t.watch_providers || {};
  const streaming = [...new Set([...(providers.flatrate || []), ...(providers.free || []), ...(providers.ads || [])])];
  const history = (u.diary_entries || []).slice().reverse().map(e => `
    <div class="history-row"><span>${formatDate(e.watched_date || e.entry_date)}${e.rewatch ? " · rewatch" : ""}${e.tags?.length ? ` · ${escapeHtml(e.tags.join(", "))}` : ""}</span><strong>${e.rating != null ? `★ ${Number(e.rating).toFixed(1)}` : "—"}</strong></div>`).join("") || '<div class="history-row"><span>No diary entries</span><strong>—</strong></div>';
  const heroStyle = t.backdrop ? `style="background-image:url('${escapeHtml(t.backdrop)}')"` : "";
  const externalLinks = [
    film.letterboxd_uri ? `<a class="button small" href="${escapeHtml(film.letterboxd_uri)}" target="_blank" rel="noreferrer">Letterboxd</a>` : "",
    tmdbEntityUrl(t) ? `<a class="button small" href="${escapeHtml(tmdbEntityUrl(t))}" target="_blank" rel="noreferrer">TMDB</a>` : "",
    t.imdb_id ? `<a class="button small" href="https://www.imdb.com/title/${escapeHtml(t.imdb_id)}/" target="_blank" rel="noreferrer">IMDb</a>` : "",
    providers.link ? `<a class="button small" href="${escapeHtml(providers.link)}" target="_blank" rel="noreferrer">Where to watch (${escapeHtml(providers.region || "")})</a>` : "",
  ].join("");
  dialog.innerHTML = `
    <div class="dialog-hero" ${heroStyle}>
      <button class="icon-button dialog-close" aria-label="Close">×</button>
      <div class="dialog-main">
        <div class="dialog-poster"><img class="poster" src="${escapeHtml(posterFor(film))}" alt="${escapeHtml(film.name)} poster" onerror="this.src='assets/img/poster-placeholder.svg'"></div>
        <div class="dialog-title">
          <div class="eyebrow">${escapeHtml(mediaTypeLabel(film))} · ${u.watchlist ? "On your watchlist" : (u.watched ? "In your history" : "Saved")}</div>
          <h2>${escapeHtml(film.name)}${film.year ? ` <span style="font-weight:400;color:#bac2ca">${film.year}</span>` : ""}</h2>
          <p>${escapeHtml(t.tagline || t.overview || "No synopsis available yet. Add TMDB enrichment to fill in film metadata.")}</p>
        </div>
      </div>
    </div>
    <div class="dialog-body">
      <div>
        <div class="meta-grid">
          <div class="meta-item"><span>Format</span><strong>${escapeHtml(mediaTypeLabel(film))}</strong></div>
          <div class="meta-item"><span>Your rating</span><strong>${ratingLabel(u.rating)}</strong></div>
          <div class="meta-item"><span>TMDB rating</span><strong>${t.vote_average ? `${Number(t.vote_average).toFixed(1)} / 10` : "—"}</strong></div>
          <div class="meta-item"><span>Runtime</span><strong>${formatRuntime(t.runtime)}</strong></div>
          <div class="meta-item"><span>Watches</span><strong>${formatNumber(u.watch_count || 0)}</strong></div>
          <div class="meta-item"><span>TMDB votes</span><strong>${formatNumber(t.vote_count)}</strong></div>
          <div class="meta-item"><span>Letterboxd avg</span><strong>${lb.average_rating != null ? Number(lb.average_rating).toFixed(2) : "—"}</strong></div>
          <div class="meta-item"><span>Letterboxd watches</span><strong>${formatNumber(lb.watches)}</strong></div>
          <div class="meta-item"><span>Release date</span><strong>${formatDate(t.release_date)}</strong></div>
          <div class="meta-item"><span>Certification (${escapeHtml(providers.region || "region")})</span><strong>${escapeHtml(t.certification || "—")}</strong></div>
        </div>
        ${t.media_kind === "tv_episode" ? `<div class="detail-section"><h3>Episode</h3><p style="color:var(--soft)">${escapeHtml(t.series_name || "Series")} · S${String(t.season_number ?? "?").padStart(2,"0")}E${String(t.episode_number ?? "?").padStart(2,"0")}</p></div>` : ""}
        ${t.media_kind === "tv" ? `<div class="detail-section"><h3>Series</h3><p style="color:var(--soft)">${t.number_of_episodes ? `${formatNumber(t.number_of_episodes)} episodes` : ""}${t.number_of_seasons ? ` · ${formatNumber(t.number_of_seasons)} season${Number(t.number_of_seasons)===1?"":"s"}` : ""}${t.episode_runtime ? ` · about ${formatRuntime(t.episode_runtime)} per episode` : ""}</p></div>` : ""}
        <div class="detail-section"><h3>Overview</h3><p style="color:var(--soft)">${escapeHtml(t.overview || "No overview available.")}</p></div>
        <div class="detail-section"><h3>Genres</h3><div class="pill-list">${(t.genres || []).map(x => `<span class="pill">${escapeHtml(x)}</span>`).join("") || '<span class="pill">No data</span>'}</div></div>
        <div class="detail-section"><h3>Countries</h3><div class="pill-list">${countries}</div></div>
        <div class="detail-section"><h3>Languages</h3><div class="pill-list">${languages}</div></div>
        <div class="detail-section"><h3>Keywords</h3><div class="pill-list">${(t.keywords || []).map(x => `<span class="pill">${escapeHtml(x)}</span>`).join("") || '<span class="pill">No data</span>'}</div></div>
        <div class="detail-section"><h3>Production companies</h3><div class="pill-list">${(t.production_companies || []).map(x => `<span class="pill">${escapeHtml(x.name)}</span>`).join("") || '<span class="pill">No data</span>'}</div></div>
        ${streaming.length ? `<div class="detail-section"><h3>Streaming / free / ad-supported in ${escapeHtml(providers.region || "your region")}</h3><div class="pill-list">${streaming.map(x => `<span class="pill">${escapeHtml(x)}</span>`).join("")}</div><p style="color:var(--muted);font-size:.72rem;margin:8px 0 0">Availability data via JustWatch through TMDB.</p></div>` : ""}
        <div class="dialog-links">${externalLinks}</div>
      </div>
      <aside>
        <div class="detail-section" style="margin-top:0"><h3>Director</h3><div class="people-list">${personRows(t.directors)}</div></div>
        <div class="detail-section"><h3>Cast</h3><div class="people-list">${personRows(t.cast)}</div></div>
        <div class="detail-section"><h3>Your history</h3>${history}</div>
        ${u.watchlist_added_date ? `<div class="detail-section"><h3>Watchlist</h3><div class="history-row"><span>Added</span><strong>${formatDate(u.watchlist_added_date)}</strong></div></div>` : ""}
      </aside>
    </div>`;
  $(".dialog-close", dialog)?.addEventListener("click", closeFilmDialog);
  backdrop.classList.add("open");
  document.body.style.overflow = "hidden";
  $(".dialog-close", dialog)?.focus();
}

export function closeFilmDialog() {
  $("#film-dialog-backdrop")?.classList.remove("open");
  document.body.style.overflow = "";
}

export function csvEscape(value) {
  const s = Array.isArray(value) ? value.join(" | ") : String(value ?? "");
  return /[",\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
}

export function exportFilmsCsv(films, filename = "film-atlas-filtered.csv") {
  const headers = ["Name","Year","Format","Format Source","Your Rating","Watched","Watchlist","Watch Count","Last Watched","Runtime","Genres","Countries","Languages","Directors","Cast","Keywords","TMDB Rating","TMDB Votes","TMDB Popularity","Letterboxd URI","TMDB ID"];
  const rows = films.map(f => {
    const t = f.tmdb || {}, u = f.user || {};
    return [
      f.name, f.year, mediaTypeLabel(f), f.media_type_source, u.rating, u.watched, u.watchlist, u.watch_count, u.last_watched, t.runtime,
      t.genres || [], (t.production_countries || []).map(x => x.code), (t.spoken_languages || []).map(x => x.code),
      (t.directors || []).map(x => x.name), (t.cast || []).map(x => x.name), t.keywords || [],
      t.vote_average, t.vote_count, t.popularity, f.letterboxd_uri, t.id,
    ];
  });
  const csv = [headers, ...rows].map(row => row.map(csvEscape).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

export function bindCardOpen(container, filmMap) {
  container.addEventListener("click", e => {
    const card = e.target.closest("[data-film-key]");
    if (!card) return;
    const film = filmMap.get(card.dataset.filmKey);
    if (film) openFilmDialog(film);
  });
  container.addEventListener("keydown", e => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const card = e.target.closest("[data-film-key]");
    if (!card) return;
    e.preventDefault();
    const film = filmMap.get(card.dataset.filmKey);
    if (film) openFilmDialog(film);
  });
}

export function setText(selector, value) { const el = $(selector); if (el) el.textContent = value; }
