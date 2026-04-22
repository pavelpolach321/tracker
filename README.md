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
- default branch
- latest push date
- latest release (if any)

## Why This Works Beyond 14 Days

GitHub traffic endpoints are often limited to short windows. This tracker avoids that limitation by storing point-in-time snapshots daily in `data/snapshots/` and appending metrics to `data/metrics_history.csv`.

## Repository Layout

- `.github/workflows/collect-insights.yml`: Scheduled collection job
- `config/repos.json`: List of repositories to track
- `scripts/fetch_insights.py`: Collector and report generator
- `data/snapshots/`: Daily JSON snapshots
- `data/metrics_history.csv`: Long-term history for analytics
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
python3 scripts/fetch_insights.py --config config/repos.json --out-dir data
```

## Triggering Manually

In GitHub Actions, run `Collect OSS Insights` via **Run workflow**.

## Notes

- The workflow uses `GITHUB_TOKEN` automatically.
- For larger repository lists, consider using a Personal Access Token to increase API limits.
- Data files are committed by the workflow on each run only when changes are detected.
