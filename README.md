# OSS Insights Tracker

Track analytics for multiple open source GitHub repositories using GitHub Actions.

This project stores **daily snapshots** in the repo itself, so you can analyze trends for months or years (not limited to 14 days).

## What It Collects

For each configured repository, the workflow captures:

- stars
- forks
- watchers/subscribers
- open issues
- open pull requests
- traffic views (14-day total and unique)
- traffic clones (14-day total and unique)
- top referrer websites (where users came from)
- top popular paths (which pages users visited)
- default branch
- latest push date
- latest release (if any)

## Why This Works Beyond 14 Days

GitHub traffic endpoints are often limited to short windows. This tracker avoids that limitation by storing point-in-time snapshots daily in `data/snapshots/` and appending metrics to `data/metrics_history.csv`.

## Repository Layout

- `.github/workflows/collect-insights.yml`: Scheduled collection job
- `config/repos.json`: List of repositories to track
- `scripts/fetch_insights.py`: Collector and report generator
- `data/snapshots/`: Daily JSON snapshots (single source of truth — contains all metrics, traffic, referrers, paths, and run status)
- `data/metrics_history.csv`: Flat one-row-per-repo-per-day summary for quick manual analysis
- `data/reports/`: Markdown reports

## Setup

1. Update repository list in `config/repos.json`.
2. Create a GitHub repository in your personal account (empty repo is fine).
3. Add remote and push:

```bash
git remote add origin https://github.com/<your-username>/<your-repo>.git
git branch -M main
git push -u origin main
```

4. In GitHub: `Settings -> Actions -> General` and ensure workflow permissions allow read/write for repository contents.

## Running Locally

```bash
export GH_TOKEN=<your_personal_access_token>
python3 scripts/fetch_insights.py --config config/repos.json --out-dir data
```

Use a token with at least `repo` scope for private repos and sufficient access to each tracked repo.

## Triggering Manually

In GitHub Actions, run `Collect OSS Insights` via **Run workflow**.

## Notes

- The snapshot JSON is the single source of truth. It contains all metrics, full 14-day daily traffic, top referrers, top paths, and a run status block.
- `data/metrics_history.csv` is a flat convenience table for quick analysis without parsing JSON.
- If traffic shows as unavailable, check `traffic_error` in the snapshot `run` block or in `metrics_history.csv`.
