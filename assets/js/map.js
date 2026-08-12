import { $, initShell, loadCatalog, countryName, languageName, formatNumber } from "./core.js";
import { ISO_NUMERIC_TO_A2 } from "./iso-numeric-map.js";

let catalog = [];
let map;
let geoLayer;
let features = [];
let currentCounts = new Map();
let currentScope = "watched";

function countCountries(films) {
  const m = new Map();
  films.forEach(f => (f.tmdb?.production_countries || []).forEach(c => { if (c.code) m.set(c.code, (m.get(c.code) || 0) + 1); }));
  return m;
}
function countLanguages(films) {
  const m = new Map();
  films.forEach(f => { const code=f.tmdb?.original_language; if(code) m.set(code,(m.get(code)||0)+1); });
  return m;
}
function scopeFilms(scope) {
  if (scope === "watchlist") return catalog.filter(f=>f.user?.watchlist);
  if (scope === "all") return catalog;
  return catalog.filter(f=>f.user?.watched);
}
function fillFor(count, max) {
  if (!count) return "rgba(157,168,181,.10)";
  const alpha = .22 + .68 * Math.sqrt(count / Math.max(1,max));
  return `rgba(240,164,93,${alpha.toFixed(2)})`;
}
function featureCode(feature) {
  const n = String(Number(feature.id));
  return ISO_NUMERIC_TO_A2[n] || null;
}
function styleFeature(feature) {
  const code = featureCode(feature);
  const count = currentCounts.get(code) || 0;
  const max = Math.max(1, ...currentCounts.values());
  return { color:"rgba(255,255,255,.17)", weight:.6, fillColor:fillFor(count,max), fillOpacity:1 };
}
function onEachFeature(feature, layer) {
  const code = featureCode(feature);
  const name = code ? countryName(code) : (feature.properties?.name || "Unknown");
  const count = currentCounts.get(code) || 0;
  layer.bindTooltip(`${name}: ${formatNumber(count)} film${count===1?"":"s"}`, { sticky:true });
  if (code) {
    layer.on("click", () => { const c = currentScope === "watchlist" ? "&collection=watchlist" : currentScope === "watched" ? "&collection=watched" : ""; location.href = `index.html?country=${encodeURIComponent(code)}${c}`; });
    layer.on("mouseover", e => e.target.setStyle({ weight:1.5, color:"rgba(255,255,255,.68)" }));
    layer.on("mouseout", e => geoLayer.resetStyle(e.target));
  }
}

async function loadWorld() {
  const res = await fetch("https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json");
  if (!res.ok) throw new Error(`World map failed (${res.status})`);
  const topo = await res.json();
  features = topojson.feature(topo, topo.objects.countries).features;
}

function initMap() {
  map = L.map("world-map", { zoomControl:true, attributionControl:false, minZoom:1, maxZoom:5, worldCopyJump:true }).setView([24,8],2);
  geoLayer = L.geoJSON(features, { style:styleFeature, onEachFeature }).addTo(map);
  map.fitBounds(geoLayer.getBounds(), { padding:[8,8] });
}

function renderSide(films) {
  currentCounts = countCountries(films);
  const langs = countLanguages(films);
  const sorted = [...currentCounts.entries()].sort((a,b)=>b[1]-a[1]);
  const topCount = sorted[0]?.[1] || 1;
  $("#top-countries").innerHTML = sorted.slice(0,15).map(([code,count])=>`<div class="country-row" data-code="${code}"><span>${countryName(code)}</span><span class="country-count">${formatNumber(count)}</span></div>`).join("") || '<p style="color:var(--muted)">No country metadata yet.</p>';
  $("#top-countries").onclick = e => { const row=e.target.closest("[data-code]"); if(row) { const c = currentScope === "watchlist" ? "&collection=watchlist" : currentScope === "watched" ? "&collection=watched" : ""; location.href=`index.html?country=${row.dataset.code}${c}`; } };

  const represented = new Set(currentCounts.keys());
  const allCodes = [...new Set(Object.values(ISO_NUMERIC_TO_A2))].sort((a,b)=>countryName(a).localeCompare(countryName(b)));
  const unseen = allCodes.filter(code=>!represented.has(code));
  $("#unseen-countries").innerHTML = unseen.slice(0,40).map(code=>`<span class="pill">${countryName(code)}</span>`).join("");
  $("#unseen-count").textContent = formatNumber(unseen.length);

  const metadataFilms = films.filter(f=>f.tmdb);
  const multi = films.filter(f=>(f.tmdb?.production_countries || []).length>1).length;
  const values = [
    ["Countries represented", represented.size],
    ["Original languages", langs.size],
    ["Top country", sorted[0] ? countryName(sorted[0][0]) : "—"],
    ["Multi-country productions", multi],
    ["Films in scope", films.length],
    ["Country metadata coverage", films.length ? `${Math.round(metadataFilms.filter(f=>(f.tmdb?.production_countries||[]).length).length/films.length*100)}%` : "—"],
  ];
  $("#map-metrics").innerHTML = values.map(([label,value])=>`<div class="meta-item"><span>${label}</span><strong>${value}</strong></div>`).join("");

  const langRows=[...langs.entries()].sort((a,b)=>b[1]-a[1]).slice(0,12);
  $("#map-languages").innerHTML = langRows.map(([code,count])=>`<div class="country-row"><span>${languageName(code)}</span><span class="country-count">${count}</span></div>`).join("") || '<p style="color:var(--muted)">No language metadata yet.</p>';
  $("#map-max").textContent = topCount;
}

function render(scope) {
  currentScope = scope;
  const films=scopeFilms(scope);
  currentCounts=countCountries(films);
  if (geoLayer) geoLayer.setStyle(styleFeature);
  renderSide(films);
}

async function main() {
  await initShell();
  const data=await loadCatalog(); catalog=data.films||[];
  try { await loadWorld(); initMap(); }
  catch(err) { console.error(err); $("#world-map").innerHTML=`<div class="empty-state"><h3>Map could not load</h3><p>${err.message}</p></div>`; }
  render("watched");
  $("#map-scope").addEventListener("change", e=>render(e.target.value));
}
main().catch(console.error);
