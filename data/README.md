# Letterboxd data folder

Replace the four placeholder CSV files with the matching files from your Letterboxd export. Keep the original filenames.

Required columns used by this project:

- `diary.csv`: `Date`, `Name`, `Year`, `Letterboxd URI`, `Rating`, `Rewatch`, `Tags`, `Watched Date`
- `ratings.csv`: `Date`, `Name`, `Year`, `Letterboxd URI`, `Rating`
- `watched.csv`: `Date`, `Name`, `Year`, `Letterboxd URI`
- `watchlist.csv`: `Date`, `Name`, `Year`, `Letterboxd URI`

## Optional: TMDB match overrides

If a title is matched to the wrong TMDB movie or TV series, add a row to `tmdb_overrides.csv`. The old four-column format still works; you may optionally add `TMDB Media Type` (`movie` or `tv`) and `Media Type`:

```csv
Letterboxd URI,Name,Year,TMDB ID,TMDB Media Type,Media Type
https://letterboxd.com/film/example/,Example,2020,12345,movie,feature_film
```

The Letterboxd URI is the preferred key. The next build will use that TMDB ID with 100% match confidence.

## Film / short / series / episode classification

The generated catalog now stores one of these values for every title:

- `feature_film`
- `short_film`
- `limited_series`
- `tv_series`
- `tv_episode`
- `unknown`

The builder searches TMDB's mixed movie/TV search. Movies are classified as shorts when their TMDB runtime is 40 minutes or less (configurable with `SHORT_FILM_MAX_MINUTES`); otherwise they are feature films. TV entries whose TMDB series type is `Miniseries` become limited series. Other TV-show matches become TV series.

TMDB does not offer general episode-title search through its movie/TV search endpoints, so standalone Letterboxd episode entries can need a precise manual row in `media_type_overrides.csv`:

```csv
Letterboxd URI,Name,Year,Media Type,TMDB Media Type,TMDB ID,Series ID,Season,Episode
https://letterboxd.com/film/example-episode/,Example Episode,2022,tv_episode,tv_episode,,12345,1,4
```

For a known limited series or TV series you can also force only the classification, or include a TMDB ID for an exact match. This override file is intentionally separate from your Letterboxd export so it survives future CSV replacements.

## Optional: Letterboxd community statistics

`letterboxd_community.csv` is deliberately manual/optional. It can contain:

```csv
Letterboxd URI,Name,Year,Average Rating,Watches,Likes,Updated
```

This project does **not** scrape Letterboxd to fill those fields. If you have an authorized source for community averages/watch counts, put them here and they will appear in film detail views.
