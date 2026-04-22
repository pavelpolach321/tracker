#!/usr/bin/env python3
"""Generate an HTML report with all-time charts and referrer summary.

Usage:
    python3 scripts/generate_report.py --data-dir data --output-dir reports/html
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import io
import json
import pathlib
from collections import defaultdict
from typing import Any, Dict, List, Tuple

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
except ImportError:
    raise SystemExit("matplotlib is required. Install it with: pip install matplotlib")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate OSS insights HTML report")
    p.add_argument("--data-dir", default="data", help="Path to the data directory")
    p.add_argument(
        "--output-dir",
        default="reports/html",
        help="Output directory for per-repository HTML reports",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def discover_repos(metrics_dir: pathlib.Path) -> List[str]:
    repos = []
    for owner_dir in sorted(metrics_dir.iterdir()):
        if not owner_dir.is_dir():
            continue
        for csv_file in sorted(owner_dir.glob("*.csv")):
            repos.append(f"{owner_dir.name}/{csv_file.stem}")
    return repos


def load_stars_series(metrics_dir: pathlib.Path, repo: str) -> List[Tuple[dt.date, int]]:
    owner, name = repo.split("/", 1)
    path = metrics_dir / owner / f"{name}.csv"
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                rows.append((dt.date.fromisoformat(row["date"]), int(row["stars"])))
            except (KeyError, ValueError):
                continue
    return sorted(rows)


def load_traffic_series_from_metrics(
    metrics_dir: pathlib.Path, repo: str
) -> List[Tuple[dt.date, int, int, int, int]]:
    owner, name = repo.split("/", 1)
    path = metrics_dir / owner / f"{name}.csv"
    if not path.exists():
        return []

    rows: List[Tuple[dt.date, int, int, int, int]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                d = dt.date.fromisoformat(row["date"])
            except (KeyError, ValueError):
                continue

            def _to_int(v: str) -> int | None:
                v = (v or "").strip()
                if not v:
                    return None
                try:
                    return int(v)
                except ValueError:
                    return None

            views_total = _to_int(row.get("views_14d_total", ""))
            views_unique = _to_int(row.get("views_14d_unique", ""))
            clones_total = _to_int(row.get("clones_14d_total", ""))
            clones_unique = _to_int(row.get("clones_14d_unique", ""))

            if None in (views_total, views_unique, clones_total, clones_unique):
                continue

            rows.append((d, views_total, views_unique, clones_total, clones_unique))

    return sorted(rows)


def load_traffic_series(
    snapshots_dir: pathlib.Path, metrics_dir: pathlib.Path, repo: str
) -> List[Tuple[dt.date, int, int, int, int]]:
    """Return sorted list of (date, views_total, views_unique, clones_total, clones_unique).

    Reads traffic_daily from every snapshot and upserts by date so overlapping
    14-day windows do not double-count — latest snapshot value wins for each date.
    """
    by_date: Dict[dt.date, Tuple[int, int, int, int]] = {}

    for snapshot_file in sorted(snapshots_dir.glob("*.json")):
        try:
            payload = json.loads(snapshot_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for repo_data in payload.get("repositories", []):
            if repo_data.get("repository") != repo:
                continue
            for day in repo_data.get("traffic_daily", []):
                try:
                    d = dt.date.fromisoformat(str(day["date"]))
                    by_date[d] = (
                        int(day.get("views_total", 0)),
                        int(day.get("views_unique", 0)),
                        int(day.get("clones_total", 0)),
                        int(day.get("clones_unique", 0)),
                    )
                except (KeyError, ValueError):
                    continue

    traffic_daily_rows = [(d, *v) for d, v in sorted(by_date.items())]
    if traffic_daily_rows:
        return traffic_daily_rows

    # Fallback: use daily collected 14-day totals from per-repo metrics CSV.
    return load_traffic_series_from_metrics(metrics_dir, repo)


def load_referrers(
    snapshots_dir: pathlib.Path, repo: str
) -> List[Tuple[str, int, int]]:
    """Aggregate referrer counts across all snapshots.

    Since each snapshot covers a sliding 14-day window the values overlap, but
    summing across all capture dates gives a meaningful measure of cumulative
    exposure per source.  Results are sorted by total count descending.
    """
    totals: Dict[str, List[int]] = defaultdict(lambda: [0, 0])

    for snapshot_file in sorted(snapshots_dir.glob("*.json")):
        try:
            payload = json.loads(snapshot_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for repo_data in payload.get("repositories", []):
            if repo_data.get("repository") != repo:
                continue
            for ref in repo_data.get("popular_referrers", []):
                name = str(ref.get("referrer", "")).strip()
                if not name:
                    continue
                totals[name][0] += int(ref.get("count", 0))
                totals[name][1] += int(ref.get("uniques", 0))

    return sorted(
        [(name, v[0], v[1]) for name, v in totals.items()],
        key=lambda x: x[1],
        reverse=True,
    )


# ---------------------------------------------------------------------------
# Chart generation
# ---------------------------------------------------------------------------

CHART_STYLE = {
    "figure.facecolor": "#0d1117",
    "axes.facecolor": "#161b22",
    "axes.edgecolor": "#30363d",
    "axes.labelcolor": "#e6edf3",
    "axes.grid": True,
    "grid.color": "#21262d",
    "grid.linewidth": 0.7,
    "xtick.color": "#8b949e",
    "ytick.color": "#8b949e",
    "text.color": "#e6edf3",
    "lines.linewidth": 2,
    "lines.markersize": 4,
}

PALETTE = ["#58a6ff", "#3fb950", "#f78166", "#d2a8ff", "#ffa657"]


def _fig_to_b64(fig: Any) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def chart_stars(series: List[Tuple[dt.date, int]], repo: str) -> str:
    with plt.rc_context(CHART_STYLE):
        fig, ax = plt.subplots(figsize=(10, 3.5))
        if series:
            dates, values = zip(*series)
            ax.plot(dates, values, color=PALETTE[0], marker="o")
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
            fig.autofmt_xdate(rotation=30)
        ax.set_title(f"{repo} — Stars over time", pad=10)
        ax.set_ylabel("Stars")
        return _fig_to_b64(fig)


def chart_traffic(
    series: List[Tuple[dt.date, int, int, int, int]], repo: str
) -> Tuple[str, str]:
    """Returns (views_chart_b64, clones_chart_b64)."""
    with plt.rc_context(CHART_STYLE):
        # Views
        fig, ax = plt.subplots(figsize=(10, 3.5))
        if series:
            dates = [r[0] for r in series]
            ax.plot(dates, [r[1] for r in series], color=PALETTE[0], marker="o", label="Total views")
            ax.plot(dates, [r[2] for r in series], color=PALETTE[1], marker="o", label="Unique visitors")
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
            fig.autofmt_xdate(rotation=30)
            ax.legend(facecolor="#21262d", edgecolor="#30363d", labelcolor="#e6edf3")
        ax.set_title(f"{repo} — Views over time", pad=10)
        ax.set_ylabel("Views")
        views_b64 = _fig_to_b64(fig)

        # Clones
        fig, ax = plt.subplots(figsize=(10, 3.5))
        if series:
            ax.plot(dates, [r[3] for r in series], color=PALETTE[2], marker="o", label="Total clones")
            ax.plot(dates, [r[4] for r in series], color=PALETTE[3], marker="o", label="Unique clones")
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
            fig.autofmt_xdate(rotation=30)
            ax.legend(facecolor="#21262d", edgecolor="#30363d", labelcolor="#e6edf3")
        ax.set_title(f"{repo} — Clones over time", pad=10)
        ax.set_ylabel("Clones")
        clones_b64 = _fig_to_b64(fig)

    return views_b64, clones_b64


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def _img(b64: str) -> str:
    return f'<img src="data:image/png;base64,{b64}" style="width:100%;border-radius:6px;margin-bottom:16px;">'


def _referrer_table(rows: List[Tuple[str, int, int]]) -> str:
    if not rows:
        return "<p style='color:#8b949e;'>No referrer data available.</p>"
    html = [
        "<table>",
        "<thead><tr><th>Referrer</th><th>Views</th><th>Unique visitors</th></tr></thead>",
        "<tbody>",
    ]
    for name, count, uniques in rows:
        html.append(f"<tr><td>{name}</td><td>{count:,}</td><td>{uniques:,}</td></tr>")
    html.extend(["</tbody>", "</table>"])
    return "\n".join(html)


def render_html(repo_section: Dict[str, Any], generated_at: str) -> str:

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>OSS Insights Report - {repo_section['repo']}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{
    background: #0d1117; color: #e6edf3;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    margin: 0; padding: 32px;
  }}
  h1 {{ color: #58a6ff; border-bottom: 1px solid #21262d; padding-bottom: 12px; }}
  h2 {{ color: #3fb950; margin-top: 48px; border-bottom: 1px solid #21262d; padding-bottom: 8px; }}
  h3 {{ color: #8b949e; font-size: 0.95rem; text-transform: uppercase; letter-spacing: 0.05em; margin-top: 28px; }}
  section {{ max-width: 960px; margin: 0 auto; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 0.9rem; }}
  thead tr {{ background: #161b22; }}
  th {{ text-align: left; padding: 8px 12px; color: #8b949e; border-bottom: 1px solid #30363d; }}
  td {{ padding: 7px 12px; border-bottom: 1px solid #21262d; }}
  tr:hover td {{ background: #161b22; }}
  .meta {{ color: #8b949e; font-size: 0.8rem; margin-top: 48px; border-top: 1px solid #21262d; padding-top: 12px; }}
</style>
</head>
<body>
<section>
    <h1>OSS Insights Report - {repo_section['repo']}</h1>
  <p style="color:#8b949e;">Generated at {generated_at} UTC</p>
</section>
<section>
    <h3>Stars</h3>
    {_img(repo_section['stars_chart'])}
    <h3>Views &amp; Unique visitors</h3>
    {_img(repo_section['views_chart'])}
    <h3>Clones &amp; Unique clones</h3>
    {_img(repo_section['clones_chart'])}
    <h3>Referring sites (all time)</h3>
    {_referrer_table(repo_section['referrers'])}
</section>
<div class="meta">Generated by oss-insights-tracker · Data from <code>data/snapshots/</code> and <code>data/metrics/</code></div>
</body>
</html>"""


