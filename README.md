# My Film Atlas

A static, GitHub Pages-ready personal film explorer built from a Letterboxd data export and enriched at build time with TMDB.

It is designed for a simple maintenance loop: replace your Letterboxd CSV files, push to GitHub, and let GitHub Actions rebuild the site.

## What is included

### Explore

- Search across titles, overviews, directors, writers, cast, countries, languages, genres, keywords and your own tags
- Collection filters for all films, watched, diary and watchlist
- Release-year range
- Runtime range
- Your Letterboxd rating range
- Format filtering everywhere: feature film, short film, limited series, TV series, TV episode, unknown
- TMDB rating and vote-count thresholds
- Rewatch filtering
- Watchlist-added and last-watched date filters
- Multi-select filters for genres, countries, languages, directors, cast, keywords, diary tags and streaming/provider names
- Sorting by title, release year, personal rating, TMDB rating, TMDB votes, popularity, runtime, recent watching and watchlist age
- Poster grid and compact list views
- Saved filter presets in browser localStorage
- Random / “Surprise me” picker from the current filtered set
- CSV export of any filtered result set
- Film detail dialog with personal history, posters, backdrop, cast/crew, countries, languages, keywords, external links and availability data

### Statistics

- Watched / diary / watchlist totals
- Average personal rating
- Logged screen time where diary + runtime metadata are available
- Viewing activity by year
- Personal rating distribution
- Genre, decade and runtime distributions
- Most represented directors and actors
- Country and original-language rankings
- Rewatch indicators
- Metadata coverage
- Watchlist age, shortest watchlist films and highly rated watchlist candidates
- A compact “taste fingerprint” based on your own library

### Map

- Interactive world choropleth based on production-country metadata
- Separate views for watched, watchlist and the full collection
- Click a country to open your library already filtered to that country
- Top-country and original-language rankings
- List of countries with no current representation in the selected scope

## Project structure

```text
.
├── index.html
├── statistics.html
├── map.html
├── about.html
├── assets/
│   ├── css/styles.css
│   ├── js/
│   │   ├── core.js
│   │   ├── explore.js
│   │   ├── statistics.js
│   │   ├── map.js
│   │   ├── about.js
│   │   └── iso-numeric-map.js
│   ├── img/
│   └── data/
│       ├── catalog.json
│       ├── build-report.json
│       └── config.json
├── data/
│   ├── diary.csv
│   ├── ratings.csv
│   ├── watched.csv
│   ├── watchlist.csv
│   ├── tmdb_overrides.csv
│   ├── media_type_overrides.csv
│   └── letterboxd_community.csv
├── scripts/enrich.py
└── .github/workflows/deploy-pages.yml
```

## 1. Add your Letterboxd CSV files

Replace these four placeholder files in `data/` with the files from your export:

- `diary.csv`
- `ratings.csv`
- `watched.csv`
- `watchlist.csv`

The build script uses only the columns documented in `data/README.md`, so extra Letterboxd columns do not hurt anything.

## 2. Create a TMDB API credential

Create a TMDB account and request an API credential from the API section of your TMDB account settings. This project expects the **API Read Access Token** (Bearer token), not a token embedded in browser JavaScript.

In GitHub:

1. Open your repository.
2. Go to **Settings → Secrets and variables → Actions**.
3. Choose **New repository secret**.
4. Name it exactly `TMDB_API_TOKEN`.
5. Paste your TMDB API Read Access Token.

The token is used only inside GitHub Actions and is never written to the generated catalog or client-side code.

If you do not configure a token, the site still builds with your Letterboxd data, but TMDB fields such as runtime, genres, countries, cast and posters remain empty.

## 3. Enable GitHub Pages

1. Push the project to the `main` branch of a GitHub repository.
2. Open **Settings → Pages**.
3. Set **Source** to **GitHub Actions**.
4. Open the **Actions** tab and run **Build and deploy My Film Atlas** if it has not already started.

The workflow publishes a `dist` artifact containing only the site and generated public catalog; it does not copy the raw `data/*.csv` files into the Pages artifact.

The workflow also runs once a week. TMDB records are cached for 30 days, so metadata and provider information can refresh without repeatedly refetching the entire library.

## 4. Customize the site

Edit `assets/data/config.json`:

```json
{
  "siteTitle": "My Film Atlas",
  "displayName": "My cinema",
  "tagline": "A personal map of what I have seen, loved, revisited, and still want to discover.",
  "defaultCollection": "all",
  "region": "DE",
  "itemsPerPage": 48,
  "showAdultTitles": false
}
```

The GitHub workflow currently sets `TMDB_REGION=DE` and `TMDB_LANGUAGE=en-US`. Change those environment values in `.github/workflows/deploy-pages.yml` if you want another region or metadata language.

## 5. Fix a wrong TMDB match

The script searches TMDB movie and TV results and applies a conservative title/year confidence score. It also stores an explicit media format for each catalog entry. Ambiguous/unmatched titles are listed in:

`assets/data/build-report.json`

For any wrong match, find the correct TMDB movie ID and add it to `data/tmdb_overrides.csv`. The override will be used on the next build.

## Local build and preview

You can build without any API token:

```bash
python scripts/enrich.py
python -m http.server 8000
```

Then open `http://localhost:8000`.

For local TMDB enrichment on macOS/Linux:

```bash
export TMDB_API_TOKEN="your-read-access-token"
python scripts/enrich.py
python -m http.server 8000
```

Do not commit your token.

## Letterboxd community ratings / watches

Your **personal** Letterboxd ratings are imported automatically from `ratings.csv`.

Community-wide Letterboxd averages, watch counts and likes are not automatically scraped. Letterboxd currently makes official API access request-only and states that it is not granting access for personal/private projects or data-analysis, visualization and recommendation projects. The optional `data/letterboxd_community.csv` file exists so you can add those fields later from an authorized source without changing the website. The Explore page can filter watch counts as a minimum and maximum range.

## TMDB and JustWatch attribution

The About page includes the required TMDB notice and links to TMDB. It also credits JustWatch for provider availability data returned by TMDB.

This project uses an approved TMDB logo hosted on TMDB's own domain. If TMDB changes that asset URL in the future, replace it with a currently approved logo from TMDB's Logos & Attribution page.

## Privacy warning

The deployed Pages artifact excludes the raw CSV exports, but the generated `catalog.json` necessarily contains the personal fields shown on the website, including viewing history, ratings and tags.

Also, if the **GitHub repository itself is public**, any CSV files you commit to `data/` are publicly readable from the repository even though the deployment workflow does not copy them into the Pages artifact. Remove anything sensitive or use an appropriate private-repository/hosting setup if required.

## External libraries loaded in the browser

- Chart.js for statistics visualizations
- Leaflet for the interactive map
- world-atlas + topojson-client for country geometry

The core site and your catalog are otherwise static HTML/CSS/JavaScript.
