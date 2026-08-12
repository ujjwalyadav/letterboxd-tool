import {
  $, initShell, loadCatalog, summaryStats, renderKpis, diaryEntries,
  average, formatNumber, countryName, languageName, formatRuntime
} from "./core.js";

let allFilms = [];
let charts = [];

function destroyCharts() { charts.forEach(c => c.destroy()); charts = []; }
function countBy(values) {
  const m = new Map(); values.filter(Boolean).forEach(v => m.set(v, (m.get(v) || 0) + 1));
  return [...m.entries()].sort((a,b) => b[1]-a[1]);
}
function top(rows, n=10) { return rows.slice(0,n); }
function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

function chart(id, type, labels, values, label, options={}) {
  const canvas = document.getElementById(id); if (!canvas || !window.Chart) return;
  const c = new Chart(canvas, {
    type,
    data: { labels, datasets: [{ label, data: values, borderColor: css("--accent"), backgroundColor: `${css("--accent")}aa`, borderWidth: 2, pointRadius: type === "line" ? 2 : undefined, tension: type === "line" ? .28 : undefined }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { displayColors: false } },
      scales: type === "doughnut" ? undefined : {
        x: { grid: { display:false }, ticks: { color: css("--muted"), maxRotation:0, autoSkip:true } },
        y: { beginAtZero:true, grid:{ color: css("--line") }, ticks:{ color:css("--muted"), precision:0 } }
      },
      ...options,
    }
  });
  charts.push(c);
}

function renderRankList(id, rows, formatter=x=>x, max=10) {
  const el = document.getElementById(id); if (!el) return;
  const shown = top(rows, max); const high = shown[0]?.[1] || 1;
  el.innerHTML = shown.length ? shown.map(([name,count]) => `<div class="rank-row"><span class="rank-name">${formatter(name)}</span><span class="rank-value">${formatNumber(count)}</span><div class="rank-bar"><span style="width:${Math.max(3,(count/high)*100)}%"></span></div></div>`).join("") : '<p style="color:var(--muted)">No data yet.</p>';
}

function renderStats(scope) {
  destroyCharts();
  const films = scope === "watchlist" ? allFilms.filter(f=>f.user?.watchlist) : scope === "all" ? allFilms : allFilms.filter(f=>f.user?.watched);
  const watchedFilms = films.filter(f=>f.user?.watched);
  const diary = diaryEntries(watchedFilms);
  renderKpis($("#kpi-strip"), summaryStats(scope === "watchlist" ? allFilms.filter(f => f.user?.watchlist) : films));

  const ratings = watchedFilms.map(f=>f.user?.rating).filter(v=>v != null);
  const runtimes = films.map(f=>f.tmdb?.runtime).filter(Boolean);
  const genres = countBy(films.flatMap(f=>f.tmdb?.genres || []));
  const directors = countBy(films.flatMap(f=>(f.tmdb?.directors || []).map(x=>x.name)));
  const actors = countBy(films.flatMap(f=>(f.tmdb?.cast || []).slice(0,10).map(x=>x.name)));
  const countries = countBy(films.flatMap(f=>(f.tmdb?.production_countries || []).map(x=>x.code)));
  const languages = countBy(films.flatMap(f=>[f.tmdb?.original_language].filter(Boolean)));
  const decades = countBy(films.map(f=>f.year ? `${Math.floor(f.year/10)*10}s` : null)).sort((a,b)=>parseInt(a[0])-parseInt(b[0]));

  const yearRows = countBy(diary.map(e=>(e.watched_date || e.entry_date || "").slice(0,4))).filter(x=>x[0]).sort((a,b)=>a[0].localeCompare(b[0]));
  chart("watching-by-year","line",yearRows.map(x=>x[0]),yearRows.map(x=>x[1]),"Watches");

  const ratingBuckets = Array.from({length:10},(_,i)=>(i+1)/2);
  const ratingCounts = ratingBuckets.map(r=>ratings.filter(x=>Number(x)===r).length);
  chart("rating-distribution","bar",ratingBuckets.map(x=>`${x}★`),ratingCounts,"Films");

  chart("genre-chart","bar",top(genres,10).map(x=>x[0]),top(genres,10).map(x=>x[1]),"Films", { indexAxis:"y" });
  chart("decade-chart","bar",decades.map(x=>x[0]),decades.map(x=>x[1]),"Films");

  const runtimeBuckets = [["< 80",0],["80–99",0],["100–119",0],["120–149",0],["150+",0]];
  runtimes.forEach(r=>{ if(r<80) runtimeBuckets[0][1]++; else if(r<100) runtimeBuckets[1][1]++; else if(r<120) runtimeBuckets[2][1]++; else if(r<150) runtimeBuckets[3][1]++; else runtimeBuckets[4][1]++; });
  chart("runtime-chart","bar",runtimeBuckets.map(x=>x[0]),runtimeBuckets.map(x=>x[1]),"Films");

  renderRankList("director-list", directors);
  renderRankList("actor-list", actors);
  renderRankList("country-list", countries, countryName, 12);
  renderRankList("language-list", languages, languageName, 12);

  const avgRuntime = average(runtimes);
  const avgTmdb = average(films.map(f=>f.tmdb?.vote_average).filter(Boolean));
  const rewatchCount = diary.filter(e=>e.rewatch).length;
  const oldestWatchlist = allFilms.filter(f=>f.user?.watchlist_added_date).sort((a,b)=>a.user.watchlist_added_date.localeCompare(b.user.watchlist_added_date))[0];
  const ratedCount = ratings.length;
  $("#secondary-metrics").innerHTML = [
    ["Rated films", formatNumber(ratedCount)],
    ["Average runtime", avgRuntime ? formatRuntime(Math.round(avgRuntime)) : "—"],
    ["Average TMDB score", avgTmdb ? `${avgTmdb.toFixed(2)} / 10` : "—"],
    ["Rewatch diary entries", formatNumber(rewatchCount)],
    ["Oldest watchlist add", oldestWatchlist ? `${oldestWatchlist.name} · ${oldestWatchlist.user.watchlist_added_date}` : "—"],
    ["Metadata coverage", films.length ? `${Math.round(films.filter(f=>f.tmdb).length/films.length*100)}%` : "—"],
  ].map(([a,b])=>`<div class="meta-item"><span>${a}</span><strong>${b}</strong></div>`).join("");

  const fingerprint = [
    ...top(genres,3).map(x=>x[0]),
    ...top(countries,2).map(x=>countryName(x[0])),
    ...top(languages,2).map(x=>languageName(x[0])),
    ...top(directors,2).map(x=>x[0]),
  ].filter(Boolean);
  $("#taste-tags").innerHTML = fingerprint.map(x=>`<span class="insight-tag">${x}</span>`).join("") || '<span class="insight-tag">Add data to reveal your taste</span>';

  const fiveStars = watchedFilms.filter(f=>f.user?.rating===5).length;
  const fourPlus = watchedFilms.filter(f=>f.user?.rating>=4).length;
  $("#taste-summary").textContent = watchedFilms.length
    ? `Across ${formatNumber(watchedFilms.length)} watched films, you have rated ${formatNumber(fourPlus)} at 4★ or above${fiveStars ? `, including ${formatNumber(fiveStars)} perfect 5★ ratings` : ""}. Your most frequent genres, countries, languages, and directors are summarized here.`
    : `Switch to a collection with watched films to see your taste profile.`;

  renderWatchlistInsights();
}