def report_output_path(output_dir: pathlib.Path, repo: str) -> pathlib.Path:
        owner, name = repo.split("/", 1)
        owner_dir = output_dir / owner
        owner_dir.mkdir(parents=True, exist_ok=True)
        return owner_dir / f"{name}.html"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()
    data_dir = pathlib.Path(args.data_dir)
    output_dir = pathlib.Path(args.output_dir)

    metrics_dir = data_dir / "metrics"
    snapshots_dir = data_dir / "snapshots"

    if not metrics_dir.exists():
        raise SystemExit(f"Metrics directory not found: {metrics_dir}")
    if not snapshots_dir.exists():
        raise SystemExit(f"Snapshots directory not found: {snapshots_dir}")

    repos = discover_repos(metrics_dir)
    if not repos:
        raise SystemExit("No repository data found in metrics directory.")

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Found {len(repos)} repository/repositories: {', '.join(repos)}")

    written_files: List[pathlib.Path] = []
    generated_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")

    for repo in repos:
        print(f"  Processing {repo} …")
        stars_series = load_stars_series(metrics_dir, repo)
        traffic_series = load_traffic_series(snapshots_dir, metrics_dir, repo)
        referrers = load_referrers(snapshots_dir, repo)

        stars_chart = chart_stars(stars_series, repo)
        views_chart, clones_chart = chart_traffic(traffic_series, repo)

        repo_section = {
            "repo": repo,
            "stars_chart": stars_chart,
            "views_chart": views_chart,
            "clones_chart": clones_chart,
            "referrers": referrers,
        }
        html = render_html(repo_section, generated_at)
        output_path = report_output_path(output_dir, repo)
        output_path.write_text(html, encoding="utf-8")
        written_files.append(output_path)

    print("\nWritten report files:")
    for path in written_files:
        print(f"  - {path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
