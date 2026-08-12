# Letterboxd data folder

Replace the four placeholder CSV files with the matching files from your Letterboxd export. Keep the original filenames.

Required columns used by this project:

- `diary.csv`: `Date`, `Name`, `Year`, `Letterboxd URI`, `Rating`, `Rewatch`, `Tags`, `Watched Date`
- `ratings.csv`: `Date`, `Name`, `Year`, `Letterboxd URI`, `Rating`
- `watched.csv`: `Date`, `Name`, `Year`, `Letterboxd URI`
- `watchlist.csv`: `Date`, `Name`, `Year`, `Letterboxd URI`

## Optional: TMDB match overrides

If a title is matched to the wrong TMDB film, add a row to `tmdb_overrides.csv`:

```csv
Letterboxd URI,Name,Year,TMDB ID
https://letterboxd.com/film/example/,Example,2020,12345
```

The Letterboxd URI is the preferred key. The next build will use that TMDB ID with 100% match confidence.

## Optional: Letterboxd community statistics

`letterboxd_community.csv` is deliberately manual/optional. It can contain:

```csv
Letterboxd URI,Name,Year,Average Rating,Watches,Likes,Updated
```

This project does **not** scrape Letterboxd to fill those fields. If you have an authorized source for community averages/watch counts, put them here and they will appear in film detail views.
