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
OUT_FILE = OUT / "catalog.json"
REPORT_FILE = OUT / "build-report.json"

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE = "https://image.tmdb.org/t/p"
TOKEN = os.environ.get("TMDB_API_TOKEN", "").strip()
TMDB_LANGUAGE = os.environ.get("TMDB_LANGUAGE", "en-US")
TMDB_REGION = os.environ.get("TMDB_REGION", "DE")
REQUEST_DELAY = float(os.environ.get("TMDB_REQUEST_DELAY", "0.12"))
CACHE_DAYS = int(os.environ.get("TMDB_CACHE_DAYS", "30"))
LETTERBOXD_TIMEOUT = float(os.environ.get("LETTERBOXD_TIMEOUT", "20"))


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
        for row in rows:
            writer.writerow(row)


def normalized_letterboxd_uri(row: dict[str, str]) -> str:
    """Return a stable Letterboxd URI identity when one is present."""
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
    return normalized_letterboxd_uri(row) or title_year_key(row)


def film_slug_from_uri(uri: str) -> str | None:
    if not uri:
        return None
    parsed = urllib.parse.urlparse(uri)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "film":
        return parts[1]
    return None


def fetch_url(url: str) -> tuple[str, dict[str, str]]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=LETTERBOXD_TIMEOUT) as resp:
        body = resp.read().decode("utf-8", "ignore")
        headers = {k.lower(): v for k, v in resp.headers.items()}
    return body, headers


def parse_letterboxd_stats(payload: dict[str, Any]) -> dict[str, Any] | None:
    counts = payload.get("counts") or {}
    rating = payload.get("rating")
    if counts.get("watches") is None and counts.get("likes") is None and rating is None:
        return None
    return {
        "average_rating": rating,
        "watches": counts.get("watches"),
        "likes": counts.get("likes"),
    }


def parse_compact_number(text: str | None) -> int | None:
    if not text:
        return None
    value = text.strip().upper().replace(",", "")
    m = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([KMB])?", value)
    if not m:
        return to_int(value)
    num = float(m.group(1))
    suffix = m.group(2)
    if suffix == "K":
        num *= 1_000
    elif suffix == "M":
        num *= 1_000_000
    elif suffix == "B":
        num *= 1_000_000_000
    return int(num)


def scrape_letterboxd_page(uri: str) -> dict[str, Any] | None:
    try:
        html, _ = fetch_url(uri)
    except Exception:
        return None

    rating = None
    m = re.search(r'<meta name="twitter:data2" content="([0-9.]+) out of 5">', html)
    if m:
        rating = to_float(m.group(1))
    if rating is None:
        m = re.search(r'"ratingValue"\s*:\s*([0-9.]+)', html)
        if m:
            rating = to_float(m.group(1))

    watches = None
    m = re.search(r'"ratingCount"\s*:\s*([0-9]+)', html)
    if m:
        watches = to_int(m.group(1))

    likes = None
    m = re.search(r'>([0-9][0-9.,]*[KMB]?)\s+fans<', html)
    if m:
        likes = parse_compact_number(m.group(1))

    if rating is None and watches is None and likes is None:
        return None
    return {"average_rating": rating, "watches": watches, "likes": likes}


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


