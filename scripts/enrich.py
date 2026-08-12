#!/usr/bin/env python3
"""Build the static catalog consumed by My Film Atlas.

Reads Letterboxd CSV exports from data/, merges them into one film catalog, and
optionally enriches titles with TMDB when TMDB_API_TOKEN is available.

No API secret is written to the generated JSON.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "assets" / "data"
CACHE_DIR = ROOT / ".cache"
CACHE_FILE = CACHE_DIR / "tmdb.json"
COMMUNITY_CACHE_FILE = CACHE_DIR / "letterboxd_community.json"
OUT_FILE = OUT / "catalog.json"
REPORT_FILE = OUT / "build-report.json"

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE = "https://image.tmdb.org/t/p"
TOKEN = os.environ.get("TMDB_API_TOKEN", "").strip()
TMDB_LANGUAGE = os.environ.get("TMDB_LANGUAGE", "en-US")
TMDB_REGION = os.environ.get("TMDB_REGION", "DE")
REQUEST_DELAY = float(os.environ.get("TMDB_REQUEST_DELAY", "0.12"))
CACHE_DAYS = int(os.environ.get("TMDB_CACHE_DAYS", "30"))
CACHE_SCHEMA_VERSION = 2
SHORT_FILM_MAX_MINUTES = int(os.environ.get("SHORT_FILM_MAX_MINUTES", "40"))

# Letterboxd community updater. Existing CSV/cache values are preserved and only
# new or stale titles are requested again. Set LETTERBOXD_SCRAPE_ENABLED=0 to
# disable network requests while still reading existing community data.
LETTERBOXD_SCRAPE_ENABLED = os.environ.get("LETTERBOXD_SCRAPE_ENABLED", "1").strip().casefold() not in {"0", "false", "no", "off"}
LETTERBOXD_COMMUNITY_REFRESH_DAYS = int(os.environ.get("LETTERBOXD_COMMUNITY_REFRESH_DAYS", "90"))
LETTERBOXD_TIMEOUT = float(os.environ.get("LETTERBOXD_TIMEOUT", "20"))
LETTERBOXD_WORKERS = max(1, int(os.environ.get("LETTERBOXD_WORKERS", "8")))
LETTERBOXD_REQUEST_DELAY = float(os.environ.get("LETTERBOXD_REQUEST_DELAY", "0.15"))


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def norm(text: str | None) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.casefold()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def to_int(value: Any) -> int | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def to_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def as_bool(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"yes", "true", "1", "y"}


def split_tags(value: str | None) -> list[str]:
    if not value:
        return []
    # Letterboxd exports tags as a comma-separated field inside CSV quoting.
    return [x.strip() for x in value.split(",") if x.strip()]


def read_csv(name: str) -> list[dict[str, str]]:
    path = DATA / name
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))

def write_csv(path: Path, rows: list[dict[str, Any]], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        # Support both a date-only value and the UTC timestamps written by this script.
        if len(text) == 10:
            return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_compact_number(text: str | None) -> int | None:
    if not text:
        return None
    value = str(text).strip().upper().replace(",", "").replace(" ", "")
    m = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([KMB])?", value)
    if not m:
        return to_int(value)
    number = float(m.group(1))
    suffix = m.group(2)
    if suffix == "K":
        number *= 1_000
    elif suffix == "M":
        number *= 1_000_000
    elif suffix == "B":
        number *= 1_000_000_000
    return int(number)


def fetch_letterboxd_url(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; MyFilmAtlas/2.0; personal catalog updater)",
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=LETTERBOXD_TIMEOUT) as response:
        body = response.read().decode("utf-8", "ignore")
    if LETTERBOXD_REQUEST_DELAY > 0:
        time.sleep(LETTERBOXD_REQUEST_DELAY)
    return body


def _first_number(patterns: list[str], text: str, *, compact: bool = False) -> int | None:
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if m:
            return parse_compact_number(m.group(1)) if compact else to_int(m.group(1))
    return None


def scrape_letterboxd_page(uri: str) -> dict[str, Any] | None:
    """Best-effort extraction of public community statistics from one film page.

    The parser intentionally does *not* treat JSON-LD ``ratingCount`` as total
    watches and does not treat ``fans`` as likes. Those are different Letterboxd
    concepts. If an exact watch/like value is not exposed in the page markup, the
    field remains None and an existing CSV/cache value is preserved.
    """
    try:
        html = fetch_letterboxd_url(uri)
    except Exception:
        return None

    average_rating = None
    rating_patterns = [
        r'<meta[^>]+name=["\']twitter:data2["\'][^>]+content=["\']([0-9.]+)\s+out\s+of\s+5["\']',
        r'<meta[^>]+content=["\']([0-9.]+)\s+out\s+of\s+5["\'][^>]+name=["\']twitter:data2["\']',
        r'"ratingValue"\s*:\s*"?([0-9.]+)"?',
        r'"averageRating"\s*:\s*([0-9.]+)',
    ]
    for pattern in rating_patterns:
        m = re.search(pattern, html, flags=re.IGNORECASE)
        if m:
            average_rating = to_float(m.group(1))
            if average_rating is not None:
                break

    # Letterboxd has changed markup several times. Prefer explicit watch/like
    # keys or labelled DOM counters and leave values blank rather than substitute
    # ratingCount/fans, which would be semantically wrong.
    watches = _first_number([
        r'"(?:watchCount|watches|watchedByCount|memberCount)"\s*:\s*([0-9]+)',
        r'data-(?:watch-count|watches|watched-count)=["\']([0-9]+)["\']',
        r'(?:watched|watches)[^>]{0,120}data-count=["\']([0-9]+)["\']',
        r'data-count=["\']([0-9]+)["\'][^>]{0,120}(?:watched|watches)',
        r'([0-9][0-9.,]*[KMB]?)\s+(?:members?\s+have\s+)?watched',
    ], html, compact=True)

    likes = _first_number([
        r'"(?:likeCount|likes)"\s*:\s*([0-9]+)',
        r'data-(?:like-count|likes)=["\']([0-9]+)["\']',
        r'(?:liked|likes?)[^>]{0,120}data-count=["\']([0-9]+)["\']',
        r'data-count=["\']([0-9]+)["\'][^>]{0,120}(?:liked|likes?)',
        r'([0-9][0-9.,]*[KMB]?)\s+likes?',
    ], html, compact=True)

    if average_rating is None and watches is None and likes is None:
        return None
    return {"average_rating": average_rating, "watches": watches, "likes": likes}


def community_identity(row: dict[str, Any]) -> str:
    # Use the same identity rules as the catalog merge without depending on
    # function ordering below.
    uri = str(row.get("Letterboxd URI") or row.get("letterboxd_uri") or "").strip()
    if uri:
        uri = uri.split("?", 1)[0].split("#", 1)[0].rstrip("/")
        uri = uri.replace("http://www.letterboxd.com/", "https://letterboxd.com/")
        uri = uri.replace("https://www.letterboxd.com/", "https://letterboxd.com/")
        uri = uri.replace("http://letterboxd.com/", "https://letterboxd.com/")
        return uri
    name = norm(str(row.get("Name") or row.get("name") or ""))
    year = str(row.get("Year") or row.get("year") or "").strip()
    return f"title::{name}::{year}" if name else ""


def _community_row_from_any(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "Letterboxd URI": str(row.get("Letterboxd URI") or row.get("letterboxd_uri") or "").strip(),
        "Name": str(row.get("Name") or row.get("name") or "").strip(),
        "Year": to_int(row.get("Year") if "Year" in row else row.get("year")) or "",
        "Average Rating": to_float(row.get("Average Rating") if "Average Rating" in row else row.get("average_rating")),
        "Watches": to_int(row.get("Watches") if "Watches" in row else row.get("watches")),
        "Likes": to_int(row.get("Likes") if "Likes" in row else row.get("likes")),
        "Updated": str(row.get("Updated") or row.get("updated") or "").strip(),
    }


def load_community_cache() -> dict[str, dict[str, Any]]:
    if not COMMUNITY_CACHE_FILE.exists():
        return {}
    try:
        raw = json.loads(COMMUNITY_CACHE_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_community_cache(cache: dict[str, dict[str, Any]]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    COMMUNITY_CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )


def collect_export_films() -> list[dict[str, Any]]:
    """Collect one row per Letterboxd title from all four user exports."""
    out: dict[str, dict[str, Any]] = {}
    aliases: dict[str, str] = {}
    for filename in ("watched.csv", "ratings.csv", "diary.csv", "watchlist.csv"):
        for row in read_csv(filename):
            uri = str(row.get("Letterboxd URI") or "").strip()
            name = str(row.get("Name") or "").strip()
            year = to_int(row.get("Year"))
            candidate = {"Letterboxd URI": uri, "Name": name, "Year": year or ""}
            key = community_identity(candidate)
            ty = f"title::{norm(name)}::{year or ''}" if name else ""
            existing_key = aliases.get(key) or aliases.get(ty)
            if existing_key and existing_key in out:
                existing = out[existing_key]
                if not existing.get("Letterboxd URI") and uri:
                    existing["Letterboxd URI"] = uri
                continue
            if not key:
                continue
            out[key] = candidate
            aliases[key] = key
            if ty:
                aliases[ty] = key
    return list(out.values())


def update_letterboxd_community() -> dict[str, int]:
    """Merge repo CSV + persistent Actions cache and scrape only new/stale titles.

    Existing non-null values are never replaced by None. The generated CSV is a
    convenient, inspectable snapshot; `.cache/letterboxd_community.json` is the
    persistent incremental cache that should be included in the GitHub Actions
    cache alongside the TMDB cache.
    """
    films = collect_export_films()
    disk_rows = read_csv("letterboxd_community.csv")
    cache = load_community_cache()

    merged: dict[str, dict[str, Any]] = {}
    attempts: dict[str, str] = {}

    # Cache first, then the repository CSV so manually supplied CSV values win.
    for key, entry in cache.items():
        if isinstance(entry, dict) and isinstance(entry.get("row"), dict):
            merged[key] = _community_row_from_any(entry["row"])
            attempts[key] = str(entry.get("last_attempted") or "")
    for raw in disk_rows:
        row = _community_row_from_any(raw)
        key = community_identity(row)
        if key:
            old = merged.get(key, {})
            # Manual/repository values override cache when non-empty.
            for field, value in row.items():
                if value not in (None, ""):
                    old[field] = value
            merged[key] = old

    # Also index by title/year to survive harmless URI differences.
    alias_to_key: dict[str, str] = {}
    for key, row in merged.items():
        alias_to_key[key] = key
        if row.get("Name"):
            alias_to_key[f"title::{norm(str(row.get('Name')))}::{row.get('Year') or ''}"] = key

    now = datetime.now(timezone.utc)
    tasks: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    skipped_fresh = 0
    for film in films:
        key = community_identity(film)
        ty = f"title::{norm(str(film.get('Name') or ''))}::{film.get('Year') or ''}"
        existing_key = alias_to_key.get(key) or alias_to_key.get(ty) or key
        existing = dict(merged.get(existing_key) or _community_row_from_any(film))
        # Ensure identity/display columns are current.
        for field in ("Letterboxd URI", "Name", "Year"):
            if film.get(field) not in (None, ""):
                existing[field] = film[field]
        merged[existing_key] = existing
        alias_to_key[key] = existing_key
        alias_to_key[ty] = existing_key

        last_attempt = parse_iso_datetime(attempts.get(existing_key))
        updated = parse_iso_datetime(str(existing.get("Updated") or ""))
        freshness_anchor = last_attempt or updated
        is_fresh = bool(freshness_anchor and (now - freshness_anchor).days < LETTERBOXD_COMMUNITY_REFRESH_DAYS)
        if is_fresh:
            skipped_fresh += 1
            continue
        uri = str(existing.get("Letterboxd URI") or "").strip()
        if LETTERBOXD_SCRAPE_ENABLED and uri:
            tasks.append((existing_key, film, existing))

    fetched = 0
    successful = 0
    if tasks:
        workers = min(LETTERBOXD_WORKERS, len(tasks))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_map = {
                pool.submit(scrape_letterboxd_page, str(existing.get("Letterboxd URI") or "")): (key, film, existing)
                for key, film, existing in tasks
            }
            for i, future in enumerate(as_completed(future_map), start=1):
                key, film, existing = future_map[future]
                fetched += 1
                attempts[key] = utc_now()
                try:
                    stats = future.result()
                except Exception:
                    stats = None
                if stats:
                    changed = False
                    mapping = (("Average Rating", "average_rating"), ("Watches", "watches"), ("Likes", "likes"))
                    for csv_field, stats_field in mapping:
                        value = stats.get(stats_field)
                        if value is not None:
                            existing[csv_field] = value
                            changed = True
                    if changed:
                        existing["Updated"] = utc_now()
                        successful += 1
                merged[key] = existing
                if i % 100 == 0:
                    print(f"Letterboxd community: checked {i}/{len(tasks)} stale/new titles...", file=sys.stderr)

    # Emit exactly one row per current catalog title. Do not keep unrelated old rows.
    output_rows: list[dict[str, Any]] = []
    new_cache: dict[str, dict[str, Any]] = {}
    seen_output: set[str] = set()
    for film in films:
        key = community_identity(film)
        ty = f"title::{norm(str(film.get('Name') or ''))}::{film.get('Year') or ''}"
        source_key = alias_to_key.get(key) or alias_to_key.get(ty) or key
        row = _community_row_from_any(merged.get(source_key) or film)
        final_key = community_identity(row) or key
        if not final_key or final_key in seen_output:
            continue
        seen_output.add(final_key)
        output_rows.append(row)
        new_cache[final_key] = {
            "row": row,
            "last_attempted": attempts.get(source_key) or attempts.get(final_key) or "",
        }

    output_rows.sort(key=lambda r: (str(r.get("Name") or "").casefold(), to_int(r.get("Year")) or 0, str(r.get("Letterboxd URI") or "")))
    write_csv(
        DATA / "letterboxd_community.csv",
        output_rows,
        ["Letterboxd URI", "Name", "Year", "Average Rating", "Watches", "Likes", "Updated"],
    )
    save_community_cache(new_cache)
    return {
        "titles": len(films),
        "requested": fetched,
        "successful": successful,
        "fresh_reused": skipped_fresh,
    }



def normalized_letterboxd_uri(row: dict[str, str]) -> str:
    """Return a stable Letterboxd URI identity when one is present.

    Letterboxd exports from different files can occasionally vary in trivial URI
    formatting.  We normalize scheme/host/trailing slashes so those variants do
    not create duplicate films.
    """
    uri = (row.get("Letterboxd URI") or "").strip()
    if not uri:
        return ""
    uri = uri.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    uri = uri.replace("http://www.letterboxd.com/", "https://letterboxd.com/")
    uri = uri.replace("https://www.letterboxd.com/", "https://letterboxd.com/")
    uri = uri.replace("http://letterboxd.com/", "https://letterboxd.com/")
    return uri


def title_year_key(row: dict[str, str]) -> str:
    name = norm(row.get("Name"))
    year = (row.get("Year") or "").strip()
    return f"title::{name}::{year}" if name else ""


def film_key(row: dict[str, str]) -> str:
    # Prefer the canonical Letterboxd URI for cache stability, but every film is
    # also indexed by title+year during merging (see get_or_create).
    return normalized_letterboxd_uri(row) or title_year_key(row)


def base_film(row: dict[str, str]) -> dict[str, Any]:
    return {
        "key": film_key(row),
        "name": (row.get("Name") or "").strip(),
        "year": to_int(row.get("Year")),
        "letterboxd_uri": (row.get("Letterboxd URI") or "").strip(),
        "user": {
            "rating": None,
            "rating_date": None,
            "watched": False,
            "watched_added_date": None,
            "watchlist": False,
            "watchlist_added_date": None,
            "diary_entries": [],
            "watch_count": 0,
            "rewatch_count": 0,
            "first_watched": None,
            "last_watched": None,
            "tags": [],
        },
        "tmdb": None,
        "media_type": "unknown",
        "media_type_label": "Unknown",
        "media_type_source": "unclassified",
        "letterboxd_community": None,
        "match": {"status": "not-attempted", "confidence": None, "reason": None},
    }


def get_or_create(
    catalog: dict[str, dict[str, Any]],
    aliases: dict[str, str],
    row: dict[str, str],
) -> dict[str, Any]:
    """Get one logical film across watched/ratings/diary/watchlist exports.

    A film is matched by *either* normalized Letterboxd URI or normalized
    title+year. This is intentionally more tolerant than using the raw URI as
    the sole dictionary key, which could create one card from watched.csv and a
    second card from ratings.csv for the same movie.
    """
    uri_alias = normalized_letterboxd_uri(row)
    ty_alias = title_year_key(row)
    alias_candidates = [a for a in (uri_alias, ty_alias) if a]

    canonical_key = next((aliases[a] for a in alias_candidates if a in aliases), None)
    if canonical_key is None:
        canonical_key = uri_alias or ty_alias
        if not canonical_key:
            canonical_key = f"unknown::{len(catalog)}"
        catalog[canonical_key] = base_film(row)
        # Keep the base object's key aligned with the actual catalog key.
        catalog[canonical_key]["key"] = canonical_key

    film = catalog[canonical_key]
    for alias in alias_candidates:
        aliases[alias] = canonical_key

    if not film.get("name"):
        film["name"] = (row.get("Name") or "").strip()
    if not film.get("year"):
        film["year"] = to_int(row.get("Year"))
    if not film.get("letterboxd_uri"):
        film["letterboxd_uri"] = (row.get("Letterboxd URI") or "").strip()
    return film


def merge_letterboxd() -> dict[str, dict[str, Any]]:
    """Merge Letterboxd exports into exactly one logical record per film.

    Semantics of the standard Letterboxd export used by this project:
    - watched.csv = master set of films the user has seen.
    - ratings.csv = rated subset of watched films.
    - diary.csv = dated viewing entries; only a subset of watched films need be here.
    - watchlist.csv = films saved to the watchlist; these may be unseen or may overlap.

    We therefore build watched first and attach ratings/diary metadata to those
    existing records whenever possible. A rating or diary row never intentionally
    creates a second card for a film already represented by watched.csv.
    """
    catalog: dict[str, dict[str, Any]] = {}
    aliases: dict[str, str] = {}
    title_index: dict[str, set[str]] = defaultdict(set)

    def index_film(key: str, film: dict[str, Any], row: dict[str, str]) -> None:
        uri = normalized_letterboxd_uri(row)
        ty = title_year_key(row)
        for alias in (uri, ty):
            if alias:
                aliases[alias] = key
        ntitle = norm(row.get("Name") or film.get("name"))
        if ntitle:
            title_index[ntitle].add(key)

    def resolve_existing(row: dict[str, str], allow_unique_title: bool = False) -> dict[str, Any] | None:
        uri = normalized_letterboxd_uri(row)
        ty = title_year_key(row)
        for alias in (uri, ty):
            if alias and alias in aliases and aliases[alias] in catalog:
                return catalog[aliases[alias]]

        # ratings.csv and diary.csv are subsets of watched.csv. If a trivial
        # title/year discrepancy exists, a unique normalized title is safer than
        # manufacturing a duplicate film card.
        if allow_unique_title:
            ntitle = norm(row.get("Name"))
            keys = title_index.get(ntitle, set())
            if len(keys) == 1:
                return catalog[next(iter(keys))]
            row_year = to_int(row.get("Year"))
            if row_year and keys:
                close = [k for k in keys if catalog[k].get("year") and abs(catalog[k]["year"] - row_year) <= 1]
                if len(close) == 1:
                    return catalog[close[0]]
        return None

    def create(row: dict[str, str]) -> dict[str, Any]:
        key = film_key(row) or f"unknown::{len(catalog)}"
        # Extremely defensive: if the preferred key already exists, reuse it.
        if key in catalog:
            film = catalog[key]
        else:
            film = base_film(row)
            film["key"] = key
            catalog[key] = film
        index_film(key, film, row)
        return film

    def attach(row: dict[str, str], *, subset_of_watched: bool = False) -> dict[str, Any]:
        film = resolve_existing(row, allow_unique_title=subset_of_watched)
        if film is None:
            film = create(row)
            if subset_of_watched:
                # A rating or diary row logically proves the film was watched,
                # even if watched.csv is unexpectedly missing/mismatched.
                film["user"]["watched"] = True
        else:
            index_film(film["key"], film, row)
            if not film.get("letterboxd_uri"):
                film["letterboxd_uri"] = (row.get("Letterboxd URI") or "").strip()
            if not film.get("year"):
                film["year"] = to_int(row.get("Year"))
        return film

    # 1) watched.csv is the authoritative universe of seen films.
    for row in read_csv("watched.csv"):
        f = attach(row)
        f["user"]["watched"] = True
        f["user"]["watched_added_date"] = (row.get("Date") or "").strip() or f["user"].get("watched_added_date")

    # 2) Ratings decorate an already-watched film; they are not separate films.
    for row in read_csv("ratings.csv"):
        f = attach(row, subset_of_watched=True)
        f["user"]["rating"] = to_float(row.get("Rating"))
        f["user"]["rating_date"] = (row.get("Date") or "").strip() or None

    # 3) Diary entries are dated viewings of watched films. Multiple diary rows
    # intentionally remain as multiple viewing events inside ONE film record.
    for row in read_csv("diary.csv"):
        f = attach(row, subset_of_watched=True)
        f["user"]["watched"] = True
        watched_date = (row.get("Watched Date") or "").strip() or None
        f["user"]["diary_entries"].append({
            "entry_date": (row.get("Date") or "").strip() or None,
            "watched_date": watched_date,
            "rating": to_float(row.get("Rating")),
            "rewatch": as_bool(row.get("Rewatch")),
            "tags": split_tags(row.get("Tags")),
        })

    # 4) Watchlist may introduce genuinely new (unseen) films, but if the same
    # title is already watched it is merged rather than duplicated.
    for row in read_csv("watchlist.csv"):
        f = attach(row, subset_of_watched=False)
        f["user"]["watchlist"] = True
        f["user"]["watchlist_added_date"] = (row.get("Date") or "").strip() or None

    for film in catalog.values():
        u = film["user"]
        entries = u["diary_entries"]
        entries.sort(key=lambda x: x.get("watched_date") or x.get("entry_date") or "")

        # watched.csv means at least one historical viewing even when that view
        # predates diary tracking. Diary rows provide exact dated viewings only.
        # Avoid pretending that an undated watched.csv row and a diary row are two
        # separate views; when diary entries exist, use those as the known count.
        u["watch_count"] = len(entries) if entries else (1 if u["watched"] else 0)
        u["rewatch_count"] = sum(1 for e in entries if e["rewatch"])
        watched_dates = [e["watched_date"] for e in entries if e.get("watched_date")]
        if watched_dates:
            u["first_watched"] = min(watched_dates)
            u["last_watched"] = max(watched_dates)
        tag_counter = Counter(t for e in entries for t in e["tags"])
        u["tags"] = [t for t, _ in tag_counter.most_common()]
        if u["rating"] is None:
            diary_ratings = [e for e in entries if e.get("rating") is not None]
            if diary_ratings:
                u["rating"] = diary_ratings[-1]["rating"]

    # Optional manual/authorized source for community statistics.
    for row in read_csv("letterboxd_community.csv"):
        film = resolve_existing(row, allow_unique_title=True)
        if film:
            film["letterboxd_community"] = {
                "average_rating": to_float(row.get("Average Rating")),
                "watches": to_int(row.get("Watches")),
                "likes": to_int(row.get("Likes")),
                "updated": (row.get("Updated") or "").strip() or None,
            }

    return catalog


MEDIA_TYPE_LABELS = {
    "feature_film": "Feature film",
    "short_film": "Short film",
    "limited_series": "Limited series",
    "tv_series": "TV series",
    "tv_episode": "TV episode",
    "unknown": "Unknown",
}


def normalize_media_type(value: str | None) -> str | None:
    if not value:
        return None
    key = norm(value).replace(" ", "_")
    aliases = {
        "movie": "feature_film", "film": "feature_film", "feature": "feature_film", "feature_film": "feature_film",
        "short": "short_film", "short_film": "short_film",
        "limited_series": "limited_series", "miniseries": "limited_series", "mini_series": "limited_series",
        "tv": "tv_series", "tv_series": "tv_series", "series": "tv_series",
        "episode": "tv_episode", "tv_episode": "tv_episode",
        "unknown": "unknown",
    }
    return aliases.get(key)


def set_media_type(film: dict[str, Any], media_type: str | None, source: str) -> None:
    media_type = normalize_media_type(media_type) or "unknown"
    film["media_type"] = media_type
    film["media_type_label"] = MEDIA_TYPE_LABELS.get(media_type, media_type.replace("_", " ").title())
    film["media_type_source"] = source


def classify_from_tmdb(tmdb: dict[str, Any] | None) -> str:
    t = tmdb or {}
    kind = t.get("media_kind") or "movie"
    if kind == "tv_episode":
        return "tv_episode"
    if kind == "tv":
        if norm(t.get("series_type")) in {"miniseries", "mini series"}:
            return "limited_series"
        return "tv_series"
    runtime = to_int(t.get("runtime"))
    if runtime is not None and runtime <= SHORT_FILM_MAX_MINUTES:
        return "short_film"
    return "feature_film"


def load_media_type_overrides() -> dict[str, dict[str, Any]]:
    overrides: dict[str, dict[str, Any]] = {}
    for row in read_csv("media_type_overrides.csv"):
        key = film_key(row)
        media_type = normalize_media_type(row.get("Media Type"))
        if not key:
            continue
        overrides[key] = {
            "media_type": media_type,
            "tmdb_media_type": (row.get("TMDB Media Type") or "").strip().casefold() or None,
            "tmdb_id": to_int(row.get("TMDB ID")),
            "series_id": to_int(row.get("Series ID")),
            "season": to_int(row.get("Season")),
            "episode": to_int(row.get("Episode")),
        }
    return overrides


def merge_user_records(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Merge two accidental duplicate film records without losing user data."""
    tu, su = target["user"], source["user"]
    tu["watched"] = bool(tu.get("watched") or su.get("watched"))
    tu["watchlist"] = bool(tu.get("watchlist") or su.get("watchlist"))
    if tu.get("rating") is None and su.get("rating") is not None:
        tu["rating"] = su["rating"]
        tu["rating_date"] = su.get("rating_date")
    elif su.get("rating") is not None and (su.get("rating_date") or "") > (tu.get("rating_date") or ""):
        tu["rating"] = su["rating"]
        tu["rating_date"] = su.get("rating_date")
    for field in ("watched_added_date", "watchlist_added_date"):
        vals = [v for v in (tu.get(field), su.get(field)) if v]
        tu[field] = min(vals) if vals else None

    seen_entries = set()
    merged_entries = []
    for entry in (tu.get("diary_entries") or []) + (su.get("diary_entries") or []):
        sig = (entry.get("entry_date"), entry.get("watched_date"), entry.get("rating"), entry.get("rewatch"), tuple(entry.get("tags") or []))
        if sig not in seen_entries:
            seen_entries.add(sig)
            merged_entries.append(entry)
    merged_entries.sort(key=lambda x: x.get("watched_date") or x.get("entry_date") or "")
    tu["diary_entries"] = merged_entries
    tu["watch_count"] = len(merged_entries) if merged_entries else (1 if tu["watched"] else 0)
    tu["rewatch_count"] = sum(1 for e in merged_entries if e.get("rewatch"))
    dates = [e.get("watched_date") for e in merged_entries if e.get("watched_date")]
    tu["first_watched"] = min(dates) if dates else tu.get("first_watched") or su.get("first_watched")
    tu["last_watched"] = max(dates) if dates else tu.get("last_watched") or su.get("last_watched")
    tags = Counter(t for e in merged_entries for t in e.get("tags") or [])
    tu["tags"] = [t for t, _ in tags.most_common()]

    if not target.get("letterboxd_uri"):
        target["letterboxd_uri"] = source.get("letterboxd_uri")
    if not target.get("name"):
        target["name"] = source.get("name")
    if not target.get("year"):
        target["year"] = source.get("year")
    if not target.get("tmdb") and source.get("tmdb"):
        target["tmdb"] = source["tmdb"]
        target["match"] = source.get("match", target.get("match"))
    if not target.get("letterboxd_community") and source.get("letterboxd_community"):
        target["letterboxd_community"] = source["letterboxd_community"]
    if target.get("media_type") in {None, "unknown"} and source.get("media_type") not in {None, "unknown"}:
        set_media_type(target, source.get("media_type"), source.get("media_type_source") or "merged")


