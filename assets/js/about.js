import { initShell, loadCatalog, $, formatNumber } from "./core.js";

async function main() {
  await initShell();
  const data = await loadCatalog();
  const meta = data.meta || {};
  $("#build-summary").innerHTML = `
    <div class="meta-item"><span>Films in catalog</span><strong>${formatNumber(meta.film_count || 0)}</strong></div>
    <div class="meta-item"><span>TMDB enrichment</span><strong>${meta.tmdb_enabled ? "Enabled" : "Not enabled"}</strong></div>
    <div class="meta-item"><span>TMDB matches</span><strong>${formatNumber(meta.matched_count || 0)}</strong></div>
    <div class="meta-item"><span>Unresolved matches</span><strong>${formatNumber(meta.unresolved_count || 0)}</strong></div>
    <div class="meta-item"><span>Region</span><strong>${meta.tmdb_region || "DE"}</strong></div>
    <div class="meta-item"><span>Generated</span><strong>${meta.generated_at ? new Date(meta.generated_at).toLocaleString() : "—"}</strong></div>`;
}
main().catch(console.error);