function renderWatchlistInsights() {
  const w = allFilms.filter(f=>f.user?.watchlist);
  const now = Date.now();
  const ages = w.map(f=>f.user?.watchlist_added_date ? Math.max(0,(now-Date.parse(f.user.watchlist_added_date))/86400000) : null).filter(v=>v!=null);
  const median = ages.length ? [...ages].sort((a,b)=>a-b)[Math.floor(ages.length/2)] : null;
  const short = w.filter(f=>f.tmdb?.runtime).sort((a,b)=>a.tmdb.runtime-b.tmdb.runtime).slice(0,5);
  const highlyRated = w.filter(f=>f.tmdb?.vote_count>=200).sort((a,b)=>(b.tmdb?.vote_average||0)-(a.tmdb?.vote_average||0)).slice(0,5);
  const box = $("#watchlist-insights");
  box.innerHTML = `
    <div><span class="eyebrow">Watchlist age</span><h3 style="font-size:2rem;margin:6px 0">${median == null ? "—" : `${Math.round(median)} days`}</h3><p style="color:var(--muted)">Median time films have been waiting on your current watchlist.</p></div>
    <div><span class="eyebrow">Quick wins</span><div class="rank-list" style="margin-top:8px">${short.map(f=>`<div class="rank-row"><span class="rank-name">${f.name}</span><span class="rank-value">${formatRuntime(f.tmdb.runtime)}</span></div>`).join("") || '<span style="color:var(--muted)">No runtime data.</span>'}</div></div>
    <div><span class="eyebrow">Highly rated unseen</span><div class="rank-list" style="margin-top:8px">${highlyRated.map(f=>`<div class="rank-row"><span class="rank-name">${f.name}</span><span class="rank-value">${f.tmdb.vote_average?.toFixed(1)}</span></div>`).join("") || '<span style="color:var(--muted)">No candidates yet.</span>'}</div></div>`;
}

async function main() {
  await initShell();
  const data = await loadCatalog();
  allFilms = data.films || [];
  $("#scope").addEventListener("change", e=>renderStats(e.target.value));
  renderStats("watched");
}
main().catch(err=>{ console.error(err); $("#stats-root").innerHTML=`<div class="empty-state"><h3>Statistics unavailable</h3><p>${err.message}</p></div>`; });