def dedupe_after_enrichment(catalog: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Final safety net: one logical TMDB entity can never produce two cards.

    Movie and TV namespaces can reuse the same numeric ID, so the media namespace
    is part of the identity. Episodes additionally include their series/season/episode.
    """
    out: dict[str, dict[str, Any]] = {}
    tmdb_to_key: dict[str, str] = {}
    fallback_to_key: dict[str, str] = {}

    for film in catalog.values():
        t = film.get("tmdb") or {}
        tmdb_id = to_int(t.get("id"))
        kind = t.get("media_kind") or "movie"
        if kind == "tv_episode":
            entity = f"tv_episode::{t.get('series_id')}::{t.get('season_number')}::{t.get('episode_number')}"
        else:
            entity = f"{kind}::{tmdb_id}" if tmdb_id else ""
        fallback = f"{norm(film.get('name'))}::{film.get('year') or ''}"
        existing_key = tmdb_to_key.get(entity) if entity else fallback_to_key.get(fallback)
        if existing_key and existing_key in out:
            merge_user_records(out[existing_key], film)
            continue
        key = film["key"]
        out[key] = film
        if entity:
            tmdb_to_key[entity] = key
        if fallback.strip(":"):
            fallback_to_key[fallback] = key
    return out


def load_overrides() -> dict[str, dict[str, Any]]:
    """Load legacy TMDB overrides; optional media-type columns are supported."""
    overrides: dict[str, dict[str, Any]] = {}
    for row in read_csv("tmdb_overrides.csv"):
        key = film_key(row)
        tmdb_id = to_int(row.get("TMDB ID"))
        if key and tmdb_id:
            overrides[key] = {
                "tmdb_id": tmdb_id,
                "tmdb_media_type": (row.get("TMDB Media Type") or "movie").strip().casefold(),
                "series_id": to_int(row.get("Series ID")),
                "season": to_int(row.get("Season")),
                "episode": to_int(row.get("Episode")),
                "media_type": normalize_media_type(row.get("Media Type")),
            }
    return overrides

def load_cache() -> dict[str, Any]:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_cache(cache: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def tmdb_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if not TOKEN:
        raise RuntimeError("TMDB_API_TOKEN not configured")
    params = dict(params or {})
    query = urllib.parse.urlencode(params, doseq=True)
    url = f"{TMDB_BASE}{path}" + (f"?{query}" if query else "")
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "accept": "application/json",
            "User-Agent": "my-film-atlas/1.0",
        },
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
            time.sleep(REQUEST_DELAY)
            return json.loads(body)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or 500 <= exc.code < 600:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        except urllib.error.URLError:
            if attempt == 3:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("TMDB request failed after retries")


def year_from_date(value: str | None) -> int | None:
    if not value or len(value) < 4:
        return None
    return to_int(value[:4])


def choose_search_result(name: str, year: int | None, results: list[dict[str, Any]], media_kind: str) -> tuple[dict[str, Any] | None, float, str]:
    if not results:
        return None, 0.0, "no-results"
    target = norm(name)
    scored: list[tuple[float, dict[str, Any], str]] = []
    for candidate in results[:12]:
        title = norm(candidate.get("title") if media_kind == "movie" else candidate.get("name"))
        original = norm(candidate.get("original_title") if media_kind == "movie" else candidate.get("original_name"))
        c_year = year_from_date(candidate.get("release_date") if media_kind == "movie" else candidate.get("first_air_date"))
        score = 0.0
        reasons = []
        if target and target == title:
            score += 0.68; reasons.append("exact-title")
        elif target and target == original:
            score += 0.64; reasons.append("exact-original-title")
        elif target and title and (target in title or title in target):
            score += 0.48; reasons.append("close-title")
        else:
            a, b = set(target.split()), set(title.split())
            if a and b:
                overlap = len(a & b) / max(len(a), len(b))
                score += min(0.42, overlap * 0.42)
                if overlap >= 0.6: reasons.append("token-overlap")
        if year and c_year:
            diff = abs(year - c_year)
            if diff == 0:
                score += 0.28; reasons.append("exact-year")
            elif diff == 1:
                score += 0.16; reasons.append("near-year")
            elif diff >= 3:
                score -= 0.15
        score += min(0.04, float(candidate.get("vote_count") or 0) / 250000)
        scored.append((score, candidate, "+".join(reasons) or "weak-match"))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1], round(scored[0][0], 3), scored[0][2]


def search_one(name: str, year: int | None, media_kind: str) -> tuple[dict[str, Any] | None, float, str]:
    params = {"query": name, "include_adult": "false", "language": TMDB_LANGUAGE}
    year_key = "primary_release_year" if media_kind == "movie" else "first_air_date_year"
    if year:
        params[year_key] = year
    payload = tmdb_get(f"/search/{media_kind}", params)
    result, confidence, reason = choose_search_result(name, year, payload.get("results") or [], media_kind)
    if not result and year:
        params.pop(year_key, None)
        payload = tmdb_get(f"/search/{media_kind}", params)
        result, confidence, reason = choose_search_result(name, year, payload.get("results") or [], media_kind)
    return result, confidence, reason


def tmdb_search(name: str, year: int | None) -> tuple[int | None, str | None, float, str]:
    """Search movie and TV namespaces in one TMDB multi-search request.

    TMDB multi search returns movies and TV shows together. It does not provide
    general episode-title search, so episode entries can be made exact through
    media_type_overrides.csv.
    """
    payload = tmdb_get("/search/multi", {"query": name, "include_adult": "false", "language": TMDB_LANGUAGE})
    mixed = [x for x in payload.get("results") or [] if x.get("media_type") in {"movie", "tv"}]
    scored = []
    for kind in ("movie", "tv"):
        subset = [x for x in mixed if x.get("media_type") == kind]
        result, confidence, reason = choose_search_result(name, year, subset, kind)
        if result:
            scored.append((confidence, kind, result, reason))
    if not scored:
        return None, None, 0.0, "no-results"
    # Prefer a movie only as a final tie-breaker; strong TV matches win normally.
    scored.sort(key=lambda x: (x[0], 1 if x[1] == "movie" else 0), reverse=True)
    confidence, kind, result, reason = scored[0]
    if confidence < 0.55:
        return None, None, confidence, "low-confidence:" + reason
    return int(result["id"]), kind, confidence, f"{kind}:{reason}"


def tmdb_details(entity_id: int, media_kind: str, override: dict[str, Any] | None = None) -> dict[str, Any]:
    override = override or {}
    if media_kind == "tv_episode":
        series_id = override.get("series_id")
        season = override.get("season")
        episode = override.get("episode")
        if not all(v is not None for v in (series_id, season, episode)):
            raise ValueError("TV episode override requires Series ID, Season and Episode")
        details = tmdb_get(
            f"/tv/{series_id}/season/{season}/episode/{episode}",
            {"language": TMDB_LANGUAGE, "append_to_response": "credits,external_ids"},
        )
        series = tmdb_get(
            f"/tv/{series_id}",
            {"language": TMDB_LANGUAGE, "append_to_response": "aggregate_credits,keywords,external_ids,content_ratings"},
        )
        providers = tmdb_get(f"/tv/{series_id}/watch/providers")
        details["_series"] = series
        details["watch_providers"] = providers
        details["_series_id"] = series_id
        return details
    append = "credits,keywords,external_ids,release_dates" if media_kind == "movie" else "aggregate_credits,keywords,external_ids,content_ratings"
    details = tmdb_get(f"/{media_kind}/{entity_id}", {"language": TMDB_LANGUAGE, "append_to_response": append})
    details["watch_providers"] = tmdb_get(f"/{media_kind}/{entity_id}/watch/providers")
    return details


def image_url(path: str | None, size: str) -> str | None:
    return f"{TMDB_IMAGE}/{size}{path}" if path else None


def provider_data(raw: dict[str, Any]) -> dict[str, Any]:
    providers = ((raw.get("watch_providers") or {}).get("results") or {}).get(TMDB_REGION, {})
    return {
        "region": TMDB_REGION,
        "link": providers.get("link"),
        "flatrate": [p.get("provider_name") for p in providers.get("flatrate") or []],
        "free": [p.get("provider_name") for p in providers.get("free") or []],
        "ads": [p.get("provider_name") for p in providers.get("ads") or []],
        "rent": [p.get("provider_name") for p in providers.get("rent") or []],
        "buy": [p.get("provider_name") for p in providers.get("buy") or []],
    }


def people(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    out = []
    for p in items[:limit]:
        role = p.get("job") or p.get("character")
        if not role and p.get("roles"):
            role = ", ".join(r.get("character") for r in p.get("roles")[:2] if r.get("character"))
        if not role and p.get("jobs"):
            role = ", ".join(j.get("job") for j in p.get("jobs")[:2] if j.get("job"))
        out.append({"id": p.get("id"), "name": p.get("name"), "job": role, "profile": image_url(p.get("profile_path"), "w185")})
    return out


def simplify_tmdb(raw: dict[str, Any], media_kind: str) -> dict[str, Any]:
    if media_kind == "tv_episode":
        series = raw.get("_series") or {}
        credits = raw.get("credits") or {}
        crew, cast = credits.get("crew") or [], (raw.get("guest_stars") or []) + (credits.get("cast") or [])
        directors = [x for x in crew if x.get("job") == "Director"]
        writers = [x for x in crew if x.get("job") in {"Writer", "Screenplay", "Story", "Teleplay"}]
        return {
            "id": raw.get("id"), "media_kind": "tv_episode", "series_id": raw.get("_series_id"),
            "series_name": series.get("name"), "season_number": raw.get("season_number"), "episode_number": raw.get("episode_number"),
            "imdb_id": (raw.get("external_ids") or {}).get("imdb_id"), "title": raw.get("name"), "original_title": raw.get("name"),
            "original_language": series.get("original_language"), "overview": raw.get("overview"), "tagline": None,
            "release_date": raw.get("air_date"), "runtime": raw.get("runtime"), "runtime_kind": "episode", "status": series.get("status"),
            "certification": None, "genres": [g.get("name") for g in series.get("genres") or [] if g.get("name")],
            "production_countries": [{"code": c.get("iso_3166_1"), "name": c.get("name")} for c in series.get("production_countries") or [] if c.get("iso_3166_1")],
            "spoken_languages": [{"code": l.get("iso_639_1"), "name": l.get("english_name") or l.get("name")} for l in series.get("spoken_languages") or [] if l.get("iso_639_1")],
            "production_companies": [{"id": c.get("id"), "name": c.get("name"), "country": c.get("origin_country")} for c in series.get("production_companies") or []],
            "vote_average": raw.get("vote_average"), "vote_count": raw.get("vote_count"), "popularity": series.get("popularity"),
            "poster": image_url(raw.get("still_path") or series.get("poster_path"), "w500"), "backdrop": image_url(series.get("backdrop_path"), "w1280"),
            "directors": people(directors, 8), "writers": people(writers, 10), "cast": people(cast, 18), "creators": people(series.get("created_by") or [], 8),
            "keywords": [k.get("name") for k in ((series.get("keywords") or {}).get("results") or []) if k.get("name")],
            "watch_providers": provider_data(raw), "series_type": series.get("type"),
        }

    is_tv = media_kind == "tv"
    credits = raw.get("aggregate_credits") if is_tv else raw.get("credits")
    credits = credits or {}
    crew, cast = credits.get("crew") or [], credits.get("cast") or []
    directors = [x for x in crew if x.get("job") == "Director" or any(j.get("job") == "Director" for j in x.get("jobs") or [])]
    writers = [x for x in crew if x.get("job") in {"Writer", "Screenplay", "Story", "Teleplay"} or any(j.get("job") in {"Writer", "Screenplay", "Story", "Teleplay"} for j in x.get("jobs") or [])]
    keywords_obj = raw.get("keywords") or {}
    keywords = keywords_obj.get("keywords") or keywords_obj.get("results") or []
    certification = None
    if is_tv:
        rows = (raw.get("content_ratings") or {}).get("results") or []
        certification = next((x.get("rating") for x in rows if x.get("iso_3166_1") == TMDB_REGION and x.get("rating")), None)
    else:
        release_rows = (raw.get("release_dates") or {}).get("results") or []
        regional_release = next((x for x in release_rows if x.get("iso_3166_1") == TMDB_REGION), {})
        regional_dates = regional_release.get("release_dates") or []
        certification = next((x.get("certification") for x in regional_dates if x.get("type") == 3 and x.get("certification")), None)
        certification = certification or next((x.get("certification") for x in regional_dates if x.get("certification")), None)

    episode_runtime = None
    total_runtime = None
    runtime_kind = "movie"
    if is_tv:
        runtimes = [to_int(x) for x in raw.get("episode_run_time") or [] if to_int(x)]
        episode_runtime = runtimes[0] if runtimes else None
        if norm(raw.get("type")) in {"miniseries", "mini series"} and episode_runtime and raw.get("number_of_episodes"):
            total_runtime = episode_runtime * int(raw.get("number_of_episodes"))
            runtime_kind = "approx_total"
        else:
            runtime_kind = "episode_typical"
    runtime = total_runtime or episode_runtime if is_tv else raw.get("runtime")

    return {
        "id": raw.get("id"), "media_kind": media_kind,
        "imdb_id": (raw.get("external_ids") or {}).get("imdb_id") or raw.get("imdb_id"),
        "title": raw.get("name") if is_tv else raw.get("title"), "original_title": raw.get("original_name") if is_tv else raw.get("original_title"),
        "original_language": raw.get("original_language"), "overview": raw.get("overview"), "tagline": raw.get("tagline"),
        "release_date": raw.get("first_air_date") if is_tv else raw.get("release_date"), "runtime": runtime,
        "runtime_kind": runtime_kind, "episode_runtime": episode_runtime, "number_of_episodes": raw.get("number_of_episodes") if is_tv else None,
        "number_of_seasons": raw.get("number_of_seasons") if is_tv else None, "series_type": raw.get("type") if is_tv else None,
        "status": raw.get("status"), "adult": raw.get("adult"), "certification": certification,
        "genres": [g.get("name") for g in raw.get("genres") or [] if g.get("name")],
        "production_countries": [{"code": c.get("iso_3166_1"), "name": c.get("name")} for c in raw.get("production_countries") or [] if c.get("iso_3166_1")],
        "spoken_languages": [{"code": l.get("iso_639_1"), "name": l.get("english_name") or l.get("name")} for l in raw.get("spoken_languages") or [] if l.get("iso_639_1")],
        "production_companies": [{"id": c.get("id"), "name": c.get("name"), "country": c.get("origin_country")} for c in raw.get("production_companies") or []],
        "collection": (raw.get("belongs_to_collection") or {}).get("name") if not is_tv else None,
        "budget": raw.get("budget") or 0, "revenue": raw.get("revenue") or 0,
        "vote_average": raw.get("vote_average"), "vote_count": raw.get("vote_count"), "popularity": raw.get("popularity"),
        "poster": image_url(raw.get("poster_path"), "w500"), "backdrop": image_url(raw.get("backdrop_path"), "w1280"),
        "directors": people(directors, 8), "writers": people(writers, 10), "cast": people(cast, 18),
        "creators": people(raw.get("created_by") or [], 8) if is_tv else [],
        "keywords": [k.get("name") for k in keywords if k.get("name")], "watch_providers": provider_data(raw),
    }

def enrich(catalog: dict[str, dict[str, Any]]) -> tuple[int, int, list[dict[str, Any]]]:
    cache = load_cache()
    overrides = load_overrides()
    media_overrides = load_media_type_overrides()
    matched = 0
    attempted = 0
    unresolved = []

    for index, film in enumerate(sorted(catalog.values(), key=lambda f: (f.get("year") or 0, f.get("name") or "")), start=1):
        cache_key = film["key"]
        manual = media_overrides.get(cache_key) or overrides.get(cache_key)
        cached = cache.get(cache_key) if cache_key not in overrides and cache_key not in media_overrides else None
        if cached:
            cached_at = cached.get("cached_at")
            fresh = False
            if cached_at:
                try:
                    age = datetime.now(timezone.utc) - datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
                    fresh = age.days < CACHE_DAYS
                except ValueError:
                    pass
            cache_version = to_int(cached.get("schema_version")) or 1
            if (fresh and cache_version >= CACHE_SCHEMA_VERSION) or not TOKEN:
                film["tmdb"] = cached.get("tmdb")
                film["match"] = cached.get("match", film["match"])
                if cached.get("media_type"):
                    set_media_type(film, cached.get("media_type"), cached.get("media_type_source") or "cache")
                elif film["tmdb"]:
                    set_media_type(film, classify_from_tmdb(film["tmdb"]), "tmdb-cache")
                if film["tmdb"]: matched += 1
                continue

        if not TOKEN:
            if manual and manual.get("media_type"):
                set_media_type(film, manual["media_type"], "manual")
            film["match"] = {"status": "skipped", "confidence": None, "reason": "TMDB token not configured"}
            continue

        attempted += 1
        try:
            override = manual or {}
            if override.get("tmdb_media_type") == "tv_episode" or override.get("media_type") == "tv_episode":
                media_kind = "tv_episode"
                entity_id = override.get("tmdb_id") or 0
                confidence, reason = 1.0, "manual-tv-episode"
            elif override.get("tmdb_id"):
                entity_id = override["tmdb_id"]
                media_kind = override.get("tmdb_media_type") or ("tv" if override.get("media_type") in {"limited_series", "tv_series"} else "movie")
                confidence, reason = 1.0, "manual-override"
            else:
                entity_id, media_kind, confidence, reason = tmdb_search(film["name"], film["year"])
            if not entity_id and media_kind != "tv_episode":
                film["match"] = {"status": "unresolved", "confidence": confidence, "reason": reason}
                if override.get("media_type"):
                    set_media_type(film, override["media_type"], "manual")
                unresolved.append({"name": film["name"], "year": film["year"], "letterboxd_uri": film["letterboxd_uri"], "reason": reason})
            else:
                raw = tmdb_details(int(entity_id or 0), media_kind, override)
                film["tmdb"] = simplify_tmdb(raw, media_kind)
                set_media_type(film, override.get("media_type") or classify_from_tmdb(film["tmdb"]), "manual" if override.get("media_type") else "tmdb")
                film["match"] = {"status": "matched", "confidence": confidence, "reason": reason}
                matched += 1
        except Exception as exc:
            film["match"] = {"status": "error", "confidence": None, "reason": str(exc)[:240]}
            if manual and manual.get("media_type"):
                set_media_type(film, manual["media_type"], "manual")
            unresolved.append({"name": film["name"], "year": film["year"], "letterboxd_uri": film["letterboxd_uri"], "reason": str(exc)[:240]})

        cache[cache_key] = {
            "tmdb": film["tmdb"], "match": film["match"], "media_type": film.get("media_type"),
            "media_type_source": film.get("media_type_source"), "cached_at": utc_now(), "schema_version": CACHE_SCHEMA_VERSION,
        }
        if index % 20 == 0:
            save_cache(cache); print(f"Processed {index}/{len(catalog)} titles...", file=sys.stderr)

    save_cache(cache)
    return matched, attempted, unresolved

def build_facets(films: list[dict[str, Any]]) -> dict[str, Any]:
    counters: dict[str, Counter] = {
        "genres": Counter(),
        "writers": Counter(),
        "companies": Counter(),
        "certifications": Counter(),
        "countries": Counter(),
        "languages": Counter(),
        "directors": Counter(),
        "cast": Counter(),
        "keywords": Counter(),
        "tags": Counter(),
        "providers": Counter(),
        "media_types": Counter(),
    }
    years = []
    runtimes = []
    for film in films:
        counters["media_types"].update([film.get("media_type") or "unknown"])
        if film.get("year"):
            years.append(film["year"])
        tmdb = film.get("tmdb") or {}
        if tmdb.get("runtime"):
            runtimes.append(tmdb["runtime"])
        counters["genres"].update(tmdb.get("genres") or [])
        counters["writers"].update(p.get("name") for p in tmdb.get("writers") or [] if p.get("name"))
        counters["companies"].update(c.get("name") for c in tmdb.get("production_companies") or [] if c.get("name"))
        if tmdb.get("certification"):
            counters["certifications"].update([tmdb["certification"]])
        counters["countries"].update(c.get("code") for c in tmdb.get("production_countries") or [] if c.get("code"))
        counters["languages"].update(l.get("code") for l in tmdb.get("spoken_languages") or [] if l.get("code"))
        counters["directors"].update(p.get("name") for p in tmdb.get("directors") or [] if p.get("name"))
        counters["cast"].update(p.get("name") for p in tmdb.get("cast") or [] if p.get("name"))
        counters["keywords"].update(tmdb.get("keywords") or [])
        counters["tags"].update((film.get("user") or {}).get("tags") or [])
        providers = tmdb.get("watch_providers") or {}
        for bucket in ("flatrate", "free", "ads", "rent", "buy"):
            counters["providers"].update(providers.get(bucket) or [])
    return {
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "runtime_max": max(runtimes) if runtimes else None,
        **{name: [{"value": k, "count": v} for k, v in counter.most_common()] for name, counter in counters.items()},
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    community_report = update_letterboxd_community()
    catalog = merge_letterboxd()
    matched, attempted, unresolved = enrich(catalog)
    catalog = dedupe_after_enrichment(catalog)
    matched = sum(1 for f in catalog.values() if f.get("tmdb"))
    films = sorted(catalog.values(), key=lambda f: ((f.get("name") or "").casefold(), f.get("year") or 0))
    media_type_counts = Counter(f.get("media_type") or "unknown" for f in films)
    payload = {
        "meta": {
            "generated_at": utc_now(),
            "film_count": len(films),
            "tmdb_enabled": bool(TOKEN),
            "tmdb_region": TMDB_REGION,
            "tmdb_language": TMDB_LANGUAGE,
            "matched_count": matched,
            "unresolved_count": len(unresolved),
            "schema_version": 2,
            "media_type_schema": "feature_film|short_film|limited_series|tv_series|tv_episode|unknown",
            "media_type_counts": dict(media_type_counts),
        },
        "facets": build_facets(films),
        "films": films,
    }
    OUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_FILE.write_text(json.dumps({
        "generated_at": payload["meta"]["generated_at"],
        "films": len(films),
        "tmdb_attempted": attempted,
        "tmdb_matched": matched,
        "media_type_counts": dict(media_type_counts),
        "letterboxd_community": community_report,
        "unresolved": unresolved,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Letterboxd community: {community_report['fresh_reused']} reused, "
        f"{community_report['requested']} requested, {community_report['successful']} updated."
    )
    print(f"Wrote {OUT_FILE} with {len(films)} films; TMDB matches: {matched}.")
    if unresolved:
        print(f"Review {REPORT_FILE} for {len(unresolved)} unresolved matches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