def dedupe_after_enrichment(catalog: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Final safety net: one TMDB movie can never produce two cards."""
    out: dict[str, dict[str, Any]] = {}
    tmdb_to_key: dict[int, str] = {}
    fallback_to_key: dict[str, str] = {}

    for film in catalog.values():
        tmdb_id = to_int((film.get("tmdb") or {}).get("id"))
        fallback = f"{norm(film.get('name'))}::{film.get('year') or ''}"
        existing_key = tmdb_to_key.get(tmdb_id) if tmdb_id else fallback_to_key.get(fallback)
        if existing_key and existing_key in out:
            merge_user_records(out[existing_key], film)
            continue
        key = film["key"]
        out[key] = film
        if tmdb_id:
            tmdb_to_key[tmdb_id] = key
        if fallback.strip(":"):
            fallback_to_key[fallback] = key
    return out


def scrape_letterboxd_community(films: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique_films: list[dict[str, Any]] = []
    seen: set[str] = set()
    for film in films:
        uri = (film.get("letterboxd_uri") or "").strip()
        if not uri or uri in seen:
            continue
        seen.add(uri)
        unique_films.append(film)

    rows: list[dict[str, Any]] = []
    max_workers = min(16, max(4, (os.cpu_count() or 4)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {
            pool.submit(scrape_letterboxd_page, (film.get("letterboxd_uri") or "").strip()): film
            for film in unique_films
        }
        for future in as_completed(future_map):
            film = future_map[future]
            try:
                stats = future.result()
            except Exception:
                stats = None
            if not stats:
                continue
            rows.append(
                {
                    "Letterboxd URI": (film.get("letterboxd_uri") or "").strip(),
                    "Name": film.get("name") or "",
                    "Year": film.get("year") or "",
                    "Average Rating": stats.get("average_rating"),
                    "Watches": stats.get("watches"),
                    "Likes": stats.get("likes"),
                    "Updated": utc_now(),
                }
            )

    rows.sort(key=lambda r: ((r.get("Name") or "").casefold(), int(r.get("Year") or 0), r.get("Letterboxd URI") or ""))
    return rows


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
        "letterboxd_community": None,
        "match": {"status": "not-attempted", "confidence": None, "reason": None},
    }


def get_or_create(catalog: dict[str, dict[str, Any]], aliases: dict[str, str], row: dict[str, str]) -> dict[str, Any]:
    uri_alias = normalized_letterboxd_uri(row)
    ty_alias = title_year_key(row)
    alias_candidates = [a for a in (uri_alias, ty_alias) if a]
    canonical_key = next((aliases[a] for a in alias_candidates if a in aliases), None)
    if canonical_key is None:
        canonical_key = uri_alias or ty_alias or f"unknown::{len(catalog)}"
        catalog[canonical_key] = base_film(row)
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
                film["user"]["watched"] = True
        else:
            index_film(film["key"], film, row)
            if not film.get("letterboxd_uri"):
                film["letterboxd_uri"] = (row.get("Letterboxd URI") or "").strip()
            if not film.get("year"):
                film["year"] = to_int(row.get("Year"))
        return film

    for row in read_csv("watched.csv"):
        f = attach(row)
        f["user"]["watched"] = True

    for row in read_csv("ratings.csv"):
        f = attach(row, subset_of_watched=True)
        f["user"]["rating"] = to_float(row.get("Rating"))
        f["user"]["rating_date"] = (row.get("Date") or "").strip() or None

    for row in read_csv("diary.csv"):
        f = attach(row, subset_of_watched=True)
        f["user"]["watched"] = True
        watched_date = (row.get("Watched Date") or "").strip() or None
        entry = {
            "entry_date": (row.get("Date") or "").strip() or None,
            "watched_date": watched_date,
            "rating": to_float(row.get("Rating")),
            "rewatch": as_bool(row.get("Rewatch")),
            "tags": split_tags(row.get("Tags")),
        }
        f["user"]["diary_entries"].append(entry)

    for row in read_csv("watchlist.csv"):
        f = attach(row, subset_of_watched=False)
        f["user"]["watchlist"] = True
        f["user"]["watchlist_added_date"] = (row.get("Date") or "").strip() or None

    for film in catalog.values():
        u = film["user"]
        entries = u["diary_entries"]
        entries.sort(key=lambda x: x.get("watched_date") or x.get("entry_date") or "")
        u["watch_count"] = len(entries) if entries else (1 if u["watched"] else 0)
        u["rewatch_count"] = sum(1 for e in entries if e["rewatch"])
        watched_dates = [e["watched_date"] for e in entries if e.get("watched_date")]
        if watched_dates:
            u["first_watched"] = min(watched_dates)
            u["last_watched"] = max(watched_dates)
        tag_counter = Counter(t for e in entries for t in e["tags"])
        u["tags"] = [t for t, _ in tag_counter.most_common()]

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


def load_overrides() -> dict[str, int]:
    overrides: dict[str, int] = {}
    for row in read_csv("tmdb_overrides.csv"):
        key = film_key(row)
        tmdb_id = to_int(row.get("TMDB ID"))
        if key and tmdb_id:
            overrides[key] = tmdb_id
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


def choose_search_result(name: str, year: int | None, results: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float, str]:
    if not results:
        return None, 0.0, "no-results"
    target = norm(name)
    scored: list[tuple[float, dict[str, Any], str]] = []
    for candidate in results[:12]:
        title = norm(candidate.get("title"))
        original = norm(candidate.get("original_title"))
        c_year = year_from_date(candidate.get("release_date"))
        score = 0.0
        reasons = []
        if target and target == title:
            score += 0.68
            reasons.append("exact-title")
        elif target and target == original:
            score += 0.64
            reasons.append("exact-original-title")
        elif target and (target in title or title in target):
            score += 0.48
            reasons.append("close-title")
        else:
            # Lightweight token overlap for translated titles.
            a, b = set(target.split()), set(title.split())
            if a and b:
                overlap = len(a & b) / max(len(a), len(b))
                score += min(0.42, overlap * 0.42)
                if overlap >= 0.6:
                    reasons.append("token-overlap")
        if year and c_year:
            diff = abs(year - c_year)
            if diff == 0:
                score += 0.28
                reasons.append("exact-year")
            elif diff == 1:
                score += 0.16
                reasons.append("near-year")
            elif diff >= 3:
                score -= 0.15
        score += min(0.04, float(candidate.get("vote_count") or 0) / 250000)
        scored.append((score, candidate, "+".join(reasons) or "weak-match"))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1], round(scored[0][0], 3), scored[0][2]


def tmdb_search(name: str, year: int | None) -> tuple[int | None, float, str]:
    params = {"query": name, "include_adult": "false", "language": TMDB_LANGUAGE}
    if year:
        params["primary_release_year"] = year
    payload = tmdb_get("/search/movie", params)
    result, confidence, reason = choose_search_result(name, year, payload.get("results") or [])
    if not result and year:
        params.pop("primary_release_year", None)
        payload = tmdb_get("/search/movie", params)
        result, confidence, reason = choose_search_result(name, year, payload.get("results") or [])
    if not result:
        return None, confidence, reason
    # Avoid silently attaching very weak matches.
    if confidence < 0.55:
        return None, confidence, "low-confidence:" + reason
    return int(result["id"]), confidence, reason


def tmdb_details(movie_id: int) -> dict[str, Any]:
    details = tmdb_get(
        f"/movie/{movie_id}",
        {
            "language": TMDB_LANGUAGE,
            "append_to_response": "credits,keywords,external_ids,release_dates",
        },
    )
    providers = tmdb_get(f"/movie/{movie_id}/watch/providers")
    details["watch_providers"] = providers
    return details


def image_url(path: str | None, size: str) -> str | None:
    return f"{TMDB_IMAGE}/{size}{path}" if path else None


def simplify_tmdb(raw: dict[str, Any]) -> dict[str, Any]:
    credits = raw.get("credits") or {}
    crew = credits.get("crew") or []
    cast = credits.get("cast") or []
    directors = [x for x in crew if x.get("job") == "Director"]
    writers = [x for x in crew if x.get("job") in {"Writer", "Screenplay", "Story", "Teleplay"}]
    keywords_obj = raw.get("keywords") or {}
    keywords = keywords_obj.get("keywords") or keywords_obj.get("results") or []
    providers = ((raw.get("watch_providers") or {}).get("results") or {}).get(TMDB_REGION, {})
    release_rows = (raw.get("release_dates") or {}).get("results") or []
    regional_release = next((x for x in release_rows if x.get("iso_3166_1") == TMDB_REGION), {})
    regional_dates = regional_release.get("release_dates") or []
    # Prefer theatrical certification, then the first non-empty certification available.
    certification = next((x.get("certification") for x in regional_dates if x.get("type") == 3 and x.get("certification")), None)
    if not certification:
        certification = next((x.get("certification") for x in regional_dates if x.get("certification")), None)

    def people(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        out = []
        for p in items[:limit]:
            out.append({
                "id": p.get("id"),
                "name": p.get("name"),
                "job": p.get("job") or p.get("character"),
                "profile": image_url(p.get("profile_path"), "w185"),
            })
        return out

    return {
        "id": raw.get("id"),
        "imdb_id": (raw.get("external_ids") or {}).get("imdb_id") or raw.get("imdb_id"),
        "title": raw.get("title"),
        "original_title": raw.get("original_title"),
        "original_language": raw.get("original_language"),
        "overview": raw.get("overview"),
        "tagline": raw.get("tagline"),
        "release_date": raw.get("release_date"),
        "runtime": raw.get("runtime"),
        "status": raw.get("status"),
        "adult": raw.get("adult"),
        "certification": certification,
        "genres": [g.get("name") for g in raw.get("genres") or [] if g.get("name")],
        "production_countries": [
            {"code": c.get("iso_3166_1"), "name": c.get("name")}
            for c in raw.get("production_countries") or [] if c.get("iso_3166_1")
        ],
        "spoken_languages": [
            {"code": l.get("iso_639_1"), "name": l.get("english_name") or l.get("name")}
            for l in raw.get("spoken_languages") or [] if l.get("iso_639_1")
        ],
        "production_companies": [
            {"id": c.get("id"), "name": c.get("name"), "country": c.get("origin_country")}
            for c in raw.get("production_companies") or []
        ],
        "collection": (raw.get("belongs_to_collection") or {}).get("name"),
        "budget": raw.get("budget") or 0,
        "revenue": raw.get("revenue") or 0,
        "vote_average": raw.get("vote_average"),
        "vote_count": raw.get("vote_count"),
        "popularity": raw.get("popularity"),
        "poster": image_url(raw.get("poster_path"), "w500"),
        "backdrop": image_url(raw.get("backdrop_path"), "w1280"),
        "directors": people(directors, 8),
        "writers": people(writers, 10),
        "cast": people(cast, 18),
        "keywords": [k.get("name") for k in keywords if k.get("name")],
        "watch_providers": {
            "region": TMDB_REGION,
            "link": providers.get("link"),
            "flatrate": [p.get("provider_name") for p in providers.get("flatrate") or []],
            "free": [p.get("provider_name") for p in providers.get("free") or []],
            "ads": [p.get("provider_name") for p in providers.get("ads") or []],
            "rent": [p.get("provider_name") for p in providers.get("rent") or []],
            "buy": [p.get("provider_name") for p in providers.get("buy") or []],
        },
    }


def enrich(catalog: dict[str, dict[str, Any]]) -> tuple[int, int, list[dict[str, Any]]]:
    cache = load_cache()
    overrides = load_overrides()
    matched = 0
    attempted = 0
    unresolved = []

    for index, film in enumerate(sorted(catalog.values(), key=lambda f: (f.get("year") or 0, f.get("name") or "")), start=1):
        cache_key = film["key"]
        if cache_key in cache and cache_key not in overrides:
            cached = cache[cache_key]
            cached_at = cached.get("cached_at")
            fresh = False
            if cached_at:
                try:
                    age = datetime.now(timezone.utc) - datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
                    fresh = age.days < CACHE_DAYS
                except ValueError:
                    fresh = False
            # If no token is configured, keep using any available cached metadata.
            if fresh or not TOKEN:
                film["tmdb"] = cached.get("tmdb")
                film["match"] = cached.get("match", film["match"])
                if film["tmdb"]:
                    matched += 1
                continue

        if not TOKEN:
            film["match"] = {"status": "skipped", "confidence": None, "reason": "TMDB token not configured"}
            continue

        attempted += 1
        try:
            if cache_key in overrides:
                tmdb_id = overrides[cache_key]
                confidence, reason = 1.0, "manual-override"
            else:
                tmdb_id, confidence, reason = tmdb_search(film["name"], film["year"])
            if not tmdb_id:
                film["match"] = {"status": "unresolved", "confidence": confidence, "reason": reason}
                unresolved.append({"name": film["name"], "year": film["year"], "letterboxd_uri": film["letterboxd_uri"], "reason": reason})
            else:
                raw = tmdb_details(tmdb_id)
                film["tmdb"] = simplify_tmdb(raw)
                film["match"] = {"status": "matched", "confidence": confidence, "reason": reason}
                matched += 1
        except Exception as exc:  # Keep the rest of the catalog usable if one title fails.
            film["match"] = {"status": "error", "confidence": None, "reason": str(exc)[:240]}
            unresolved.append({"name": film["name"], "year": film["year"], "letterboxd_uri": film["letterboxd_uri"], "reason": str(exc)[:240]})

        cache[cache_key] = {"tmdb": film["tmdb"], "match": film["match"], "cached_at": utc_now()}
        if index % 20 == 0:
            save_cache(cache)
            print(f"Processed {index}/{len(catalog)} films...", file=sys.stderr)

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
    }
    years = []
    runtimes = []
    for film in films:
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
    catalog = merge_letterboxd()
    community_rows = scrape_letterboxd_community(sorted(catalog.values(), key=lambda f: ((f.get("name") or "").casefold(), f.get("year") or 0)))
    write_csv(
        DATA / "letterboxd_community.csv",
        community_rows,
        ["Letterboxd URI", "Name", "Year", "Average Rating", "Watches", "Likes", "Updated"],
    )
    matched, attempted, unresolved = enrich(catalog)
    catalog = dedupe_after_enrichment(catalog)
    matched = sum(1 for f in catalog.values() if f.get("tmdb"))
    films = sorted(catalog.values(), key=lambda f: ((f.get("name") or "").casefold(), f.get("year") or 0))
    payload = {
        "meta": {
            "generated_at": utc_now(),
            "film_count": len(films),
            "tmdb_enabled": bool(TOKEN),
            "tmdb_region": TMDB_REGION,
            "tmdb_language": TMDB_LANGUAGE,
            "matched_count": matched,
            "unresolved_count": len(unresolved),
            "schema_version": 1,
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
        "unresolved": unresolved,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_FILE} with {len(films)} films; TMDB matches: {matched}.")
    if unresolved:
        print(f"Review {REPORT_FILE} for {len(unresolved)} unresolved matches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
